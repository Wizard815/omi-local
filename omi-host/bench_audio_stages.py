#!/usr/bin/env python3
"""Per-stage CPU cost of omi-audio's live and batch paths (no model needed).

Times each stage local_audio_service.py runs per backend frame (40 ms PCM16)
or per utterance/upload, in microseconds of wall time on this machine:

  decode        pcm16_to_float, per frame
  resample      8 kHz -> 16 kHz (pcm8 devices), per frame
  buffer@Nmin   appending a frame to the session audio once the stream is N
                minutes old (the one stage that depends on stream age)
  vad           EnergyVAD.feed, per frame
  utt_encode    float -> PCM16 -> WAV for one 5 s utterance (whisper.cpp and
                OpenAI engines get a WAV per utterance)
  asr_hop       POST of that WAV to a local no-op /inference over loopback
  parse_wav     decoding a 10-minute 16 kHz WAV upload (batch path)

Usage: backend/.venv/bin/python omi-host/bench_audio_stages.py [path/to/local_audio_service.py]
(pass an older copy of the service to compare).
"""

import importlib.util
import io
import json
import os
import socket
import sys
import threading
import time
import wave

import numpy as np

os.environ.update({"OMI_ASR_ENGINE": "openai", "OMI_ASR_URL": "http://127.0.0.1:9/v1", "OMI_DISABLE_DIA": "1"})
HERE = os.path.dirname(os.path.abspath(__file__))
SR = 16000
FRAME = 640


def load(path: str):
    spec = importlib.util.spec_from_file_location("las_bench", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def per_call_us(fn, n: int) -> float:
    t = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t) / n * 1e6


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "local_audio_service.py")
    las = load(path)
    rng = np.random.default_rng(1)
    frame = (rng.standard_normal(FRAME) * 3000).astype(np.int16).tobytes()
    frame8k = frame[:FRAME]
    f32 = las.pcm16_to_float(frame)
    out = {"service": path}

    out["decode_us"] = round(per_call_us(lambda: las.pcm16_to_float(frame), 20000), 2)
    out["resample_8k_us"] = round(per_call_us(lambda: las.resample_linear(las.pcm16_to_float(frame8k), 8000, SR), 20000), 2)

    for mins in (1, 10, 30, 60):
        n_old = SR * 60 * mins
        if hasattr(las, "SessionAudio"):
            sa = las.SessionAudio()
            sa.append(np.zeros(n_old, dtype=np.float32))
            sa.discard_before(sa.end - SR * 3)  # what a live stream holds: the open utterance

            def step(sa=sa):
                sa.append(f32)
                sa.discard_before(sa.end - SR * 3)
            out[f"buffer_at_{mins}min_us"] = round(per_call_us(step, 2000), 2)
        else:
            holder = {"buf": np.zeros(n_old, dtype=np.float32)}

            def step(holder=holder, n_old=n_old):
                holder["buf"] = np.concatenate([holder["buf"][:n_old], f32])
            out[f"buffer_at_{mins}min_us"] = round(per_call_us(step, 20 if mins >= 30 else 100), 2)

    vad = las.EnergyVAD()
    speechy = (np.sin(np.arange(FRAME) / 3) * 0.3).astype(np.float32)
    toggle = {"i": 0}

    def vad_step():
        toggle["i"] += 1
        vad.feed(speechy if (toggle["i"] // 50) % 2 else f32 * 0.001)
    out["vad_us"] = round(per_call_us(vad_step, 20000), 2)

    utt = (rng.standard_normal(SR * 5) * 0.1).astype(np.float32)
    out["utt_encode_5s_us"] = round(per_call_us(lambda: las.wav_bytes(las.float_to_pcm16(utt), SR), 300), 1)

    import requests
    import uvicorn
    from fastapi import FastAPI, File, UploadFile
    app = FastAPI()

    @app.post("/inference")
    async def inference(file: UploadFile = File(...)):
        await file.read()
        return {"segments": []}
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    wav = las.wav_bytes(las.float_to_pcm16(utt), SR)
    url = f"http://127.0.0.1:{port}/inference"
    out["asr_hop_5s_wav_us"] = round(per_call_us(
        lambda: requests.post(url, files={"file": ("a.wav", wav, "audio/wav")}, data={"response_format": "verbose_json"}), 100), 1)
    server.should_exit = True

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((rng.standard_normal(SR * 600) * 3000).astype(np.int16).tobytes())
    upload = buf.getvalue()
    out["parse_wav_10min_us"] = round(per_call_us(lambda: las.parse_wav(upload), 10), 1)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
