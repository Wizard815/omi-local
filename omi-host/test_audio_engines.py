#!/usr/bin/env python3
"""omi-audio as a gateway in front of an OpenAI-compatible engine.

With OMI_ASR_ENGINE=openai the service keeps every endpoint the backend calls
(WS /v3/stream, /v2/transcribe, /v1/tts, /health) and sends each VAD-bounded
utterance to POST {OMI_ASR_URL}/audio/transcriptions. These tests run the real
service against a real HTTP fake of that engine and pin down:

  * the request an engine sees: a 16 kHz WAV, the model, the bearer key and
    the language (none when OMI_ASR_LANGUAGE=auto),
  * verbose_json segments mapped to the {text,start,end} the other engines give,
  * an engine that only answers "json" (gpt-4o-transcribe): one retry, then
    json from then on, the text served as one segment spanning the utterance,
  * an engine failure yields no text instead of breaking the stream,
  * the live stream, batch transcription, TTS and /health end to end.

Run:  python3 test_audio_engines.py     (no pytest required; needs fastapi,
uvicorn, requests, numpy, python-multipart, e.g. backend/.venv/bin/python)
"""

import asyncio
import io
import json
import os
import socket
import sys
import threading
import time
import wave

import numpy as np


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = _free_port()
ENGINE = f"http://127.0.0.1:{PORT}/v1"
os.environ.update({
    "OMI_ASR_ENGINE": "openai",
    "OMI_ASR_URL": ENGINE + "/",
    "OMI_ASR_MODEL": "test-whisper",
    "OMI_ASR_API_KEY": "asr-key",
    "OMI_TTS_ENGINE": "openai",
    "OMI_TTS_URL": ENGINE,
    "OMI_TTS_MODEL": "test-tts",
    "OMI_TTS_VOICE": "test-voice",
    "OMI_TTS_API_KEY": "tts-key",
    "OMI_DISABLE_DIA": "1",
})
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn  # noqa: E402
from fastapi import FastAPI, File, Form, Request, UploadFile  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import local_audio_service as las  # noqa: E402

# --------------------------------------------------------------------------- fake engine
engine = FastAPI()
REQUESTS = []
MODE = {"asr": "verbose"}


def _wav_info(data: bytes):
    with wave.open(io.BytesIO(data)) as w:
        return w.getframerate(), w.getnframes()


@engine.post("/v1/audio/transcriptions")
async def fake_transcriptions(request: Request, file: UploadFile = File(...), model: str = Form(...),
                              response_format: str = Form("json"), language: str = Form(None)):
    REQUESTS.append({
        "path": "transcriptions",
        "auth": request.headers.get("authorization"),
        "model": model,
        "response_format": response_format,
        "language": language,
        "wav": _wav_info(await file.read()),
    })
    if MODE["asr"] == "error":
        return JSONResponse({"error": "engine down"}, status_code=500)
    if MODE["asr"] == "json_only" and response_format != "json":
        return JSONResponse({"error": "response_format verbose_json is not supported"}, status_code=400)
    if response_format == "json":
        return {"text": " hello from json "}
    return {
        "text": "hello there second part",
        "segments": [
            {"text": " hello there ", "start": 0.25, "end": 1.0},
            {"text": "   ", "start": 1.0, "end": 1.1},
            {"text": " second part", "start": 1.1, "end": 1.75},
        ],
    }


@engine.post("/v1/audio/speech")
async def fake_speech(request: Request):
    REQUESTS.append({"path": "speech", "auth": request.headers.get("authorization"),
                     "json": await request.json()})
    return Response(b"ID3-fake-mp3", media_type="audio/mpeg")


