"""Local fully-offline audio service — drop-in replacement for the hosted STT/TTS stack.

Implements the EXACT wire protocol the Omi backend already speaks, so it is wired in
with ZERO backend code changes:

  * WS  /v3/stream?sample_rate=16000      (utils/stt/streaming.py: ParakeetWebSocketSocket)
      - on connect the server MUST first send {"type":"ready"}
      - client streams binary PCM16 mono frames, then sends the text "finalize"
      - server sends JSON messages; ANY dict with a non-empty "text" key is a
        transcript segment: {"text","start_ms","duration_ms","speaker","type":"utterance"}
      - server closes cleanly after the final segment
  * POST /v1/transcribe    (multipart file=audio.wav)  -> {"text","segments":[...]}
  * POST /v2/transcribe    (multipart file=audio.wav, diarize=true) -> same + speaker labels
      (utils/stt/pre_recorded.py: parakeet_prerecorded_from_bytes)
  * POST /v2/embedding     (multipart file=audio.wav)  -> {"embedding":[float,...]}
      (utils/stt/speaker_embedding.py greets {url}/v2/embedding)
  * GET  /health           -> {"status":"healthy","ready":true}
  * POST /v1/tts           {"text"} -> audio/mpeg (local Piper TTS)
      (routers/tts.py — pointed here via the TTS_LOCAL_BASE_URL env added to that router)

Everything runs on CPU. No cloud calls, ever.

  ASR            faster-whisper (CTranslate2) — default "small" int8 (≈5.4× realtime
                 on 2× Xeon Gold 6152). Set OMI_ASR_MODEL for other sizes.
  Endpointing    pure-numpy energy VAD with AGC (signal-relative threshold + hysteresis).
                 Robust; no torchscript dependency.
  Diarization    resemblyzer ECAPA embeddings + greedy online speaker clustering.
                 Optional: if OMI_DISABLE_DIA=1 (or the model fails to load) all
                 segments are labelled SPEAKER_00 and /v2/embedding returns 503 — the
                 backend degrades gracefully to a single speaker in both cases.
  TTS            Piper (rhasspy) — OMI_PIPER_VOICE path. Serves mp3 when ffmpeg exists.
"""

import asyncio
import io
import logging
import os
import shutil
import tempfile
import time
import wave
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

