"""Local fully-offline audio service — drop-in replacement for the hosted STT/TTS stack.

Implements the EXACT wire protocol the Omi backend already speaks, so it is wired in
with ZERO backend code changes:

  * WS  /v3/stream?sample_rate=16000      (utils/stt/streaming.py: ParakeetWebSocketSocket)
      - on connect the server MUST first send {"type":"ready"}
      - client streams binary PCM16 mono frames, then sends the text "finalize"
      - server sends JSON messages; ANY dict with a non-empty "text" key is a
        transcript segment: {"text","start","end","speaker","is_user","person_id",
        "type":"utterance"} (start/end are seconds from stream start; is_user/
        person_id have no pydantic default on TranscriptSegment and are required
        even though this bridge never has a real value for either — matching
        /v1/transcribe's segment shape below)
      - server closes cleanly after the final segment
  * POST /v1/transcribe    (multipart file=audio.wav)  -> {"text","segments":[...]}
  * POST /v2/transcribe    (multipart file=audio.wav, diarize=true) -> same + speaker labels
      (utils/stt/pre_recorded.py: parakeet_prerecorded_from_bytes)
  * POST /v2/embedding     (multipart file=audio.wav)  -> {"embedding":[float,...]}
      (utils/stt/speaker_embedding.py greets {url}/v2/embedding)
  * GET  /health           -> {"status":"healthy","ready":true}
  * POST /v1/tts           {"text"} -> audio/mpeg (local Piper TTS)
      (routers/tts.py — pointed here via the TTS_LOCAL_BASE_URL env added to that router)

No cloud calls, ever. ASR runs on the MI50 (gfx906) by default; everything else is CPU.

  ASR            whisper.cpp (HIP/ROCm, gfx906), OMI_ASR_ENGINE=whispercpp (default).
                 A whisper-server process (started by entrypoint-audio.sh) loads the
                 ggml model once on the GPU; this file just POSTs to its /inference
                 endpoint per utterance. Set OMI_ASR_ENGINE=fasterwhisper to fall back
                 to the original CPU path (faster-whisper/CTranslate2, OMI_ASR_MODEL).
  Endpointing    pure-numpy energy VAD with AGC (signal-relative threshold + hysteresis).
                 Robust; no torchscript dependency.
  Diarization    resemblyzer ECAPA embeddings + greedy online speaker clustering.
                 Still CPU (unrelated to the ASR engine choice above). Optional: if
                 OMI_DISABLE_DIA=1 (or the model fails to load) all segments are
                 labelled SPEAKER_00 and /v2/embedding returns 503 — the backend
                 degrades gracefully to a single speaker in both cases.
  TTS            Piper (rhasspy) — OMI_PIPER_VOICE path. Serves mp3 when ffmpeg exists.
"""

import asyncio
import io
import logging
import os
import shutil
import tempfile
import threading
import time
import wave
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import requests
from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

