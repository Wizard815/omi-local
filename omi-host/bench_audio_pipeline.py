#!/usr/bin/env python3
"""End-to-end harness + benchmark for the self-hosted audio path (omi-audio).

Runs the real local_audio_service.py in its own process and pushes the
repository's speech fixture (backend/testing/release_fixtures/
transcription-release-probe.wav, LibriSpeech 61-70968-0000) through

  * the live path: WS /v3/stream, 16 kHz PCM16 in small frames, exactly what
    the backend relays (--client backend drives it with the backend's own
    ParakeetWebSocketSocket from utils/stt/streaming.py), and
  * the batch path: POST /v2/transcribe (--client backend uses the backend's
    parakeet_prerecorded_from_bytes from utils/stt/pre_recorded.py),

then checks the transcript and reports latency, real-time factor, CPU and peak
memory of the service process.

ASR engines:
  --engine fake-whispercpp (default) runs the service's real whisper.cpp
      path -- it spawns and owns a "whisper-server" process -- but that
      process is a stand-in served by this file: it answers /inference with the
      fixture's known sentence for a clip that holds one whole utterance and
      with nothing otherwise, after sleeping --fake-rtf x clip seconds. The
      check is therefore about the pipeline (VAD endpointing, framing,
      timestamps, segment count, no lost or duplicated utterances), not the
      model; the numbers are pipeline overhead plus the simulated decode.
  --engine fasterwhisper --model DIR|NAME uses a real CTranslate2 Whisper
      model (e.g. a local tiny/base directory); the transcript check is then a
      word-overlap score against the fixture sentence.

Usage (from the repo root, with backend/.venv from `make setup-backend`):
  backend/.venv/bin/python omi-host/bench_audio_pipeline.py --utterances 3
  backend/.venv/bin/python omi-host/bench_audio_pipeline.py --utterances 120 --gap-s 3 --no-realtime
  backend/.venv/bin/python omi-host/bench_audio_pipeline.py --client backend
Exit status is non-zero when a transcript check fails.
"""

import argparse
import asyncio
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import wave
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FIXTURE_DIR = os.path.join(REPO, "backend", "testing", "release_fixtures")
FIXTURE_WAV = os.path.join(FIXTURE_DIR, "transcription-release-probe.wav")
FIXTURE_MANIFEST = os.path.join(FIXTURE_DIR, "transcription-release-probe.json")
SR = 16000


# --------------------------------------------------------------------------- fake whisper-server
def _run_fake_whisper_server(argv: List[str]) -> None:
    """whisper-server stand-in: same CLI flags and /health + /inference routes."""
    import uvicorn
    from fastapi import FastAPI, File, Form, UploadFile

    p = argparse.ArgumentParser()
    p.add_argument("-m")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8081)
    p.add_argument("-t")
    a = p.parse_args(argv)
    text = os.environ["FAKE_ASR_TEXT"]
    rtf = float(os.environ.get("FAKE_ASR_RTF", "0"))
    min_s = float(os.environ.get("FAKE_ASR_MIN_S", "0"))
    max_s = float(os.environ.get("FAKE_ASR_MAX_S", "1e9"))
    log_path = os.environ.get("FAKE_ASR_LOG")
    # A model load: whisper-server answers nothing until it has the weights.
    time.sleep(float(os.environ.get("FAKE_ASR_LOAD_S", "0")))
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/inference")
    async def inference(file: UploadFile = File(...), response_format: str = Form("json"),
                        language: str = Form("en"), beam_size: str = Form("1"), best_of: str = Form("1")):
        data = await file.read()
        with wave.open(io.BytesIO(data)) as w:
            sr, n = w.getframerate(), w.getnframes()
            pcm = np.frombuffer(w.readframes(n), dtype=np.int16)
        dur = n / sr
        if rtf:
            await asyncio.sleep(dur * rtf)
        hit = sr == SR and min_s <= dur <= max_s
        if log_path:
            with open(log_path, "a") as f:
                f.write(json.dumps({"dur": dur, "sr": sr, "hit": hit,
                                    "rms": float(np.sqrt(np.mean((pcm / 32768.0) ** 2))) if n else 0.0}) + "\n")
        return {"text": text if hit else "",
                "segments": [{"text": " " + text, "start": 0.0, "end": round(dur, 3)}] if hit else []}

    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