# --------------------------------------------------------------------------- config
WHISPER_MODEL = os.getenv("OMI_ASR_MODEL", "Systran/faster-whisper-small")
WHISPER_DEVICE = os.getenv("OMI_ASR_DEVICE", "cpu")
WHISPER_COMPUTE = os.getenv("OMI_ASR_COMPUTE", "int8")
WHISPER_THREADS = int(os.getenv("OMI_ASR_THREADS", "8"))
# streaming window: accumulate audio, transcribe a chunk once we have this much NEW
# voiced audio, then keep the tail (overlap) so words aren't split at the boundary.
STREAM_WINDOW_S = float(os.getenv("OMI_STREAM_WINDOW_S", "4.0"))
STREAM_MIN_NEW_S = float(os.getenv("OMI_STREAM_MIN_NEW_S", "2.5"))
# energy VAD
VAD_WIN_MS = int(os.getenv("OMI_VAD_WIN_MS", "30"))
VAD_THRESH_REL = float(os.getenv("OMI_VAD_THRESH_REL", "0.12"))
VAD_MIN_SIL_MS = int(os.getenv("OMI_VAD_MIN_SILENCE_MS", "1000"))
VAD_MIN_SPEECH_MS = int(os.getenv("OMI_VAD_MIN_SPEECH_MS", "300"))
VAD_MAX_UT_S = int(os.getenv("OMI_VAD_MAX_UTTERANCE_S", "20"))
DIA_ON = os.getenv("OMI_DISABLE_DIA", "0") != "1"
SPEAKER_MATCH_THRESHOLD = float(os.getenv("OMI_SPK_MATCH", "0.75"))
EMBED_MIN_S = 0.6
PIPER_VOICE = os.getenv("OMI_PIPER_VOICE", "/data/piper/voices/en_US-lessac-medium.onnx")
PIPER_BIN = os.getenv("OMI_PIPER_BIN", "piper")
PORT = int(os.getenv("PORT", "8790"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("omi-local-audio")


# --------------------------------------------------------------------------- audio utils
def resample_linear(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to or len(x) == 0:
        return x.astype(np.float32, copy=False)
    n_out = max(1, int(round(len(x) * sr_to / sr_from)))
    pos = np.arange(n_out) * (sr_from / sr_to)
    idx = np.clip(pos.astype(np.int64), 0, len(x) - 1)
    return x[idx].astype(np.float32)


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    if len(pcm) % 2:
        pcm = pcm[:-1]
    n = len(pcm) // 2
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def float_to_pcm16(x: np.ndarray) -> bytes:
    xi = np.clip(x * 32767.0, -32768, 32767).astype(np.int16)
    return xi.tobytes()


def wav_bytes(pcm16: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16)
    return buf.getvalue()


def parse_wav(data: bytes) -> Tuple[np.ndarray, int]:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tf.write(data)
        tmp = tf.name
    try:
        import soundfile as sf
        audio, sr = sf.read(tmp, dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio[:, 0]
        return audio.astype(np.float32), int(sr)
    finally:
        os.unlink(tmp)


# --------------------------------------------------------------------------- energy VAD
class EnergyVAD:
    """Stateful endpointer over a running stream. Keeps a per-session rolling peak
    (AGC) so the threshold tracks mic gain; emits completed utterance spans."""

    def __init__(self) -> None:
        self.win = VAD_WIN_MS
        self.n = 0            # samples buffered (16k)
        self.peak = 0.02      # rolling 95th-percentile-ish rms floor
        self.in_speech = False
        self.start = 0
        self.last_voice = -1
        self._rms_window: List[float] = []

    def _thr(self) -> float:
        return max(0.004, self.peak * VAD_THRESH_REL)

    def _update_peak(self, rms: float) -> None:
        # slow attack / fast-ish decay AGC
        if rms > self.peak:
            self.peak = rms
        else:
            self.peak = max(0.02, self.peak * 0.9995)

    def feed(self, chunk: np.ndarray) -> List[Tuple[int, int]]:
        """Append 16k audio; return completed (start,end) spans in absolute samples."""
        out: List[Tuple[int, int]] = []
        base = self.n
        self.n += len(chunk)
        win = VAD_WIN_MS  # ms
        step = int(16000 * win / 1000)
        i = 0
        while i + step <= len(chunk):
            seg = chunk[i:i + step]
            rms = float(np.sqrt(np.mean(seg * seg) + 1e-12))
            self._update_peak(rms)
            t_abs = base + i
            thr = self._thr()
            exit_thr = thr * 0.5
            min_sil_s = int(VAD_MIN_SIL_MS / 1000 * 16000)
            min_speech_s = int(VAD_MIN_SPEECH_MS / 1000 * 16000)
            max_ut_s = VAD_MAX_UT_S * 16000
            if not self.in_speech:
                if rms >= thr:
                    self.in_speech = True
                    self.start = t_abs
                    self.last_voice = t_abs
            else:
                if rms >= exit_thr:
                    self.last_voice = t_abs + step
                if (t_abs - self.last_voice) >= min_sil_s or \
                   (t_abs - self.start) >= max_ut_s:
                    if self.last_voice - self.start >= min_speech_s:
                        out.append((self.start, self.last_voice))
                    self.in_speech = False
            i += step
        return out

    def flush(self) -> Optional[Tuple[int, int]]:
        min_speech_s = int(VAD_MIN_SPEECH_MS / 1000 * 16000)
        if self.in_speech and self.last_voice - self.start >= min_speech_s:
            span = (self.start, self.last_voice)
            self.in_speech = False
            return span
        return None


# --------------------------------------------------------------------------- speaker clustering
class SpeakerTracker:
    def __init__(self) -> None:
        self.centroids: List[np.ndarray] = []
        self.last = 0

    def assign(self, emb: Optional[np.ndarray]) -> int:
        if emb is None:
            return self.last
        e = emb / (np.linalg.norm(emb) + 1e-9)
        if not self.centroids:
            self.centroids.append(e)
            self.last = 0
            return 0
        best, best_sim = -1, -2.0
        for i, c in enumerate(self.centroids):
            sim = float(np.dot(e, c))
            if sim > best_sim:
                best_sim, best = sim, i
        if best_sim >= SPEAKER_MATCH_THRESHOLD:
            n = len(self.centroids)
            self.centroids[best] = (self.centroids[best] * n + e) / (n + 1)
            self.centroids[best] /= (np.linalg.norm(self.centroids[best]) + 1e-9)
            self.last = best
            return best
        self.centroids.append(e)
        self.last = len(self.centroids) - 1
        return self.last


def speaker_label(idx: int) -> str:
    return f"SPEAKER_{idx:02d}"


# --------------------------------------------------------------------------- models
class Models:
    def __init__(self) -> None:
        self.whisper: Any = None
        self.encoder: Any = None
        self.diarize = DIA_ON
        self.ready = False

    def load(self) -> None:
        log.info("Loading whisper %s (compute=%s threads=%d) ...", WHISPER_MODEL, WHISPER_COMPUTE, WHISPER_THREADS)
        from faster_whisper import WhisperModel
        t0 = time.time()
        self.whisper = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE,
                                    compute_type=WHISPER_COMPUTE, cpu_threads=WHISPER_THREADS)
        log.info("whisper ready in %.1fs", time.time() - t0)
        if DIA_ON:
            try:
                from resemblyzer import VoiceEncoder
                t0 = time.time()
                self.encoder = VoiceEncoder("cpu")
                log.info("resemblyzer encoder ready in %.1fs", time.time() - t0)
            except Exception as e:  # noqa: BLE001
                self.encoder = None
                self.diarize = False
                log.warning("speaker embedding unavailable -> single speaker: %s", e)
        self.ready = True
        log.info("all models ready (diarize=%s)", self.diarize)


MODELS = Models()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, MODELS.load)
    yield


app = FastAPI(lifespan=lifespan)


# --------------------------------------------------------------------------- inference helpers
def transcribe(audio16: np.ndarray, language: str = "en") -> List[Dict[str, Any]]:
    """Return [{text,start,end}] in seconds for 16k float audio."""
    if len(audio16) < 1600:
        return []
    seg_iter, _info = MODELS.whisper.transcribe(
        audio16, language=language, vad_filter=False,
        beam_size=1, condition_on_previous_text=False)
    out: List[Dict[str, Any]] = []
    for s in seg_iter:
        t = (s.text or "").strip()
        if t:
            out.append({"text": t, "start": round(float(s.start), 3), "end": round(float(s.end), 3)})
    return out


def embed(audio16: np.ndarray) -> Optional[np.ndarray]:
    if MODELS.encoder is None or len(audio16) < EMBED_MIN_S * 16000:
        return None
    try:
        return np.asarray(MODELS.encoder.embed_utterance(audio16), dtype=np.float32)
    except Exception as e:  # noqa: BLE001
        log.warning("embed failed: %s", e)
        return None


# --------------------------------------------------------------------------- health
@app.get("/health")
async def health():
    return JSONResponse(
        {"status": "healthy" if MODELS.ready else "loading", "ready": MODELS.ready},
        status_code=200 if MODELS.ready else 503)


# --------------------------------------------------------------------------- WS /v3/stream
@app.websocket("/v3/stream")
async def ws_stream(ws: WebSocket, sample_rate: int = 16000):
    await ws.accept()
    if not MODELS.ready:
        await ws.close(code=1013, reason="service_not_ready")
        return
    await ws.send_json({"type": "ready"})

    loop = asyncio.get_running_loop()
    buf = np.zeros(0, dtype=np.float32)   # full 16k session audio
    vad = EnergyVAD()
    tracker = SpeakerTracker()
    emitted_until = 0                     # absolute sample index already transcribed

    async def emit(span: Tuple[int, int]) -> None:
        nonlocal emitted_until
        a, b = span
        a = max(a, emitted_until)
        if b - a < VAD_MIN_SPEECH_MS:
            return
        seg_audio = buf[a:b]
        t0 = time.time()
        segs = await loop.run_in_executor(None, transcribe, seg_audio)
        if not segs:
            emitted_until = max(emitted_until, b)
            return
        text = " ".join(s["text"] for s in segs)
        spk = 0
        if MODELS.diarize:
            emb = await loop.run_in_executor(None, embed, seg_audio)
            spk = tracker.assign(emb)
        log.info("stream %.1f-%.1fs -> %r (%d seg) in %.1fs",
                 a / 16000, b / 16000, text[:40], len(segs), time.time() - t0)
        await ws.send_json({
            "text": text,
            "start_ms": int(a / 16000 * 1000),
            "duration_ms": int((b - a) / 16000 * 1000),
            "speaker": speaker_label(spk),
            "type": "utterance",
        })
        emitted_until = max(emitted_until, b)

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            text = msg.get("text")
            if text is not None:
                if text.strip() == "finalize":
                    span = vad.flush()
                    if span:
                        await emit(span)
                    break
                continue
            if not data:
                continue
            chunk = pcm16_to_float(data)
            if sample_rate != 16000:
                chunk = resample_linear(chunk, sample_rate, 16000)
            buf = np.concatenate([buf, chunk]) if len(chunk) else buf
            for span in vad.feed(chunk):
                await emit(span)
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.error("ws stream error: %s", e)
    finally:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- batch /v1 /v2
async def do_transcribe(file_bytes: bytes, diarize: bool) -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    audio, sr = await loop.run_in_executor(None, parse_wav, file_bytes)
    if len(audio) == 0:
        return {"text": "", "segments": []}
    audio16 = resample_linear(audio, sr, 16000) if sr != 16000 else audio.astype(np.float32)

    vad = EnergyVAD()
    spans: List[Tuple[int, int]] = []
    step = 16000 // 2
    for i in range(0, len(audio16), step):
        spans.extend(vad.feed(audio16[i:i + step]))
    f = vad.flush()
    if f:
        spans.append(f)
    if not spans:
        spans = [(0, len(audio16))]

    tracker = SpeakerTracker()
    segments: List[Dict[str, Any]] = []
    for (a, b) in spans:
        seg_audio = audio16[max(0, a):b]
        if len(seg_audio) < VAD_MIN_SPEECH_MS:
            continue
        segs = await loop.run_in_executor(None, transcribe, seg_audio)
        for s in segs:
            spk = 0
            if diarize:
                sa = seg_audio[int(s["start"] * 16000):int(s["end"] * 16000)]
                spk = tracker.assign(await loop.run_in_executor(None, embed, sa))
            segments.append({
                "text": s["text"],
                "start": round(a / 16000 + s["start"], 3),
                "end": round(a / 16000 + s["end"], 3),
                "speaker": speaker_label(spk) if diarize else "SPEAKER_00",
                "detected_language": "en",
            })
    text = " ".join(s["text"] for s in segments).strip()
    return {"text": text, "segments": segments}


@app.post("/v1/transcribe")
async def transcribe_v1(file: UploadFile = File(...)):
    if not MODELS.ready:
        return JSONResponse({"error": "loading"}, status_code=503)
    return JSONResponse(await do_transcribe(await file.read(), diarize=False))


@app.post("/v2/transcribe")
async def transcribe_v2(file: UploadFile = File(...), diarize: str = Form("true")):
    if not MODELS.ready:
        return JSONResponse({"error": "loading"}, status_code=503)
    return JSONResponse(await do_transcribe(await file.read(), diarize=(diarize.lower() == "true")))


# --------------------------------------------------------------------------- speaker embedding
@app.post("/v2/embedding")
async def embedding(file: UploadFile = File(...)):
    if not MODELS.ready:
        return JSONResponse({"error": "loading"}, status_code=503)
    if MODELS.encoder is None:
        return JSONResponse({"error": "embedding unavailable"}, status_code=503)
    loop = asyncio.get_running_loop()
    data = await file.read()
    audio, sr = await loop.run_in_executor(None, parse_wav, data)
    audio16 = resample_linear(audio, sr, 16000) if sr != 16000 else audio
    emb = await loop.run_in_executor(None, embed, audio16)
    if emb is None:
        return JSONResponse({"error": "could not embed"}, status_code=500)
    return JSONResponse({"embedding": emb.tolist()})


# --------------------------------------------------------------------------- TTS (Piper)
_ffmpeg: Optional[bool] = None


def _has_ffmpeg() -> bool:
    global _ffmpeg
    if _ffmpeg is None:
        _ffmpeg = shutil.which("ffmpeg") is not None
    return _ffmpeg


@app.post("/v1/tts")
async def tts(req: Request):
    if not MODELS.ready:
        return JSONResponse({"error": "loading"}, status_code=503)
    body = await req.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    if not os.path.exists(PIPER_VOICE):
        return JSONResponse({"error": f"piper voice not found: {PIPER_VOICE}"}, status_code=503)
    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        out_wav = os.path.join(td, "out.wav")
        proc = await asyncio.create_subprocess_exec(
            PIPER_BIN, "--model", PIPER_VOICE, "--output_file", out_wav,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        _outp, errp = await proc.communicate(input=text.encode("utf-8"))
        if proc.returncode != 0:
            return JSONResponse({"error": f"piper failed: {errp.decode()[:200]}"}, status_code=500)
        wav = open(out_wav, "rb").read()
    log.info("tts %d chars in %.2fs", len(text), time.time() - t0)
    if _has_ffmpeg():
        p = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", "pipe:0", "-codec:a", "libmp3lame", "-qscale:a", "3", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)
        mp3, _ = await p.communicate(input=wav)
        if p.returncode == 0 and mp3:
            return Response(mp3, media_type="audio/mpeg")
    return Response(wav, media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