# --------------------------------------------------------------------------- config
OMI_ASR_ENGINE = os.getenv("OMI_ASR_ENGINE", "whispercpp").strip().lower()
WHISPER_CPP_SERVER_URL = os.getenv("WHISPER_CPP_SERVER_URL", "http://127.0.0.1:8081").rstrip("/")
# Free the GPU model after this many idle seconds (0 disables). The next
# /inference call auto-reloads it (whisper-server's own behavior) -- one
# slower first request, in exchange for not holding VRAM/host RAM while idle.
WHISPER_CPP_IDLE_UNLOAD_S = float(os.getenv("OMI_ASR_IDLE_UNLOAD_S", "300"))
# faster-whisper fallback path (OMI_ASR_ENGINE=fasterwhisper) — unused on the GPU path.
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
# "cuda" is correct under ROCm/HIP too -- torch's device string, not a CUDA-specific
# claim. Falls back to cpu at load time if the GPU isn't actually available.
DIA_DEVICE = os.getenv("OMI_DIA_DEVICE", "cuda")
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
        self.dia_device = "cpu"
        self.diarize = DIA_ON
        self.ready = False

    def load(self) -> None:
        if OMI_ASR_ENGINE == "whispercpp":
            log.info("ASR engine: whisper.cpp (HIP/ROCm gfx906) at %s", WHISPER_CPP_SERVER_URL)
            _wait_for_whisper_cpp_server()
        else:
            log.info("Loading whisper %s (compute=%s threads=%d) ...", WHISPER_MODEL, WHISPER_COMPUTE, WHISPER_THREADS)
            from faster_whisper import WhisperModel
            t0 = time.time()
            self.whisper = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE,
                                        compute_type=WHISPER_COMPUTE, cpu_threads=WHISPER_THREADS)
            log.info("whisper ready in %.1fs", time.time() - t0)
        if DIA_ON:
            try:
                import torch
                from resemblyzer import VoiceEncoder
                device = DIA_DEVICE
                if device != "cpu" and not torch.cuda.is_available():
                    log.warning("OMI_DIA_DEVICE=%s requested but torch.cuda.is_available() is False "
                                "(GPU passthrough missing/ROCm not seeing the device) -> falling back to cpu.", device)
                    device = "cpu"
                t0 = time.time()
                self.encoder = VoiceEncoder(device)
                self.dia_device = device
                if device != "cpu":
                    log.info("resemblyzer encoder ready in %.1fs on GPU: %s", time.time() - t0, torch.cuda.get_device_name(0))
                else:
                    log.info("resemblyzer encoder ready in %.1fs on cpu", time.time() - t0)
            except Exception as e:  # noqa: BLE001
                self.encoder = None
                self.diarize = False
                log.warning("speaker embedding unavailable -> single speaker: %s", e)
        self.ready = True
        log.info("all models ready (diarize=%s)", self.diarize)


def _wait_for_whisper_cpp_server(timeout_s: float = 240.0) -> None:
    """Block until whisper-server's own model load (started by entrypoint-audio.sh)
    reports ready. It owns GPU init + ggml load; we just poll its /health."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            r = requests.get(f"{WHISPER_CPP_SERVER_URL}/health", timeout=3)
            if r.status_code == 200:
                log.info("whisper-server ready in %.1fs", time.time() - t0)
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    raise RuntimeError(f"whisper-server at {WHISPER_CPP_SERVER_URL} did not become ready within {timeout_s:.0f}s")


MODELS = Models()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, MODELS.load)
    unload_task = None
    if OMI_ASR_ENGINE == "whispercpp" and WHISPER_CPP_IDLE_UNLOAD_S > 0:
        unload_task = asyncio.create_task(_whisper_cpp_idle_unloader())
        log.info("whisper-server idle-unload armed: %.0fs", WHISPER_CPP_IDLE_UNLOAD_S)
    yield
    if unload_task is not None:
        unload_task.cancel()


# --------------------------------------------------------------------------- idle unload
# whisper-server loads its --model at its own startup, so this starts True; it only
# goes False after our own /unload call below (never touched on the fasterwhisper path).
_whisper_cpp_lock = threading.Lock()
_whisper_cpp_last_used = time.time()
_whisper_cpp_in_flight = 0
_whisper_cpp_loaded = True


def _whisper_cpp_touch(start: bool) -> None:
    global _whisper_cpp_last_used, _whisper_cpp_in_flight
    with _whisper_cpp_lock:
        if start:
            _whisper_cpp_in_flight += 1
        else:
            _whisper_cpp_in_flight = max(0, _whisper_cpp_in_flight - 1)
            _whisper_cpp_last_used = time.time()


async def _whisper_cpp_idle_unloader() -> None:
    """Background loop: unload the ggml model off the GPU after WHISPER_CPP_IDLE_UNLOAD_S
    of no ASR calls. Skips while a request is in flight; the next call after an unload
    just eats whisper-server's own reload time on that one request."""
    global _whisper_cpp_loaded
    check_every = max(10.0, min(30.0, WHISPER_CPP_IDLE_UNLOAD_S / 4))
    while True:
        await asyncio.sleep(check_every)
        with _whisper_cpp_lock:
            idle_for = time.time() - _whisper_cpp_last_used
            in_flight = _whisper_cpp_in_flight
            loaded = _whisper_cpp_loaded
        if not loaded or in_flight > 0 or idle_for < WHISPER_CPP_IDLE_UNLOAD_S:
            continue
        try:
            r = requests.post(f"{WHISPER_CPP_SERVER_URL}/unload", timeout=10)
            if r.status_code == 200:
                _whisper_cpp_loaded = False
                log.info("whisper-server model unloaded after %.0fs idle (GPU/host RAM freed).", idle_for)
            else:
                log.warning("whisper-server /unload returned %s: %s", r.status_code, r.text[:200])
        except Exception as e:  # noqa: BLE001
            log.warning("whisper-server /unload request failed: %s", e)