def _start_engine() -> None:
    server = uvicorn.Server(uvicorn.Config(engine, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("fake engine did not start")
        time.sleep(0.05)


# --------------------------------------------------------------------------- audio helpers
def _tone(seconds: float, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(16000 * seconds)) / 16000
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(16000 * seconds), dtype=np.float32)


def _reset(mode: str = "verbose") -> None:
    REQUESTS.clear()
    MODE["asr"] = mode
    las._openai_asr_format = las.OPENAI_ASR_RESPONSE_FORMAT
    las.ASR_LANGUAGE = "en"


# --------------------------------------------------------------------------- tests
def test_trailing_slash_in_the_url_is_dropped():
    assert las.OPENAI_ASR_URL == ENGINE, las.OPENAI_ASR_URL


def test_utterance_reaches_the_engine_as_a_16k_wav_with_model_key_and_language():
    _reset()
    clip = _tone(2.0)
    segs = las.transcribe(clip)

    assert len(REQUESTS) == 1, REQUESTS
    req = REQUESTS[0]
    assert req["auth"] == "Bearer asr-key"
    assert req["model"] == "test-whisper"
    assert req["language"] == "en"
    assert req["response_format"] == "verbose_json"
    assert req["wav"] == (16000, len(clip)), req["wav"]
    # Blank segments dropped, text stripped, times kept relative to the clip.
    assert segs == [
        {"text": "hello there", "start": 0.25, "end": 1.0},
        {"text": "second part", "start": 1.1, "end": 1.75},
    ], segs


def test_auto_language_is_left_to_the_engine():
    _reset()
    las.ASR_LANGUAGE = "auto"
    las.transcribe(_tone(1.0))
    assert REQUESTS[0]["language"] is None, REQUESTS[0]


def test_json_only_engine_is_retried_once_then_served_json():
    _reset("json_only")
    clip = _tone(1.5)
    first = las.transcribe(clip)
    second = las.transcribe(clip)

    assert [r["response_format"] for r in REQUESTS] == ["verbose_json", "json", "json"], REQUESTS
    assert first == [{"text": "hello from json", "start": 0.0, "end": 1.5}], first
    assert second == first


def test_engine_failure_yields_no_text():
    _reset("error")
    assert las.transcribe(_tone(1.0)) == []


def test_clips_too_short_to_hold_speech_are_not_sent():
    _reset()
    assert las.transcribe(_tone(0.05)) == []
    assert REQUESTS == []


def test_engine_without_a_url_refuses_to_start():
    saved = las.OPENAI_ASR_URL
    las.OPENAI_ASR_URL = ""
    try:
        las.Models().load()
    except RuntimeError as e:
        assert "OMI_ASR_URL" in str(e)
    else:
        raise AssertionError("load() must refuse OMI_ASR_ENGINE=openai without OMI_ASR_URL")
    finally:
        las.OPENAI_ASR_URL = saved


def test_service_end_to_end(client: TestClient):
    """Health, live stream, batch upload and TTS through the real app."""
    _reset()
    health = client.get("/health").json()
    assert health["ready"] is True, health
    assert health["asr_engine"] == "openai" and health["tts_engine"] == "openai", health
    assert health["asr_url"] == ENGINE and health["asr_model"] == "test-whisper", health

    # Live: speech, then enough silence for VAD to close the utterance.
    _reset()
    audio = np.concatenate([_silence(0.5), _tone(1.5), _silence(1.5)])
    frames = []
    with client.websocket_connect("/v3/stream?sample_rate=16000") as ws:
        assert ws.receive_json() == {"type": "ready"}
        ws.send_bytes(las.float_to_pcm16(audio))
        frames.append(ws.receive_json())
        ws.send_text("finalize")
    utt = frames[0]
    assert utt["type"] == "utterance" and utt["text"] == "hello there second part", utt
    assert utt["speaker"] == "SPEAKER_00", utt
    assert 0.3 <= utt["start"] <= 0.6, utt
    assert [r["path"] for r in REQUESTS] == ["transcriptions"], REQUESTS

    # Batch (sync uploads): segment times become absolute in the recording.
    _reset()
    wav = las.wav_bytes(las.float_to_pcm16(audio), 16000)
    body = client.post("/v2/transcribe", files={"file": ("rec.wav", wav, "audio/wav")},
                       data={"diarize": "false"}).json()
    assert [s["text"] for s in body["segments"]] == ["hello there", "second part"], body
    first = body["segments"][0]
    assert first["start"] >= 0.25 + 0.3 and first["speaker"] == "SPEAKER_00", first
    assert first["detected_language"] == "en", first

    # TTS
    _reset()
    resp = client.post("/v1/tts", json={"text": "good morning"})
    assert resp.status_code == 200 and resp.content == b"ID3-fake-mp3", resp.text
    assert resp.headers["content-type"].startswith("audio/mpeg"), resp.headers
    assert REQUESTS == [{"path": "speech", "auth": "Bearer tts-key", "json": {
        "model": "test-tts", "input": "good morning", "voice": "test-voice", "response_format": "mp3"}}], REQUESTS

    # An engine outage sends no utterance and the stream still ends cleanly.
    _reset("error")
    with client.websocket_connect("/v3/stream?sample_rate=16000") as ws:
        assert ws.receive_json() == {"type": "ready"}
        ws.send_bytes(las.float_to_pcm16(audio))
        ws.send_text("finalize")
        closing = ws.receive()
    assert closing["type"] == "websocket.close", closing
    assert [r["path"] for r in REQUESTS] == ["transcriptions"], REQUESTS


def main() -> None:
    _start_engine()
    tests = [
        test_trailing_slash_in_the_url_is_dropped,
        test_utterance_reaches_the_engine_as_a_16k_wav_with_model_key_and_language,
        test_auto_language_is_left_to_the_engine,
        test_json_only_engine_is_retried_once_then_served_json,
        test_engine_failure_yields_no_text,
        test_clips_too_short_to_hold_speech_are_not_sent,
        test_engine_without_a_url_refuses_to_start,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    with TestClient(las.app) as client:
        test_service_end_to_end(client)
    print("PASS: test_service_end_to_end")
    print(f"\nAll {len(tests) + 1} tests passed.")


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