# --------------------------------------------------------------------------- helpers
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load_fixture() -> Tuple[np.ndarray, str]:
    with wave.open(FIXTURE_WAV) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1 and w.getsampwidth() == 2
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    with open(FIXTURE_MANIFEST) as f:
        expected = json.load(f)["expected_transcript"]
    return pcm, expected


def build_session(clip: np.ndarray, utterances: int, gap_s: float, lead_s: float) -> Tuple[np.ndarray, List[Tuple[float, float]]]:
    """clip, gap, clip, gap ... with a quiet noise floor (not digital silence)."""
    rng = np.random.default_rng(7)
    parts, placed, t = [], [], 0.0

    def noise(sec: float) -> np.ndarray:
        return (rng.standard_normal(int(SR * sec)) * 20).astype(np.int16)

    parts.append(noise(lead_s))
    t += lead_s
    for _ in range(utterances):
        parts.append(clip)
        placed.append((t, t + len(clip) / SR))
        t += len(clip) / SR
        parts.append(noise(gap_s))
        t += gap_s
    return np.concatenate(parts), placed


def wav_bytes(pcm: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()


def words(s: str) -> List[str]:
    return re.findall(r"[a-z']+", s.lower())


def word_recall(hyp: str, ref: str) -> float:
    h = words(hyp)
    r = words(ref)
    hit = 0
    for w in r:
        if w in h:
            h.remove(w)
            hit += 1
    return hit / max(1, len(r))


class ProcStats:
    """CPU seconds and peak RSS of a process from /proc (no psutil needed)."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._tick = os.sysconf("SC_CLK_TCK")
        self.cpu0 = self.cpu()

    def cpu(self) -> float:
        with open(f"/proc/{self.pid}/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / self._tick

    def status(self, key: str) -> float:
        with open(f"/proc/{self.pid}/status") as f:
            for line in f:
                if line.startswith(key + ":"):
                    return int(line.split()[1]) / 1024.0  # MB
        return 0.0

    def reset_peak(self) -> None:
        # Writing 5 to clear_refs resets VmHWM (Linux >= 4.0).
        try:
            with open(f"/proc/{self.pid}/clear_refs", "w") as f:
                f.write("5")
        except OSError:
            pass
        self.cpu0 = self.cpu()


# --------------------------------------------------------------------------- service
class Service:
    def __init__(self, args: argparse.Namespace, expected: str, clip_s: float, tmp: str) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.asr_log = os.path.join(tmp, "asr.jsonl")
        env = dict(os.environ)
        env.update({
            "PORT": str(self.port),
            "OMI_DISABLE_DIA": "1",
            "OMI_TTS_ENGINE": "piper",
            "OMI_ASR_LANGUAGE": "en",
            "OMI_ASR_IDLE_UNLOAD_S": "0",
        })
        if args.engine == "fake-whispercpp":
            wport = free_port()
            models = os.path.join(tmp, "models")
            os.makedirs(models, exist_ok=True)
            open(os.path.join(models, "ggml-fake.bin"), "wb").close()
            wrapper = os.path.join(tmp, "whisper-server")
            with open(wrapper, "w") as f:
                f.write(f"#!/bin/sh\nexec '{sys.executable}' '{os.path.abspath(__file__)}' --fake-whisper-server \"$@\"\n")
            os.chmod(wrapper, 0o755)
            env.update({
                "OMI_ASR_ENGINE": "whispercpp",
                "WHISPER_CPP_SERVER_BIN": wrapper,
                "WHISPER_CPP_MODELS_DIR": models,
                "WHISPER_MODEL": "fake",
                "WHISPER_CPP_SERVER_URL": f"http://127.0.0.1:{wport}",
                "FAKE_ASR_TEXT": expected,
                "FAKE_ASR_RTF": str(args.fake_rtf),
                "FAKE_ASR_LOAD_S": str(args.fake_load_s),
                # One whole fixture utterance, with VAD's edges either side.
                "FAKE_ASR_MIN_S": str(clip_s * 0.8),
                "FAKE_ASR_MAX_S": str(clip_s + 1.5),
                "FAKE_ASR_LOG": self.asr_log,
            })
        else:
            env.update({
                "OMI_ASR_ENGINE": "fasterwhisper",
                "OMI_ASR_MODEL": args.model,
                "OMI_ASR_COMPUTE": args.compute,
                "OMI_ASR_THREADS": str(args.threads),
            })
        for kv in args.env:
            k, _, v = kv.partition("=")
            env[k] = v
        self.proc = subprocess.Popen([sys.executable, os.path.join(HERE, "local_audio_service.py")], env=env,
                                     stdout=subprocess.DEVNULL if not args.verbose else None,
                                     stderr=subprocess.DEVNULL if not args.verbose else None)
        self.stats: Optional[ProcStats] = None

    def wait_ready(self, timeout: float = 300) -> None:
        import requests
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError("audio service exited during startup (rerun with --verbose)")
            try:
                if requests.get(self.url + "/health", timeout=2).status_code == 200:
                    self.stats = ProcStats(self.proc.pid)
                    return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.2)
        raise RuntimeError("audio service did not become healthy")

    def asr_calls(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.asr_log):
            return []
        with open(self.asr_log) as f:
            return [json.loads(line) for line in f if line.strip()]

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


# --------------------------------------------------------------------------- live path
async def stream_direct(url: str, pcm: np.ndarray, chunk_ms: int, realtime: bool) -> Tuple[List[Tuple[float, Dict[str, Any]]], List[float], float]:
    """Return ([(arrival_wall, segment)], send_wall per chunk, finalize_wall)."""
    import websockets
    step = SR * chunk_ms // 1000
    data = pcm.astype(np.int16).tobytes()
    received: List[Tuple[float, Dict[str, Any]]] = []
    sent_at: List[float] = []
    ws_url = url.replace("http://", "ws://") + "/v3/stream?sample_rate=16000"
    # No client keepalive: in --no-realtime runs the service may lag far behind
    # the sender, and this measures throughput, not liveness.
    async with websockets.connect(ws_url, max_size=10 * 1024 * 1024, ping_interval=None) as ws:
        ready = json.loads(await ws.recv())
        assert ready == {"type": "ready"}, ready

        async def rx() -> None:
            try:
                async for msg in ws:
                    if isinstance(msg, str):
                        seg = json.loads(msg)
                        if seg.get("text"):
                            received.append((time.perf_counter(), seg))
            except websockets.ConnectionClosed:
                pass

        rx_task = asyncio.create_task(rx())
        t0 = time.perf_counter()
        for i, off in enumerate(range(0, len(data), step * 2)):
            if realtime:
                delay = t0 + i * chunk_ms / 1000 - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
            await ws.send(data[off:off + step * 2])
            sent_at.append(time.perf_counter())
        await ws.send("finalize")
        fin = time.perf_counter()
        await asyncio.wait_for(rx_task, timeout=600)
    return received, sent_at, fin


async def stream_via_backend(url: str, pcm: np.ndarray, chunk_ms: int, realtime: bool) -> Tuple[List[Tuple[float, Dict[str, Any]]], List[float], float]:
    """Same, but through the backend's own ParakeetWebSocketSocket client."""
    from utils.stt.streaming import process_audio_parakeet
    os.environ["HOSTED_PARAKEET_API_URL"] = url
    received: List[Tuple[float, Dict[str, Any]]] = []
    sent_at: List[float] = []

    def on_segments(segs: List[Dict[str, Any]]) -> None:
        now = time.perf_counter()
        for s in segs:
            received.append((now, s))

    sock = await process_audio_parakeet(on_segments, "en", SR, 1)
    step = SR * chunk_ms // 1000
    data = pcm.astype(np.int16).tobytes()
    t0 = time.perf_counter()
    for i, off in enumerate(range(0, len(data), step * 2)):
        if realtime:
            delay = t0 + i * chunk_ms / 1000 - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
        else:
            await asyncio.sleep(0)  # let the socket's sender task drain
            while sock._send_queue.qsize() > 200:  # noqa: SLF001 -- stay under its 1000-frame cap
                await asyncio.sleep(0.005)
        assert sock.send(data[off:off + step * 2]), sock.death_reason
        sent_at.append(time.perf_counter())
    sock.finalize()
    fin = time.perf_counter()
    await sock.drain_and_close()
    return received, sent_at, fin


# --------------------------------------------------------------------------- batch path
def batch_direct(url: str, pcm: np.ndarray) -> Tuple[List[Dict[str, Any]], float]:
    import requests
    t0 = time.perf_counter()
    r = requests.post(url + "/v2/transcribe", files={"file": ("rec.wav", wav_bytes(pcm), "audio/wav")},
                      data={"diarize": "true"}, timeout=600)
    r.raise_for_status()
    return r.json()["segments"], time.perf_counter() - t0


def batch_via_backend(url: str, pcm: np.ndarray) -> Tuple[List[Dict[str, Any]], float]:
    os.environ["HOSTED_PARAKEET_API_URL"] = url
    from utils.stt.pre_recorded import parakeet_prerecorded_from_bytes
    t0 = time.perf_counter()
    words_out = parakeet_prerecorded_from_bytes(wav_bytes(pcm), SR, diarize=True)
    dt = time.perf_counter() - t0
    return [{"text": w["text"], "start": w["timestamp"][0], "end": w["timestamp"][1]} for w in words_out], dt


# --------------------------------------------------------------------------- checks
def check_segments(segs: List[Dict[str, Any]], placed: List[Tuple[float, float]], expected: str,
                   engine: str, label: str) -> List[str]:
    errs: List[str] = []
    if engine == "fake-whispercpp":
        if len(segs) != len(placed):
            errs.append(f"{label}: {len(segs)} segments for {len(placed)} utterances")
        for i, (seg, (a, b)) in enumerate(zip(segs, placed)):
            if seg["text"].strip() != expected:
                errs.append(f"{label}[{i}]: text {seg['text']!r}")
            if not (a - 0.35 <= seg["start"] <= a + 0.6):
                errs.append(f"{label}[{i}]: start {seg['start']} vs speech at {a:.2f}")
            if not (b - 1.0 <= seg["end"] <= b + 1.2):
                errs.append(f"{label}[{i}]: end {seg['end']} vs speech end {b:.2f}")
    else:
        text = " ".join(s["text"] for s in segs)
        recall = word_recall(text, " ".join([expected] * len(placed)))
        if recall < 0.8:
            errs.append(f"{label}: word recall {recall:.2f} < 0.80: {text[:200]!r}")
    return errs


def summarize_live(received: List[Tuple[float, Dict[str, Any]]], sent_at: List[float], placed: List[Tuple[float, float]],
                   chunk_ms: int) -> Dict[str, Any]:
    """Latency = segment arrival - wall time the chunk holding the speech end was sent."""
    lat = []
    for (arr, _seg), (_a, b) in zip(received, placed):
        idx = min(len(sent_at) - 1, int(b * 1000 // chunk_ms))
        lat.append(arr - sent_at[idx])
    out: Dict[str, Any] = {"segments": len(received)}
    if lat:
        out.update({
            "first_final_latency_s": round(lat[0], 3),
            "latency_mean_s": round(float(np.mean(lat)), 3),
            "latency_p95_s": round(float(np.percentile(lat, 95)), 3),
            "latency_max_s": round(float(np.max(lat)), 3),
        })
    return out


# --------------------------------------------------------------------------- main
def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--fake-whisper-server":
        _run_fake_whisper_server(sys.argv[2:])
        return 0
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=["fake-whispercpp", "fasterwhisper"], default="fake-whispercpp")
    ap.add_argument("--model", help="fasterwhisper model dir or name (e.g. a local tiny.en directory)")
    ap.add_argument("--compute", default="int8")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--fake-rtf", type=float, default=0.0, help="simulated decode seconds per audio second")
    ap.add_argument("--fake-load-s", type=float, default=0.0,
                    help="simulated model load when whisper-server starts (cold start after idle unload)")
    ap.add_argument("--utterances", type=int, default=3)
    ap.add_argument("--gap-s", type=float, default=2.0, help="silence after each utterance")
    ap.add_argument("--lead-s", type=float, default=0.5)
    ap.add_argument("--chunk-ms", type=int, default=40, help="frame size the backend relays (30 ms flush, 20 ms opus)")
    ap.add_argument("--no-realtime", dest="realtime", action="store_false", help="send as fast as possible")
    ap.add_argument("--client", choices=["direct", "backend"], default="direct")
    ap.add_argument("--skip-batch", action="store_true")
    ap.add_argument("--skip-live", action="store_true")
    ap.add_argument("--env", action="append", default=[], help="extra KEY=VALUE for the service")
    ap.add_argument("--json", help="write the result here")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if args.engine == "fasterwhisper" and not args.model:
        ap.error("--engine fasterwhisper needs --model")
    if args.client == "backend":
        sys.path.insert(0, os.path.join(REPO, "backend"))
        os.environ.setdefault("ENCRYPTION_SECRET", "omi-local-audio-bench-not-a-secret-0000")

    clip, expected = load_fixture()
    clip_s = len(clip) / SR
    pcm, placed = build_session(clip, args.utterances, args.gap_s, args.lead_s)
    audio_s = len(pcm) / SR
    result: Dict[str, Any] = {"engine": args.engine, "client": args.client, "audio_s": round(audio_s, 2),
                              "utterances": args.utterances, "chunk_ms": args.chunk_ms, "realtime": args.realtime}
    errors: List[str] = []
    with tempfile.TemporaryDirectory(prefix="omi-audio-bench-") as tmp:
        svc = Service(args, expected, clip_s, tmp)
        try:
            t0 = time.perf_counter()
            svc.wait_ready()
            result["startup_s"] = round(time.perf_counter() - t0, 2)
            assert svc.stats is not None
            if not args.skip_live:
                svc.stats.reset_peak()
                rss0 = svc.stats.status("VmRSS")
                t0 = time.perf_counter()
                fn = stream_via_backend if args.client == "backend" else stream_direct
                received, sent_at, fin = asyncio.run(fn(svc.url, pcm, args.chunk_ms, args.realtime))
                wall = time.perf_counter() - t0
                segs = [s for _t, s in received]
                errors += check_segments(segs, placed, expected, args.engine, "live")
                live = summarize_live(received, sent_at, placed, args.chunk_ms)
                live.update({
                    "wall_s": round(wall, 2),
                    "send_wall_s": round(sent_at[-1] - (sent_at[0] if sent_at else 0), 2),
                    "drain_after_finalize_s": round(time.perf_counter() - fin, 2),
                    "rtf": round(wall / audio_s, 4),
                    "service_cpu_s": round(svc.stats.cpu() - svc.stats.cpu0, 2),
                    "service_rss_start_mb": round(rss0, 1),
                    "service_rss_peak_mb": round(svc.stats.status("VmHWM"), 1),
                })
                calls = svc.asr_calls()
                if calls:
                    live["asr_calls"] = len(calls)
                    live["asr_audio_s"] = round(sum(c["dur"] for c in calls), 2)
                result["live"] = live
                result["live_text"] = [s["text"] for s in segs][:3]
            if not args.skip_batch:
                n0 = len(svc.asr_calls())
                svc.stats.reset_peak()
                fn_b = batch_via_backend if args.client == "backend" else batch_direct
                bsegs, dt = fn_b(svc.url, pcm)
                errors += check_segments(bsegs, placed, expected, args.engine, "batch")
                result["batch"] = {
                    "segments": len(bsegs),
                    "wall_s": round(dt, 3),
                    "rtf": round(dt / audio_s, 4),
                    "service_cpu_s": round(svc.stats.cpu() - svc.stats.cpu0, 2),
                    "service_rss_peak_mb": round(svc.stats.status("VmHWM"), 1),
                    "asr_calls": len(svc.asr_calls()) - n0,
                }
        finally:
            svc.stop()
    result["errors"] = errors
    result["ok"] = not errors
    print(json.dumps(result, indent=2))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(result, f, indent=2)
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