app = FastAPI(lifespan=lifespan)


# --------------------------------------------------------------------------- inference helpers
def transcribe(audio16: np.ndarray, language: str = "en") -> List[Dict[str, Any]]:
    """Return [{text,start,end}] in seconds for 16k float audio."""
    if len(audio16) < 1600:
        return []
    if OMI_ASR_ENGINE == "whispercpp":
        return _transcribe_whispercpp(audio16, language)
    seg_iter, _info = MODELS.whisper.transcribe(
        audio16, language=language, vad_filter=False,
        beam_size=1, condition_on_previous_text=False)
    out: List[Dict[str, Any]] = []
    for s in seg_iter:
        t = (s.text or "").strip()
        if t:
            out.append({"text": t, "start": round(float(s.start), 3), "end": round(float(s.end), 3)})
    return out


def _transcribe_whispercpp(audio16: np.ndarray, language: str) -> List[Dict[str, Any]]:
    """POST to the persistent whisper-server process (loaded once, on the MI50)
    and translate its verbose_json segments into the same {text,start,end} shape
    the faster-whisper path returns, so callers (WS stream + batch v1/v2) don't
    need to know which engine served the request."""
    global _whisper_cpp_loaded
    wav = wav_bytes(float_to_pcm16(audio16), 16000)
    _whisper_cpp_touch(True)
    try:
        resp = requests.post(
            f"{WHISPER_CPP_SERVER_URL}/inference",
            files={"file": ("audio.wav", wav, "audio/wav")},
            data={"response_format": "verbose_json", "language": language or "auto"},
            # Idle-unload means the first request after a long silence pays
            # whisper-server's own reload time on top of inference — give it room.
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
        _whisper_cpp_loaded = True
    except Exception as e:  # noqa: BLE001
        log.error("whisper-server request failed: %s", e)
        return []
    finally:
        _whisper_cpp_touch(False)
    out: List[Dict[str, Any]] = []
    for seg in data.get("segments", []):
        t = (seg.get("text") or "").strip()
        if t:
            out.append({
                "text": t,
                "start": round(float(seg.get("start", 0.0)), 3),
                "end": round(float(seg.get("end", 0.0)), 3),
            })
    return out


def embed(audio16: np.ndarray) -> Optional[np.ndarray]:
    if MODELS.encoder is None or len(audio16) < EMBED_MIN_S * 16000:
        return None
    try:
        if MODELS.dia_device != "cpu":
            # gfx906 has no bf16 hardware -- fp16 autocast is the real speedup path
            # on this card (see gfx906_runtime_env.sh). "cuda" here is torch's device
            # type string; ROCm's build of torch maps it to HIP, not a typo.
            import torch
            with torch.autocast(device_type="cuda", dtype=torch.float16):
                out = MODELS.encoder.embed_utterance(audio16)
        else:
            out = MODELS.encoder.embed_utterance(audio16)
        return np.asarray(out, dtype=np.float32)
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
            "start": round(a / 16000, 3),
            "end": round(b / 16000, 3),
            "speaker": speaker_label(spk),
            # Required by TranscriptSegment (no default) — matches the REST
            # /v1/transcribe segment shape in utils/stt/streaming.py.
            "is_user": False,
            "person_id": None,
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
