#!/usr/bin/env python3
"""The live-stream path of omi-audio: VAD framing, session memory, slow ASR.

What this pins down:

  * the VAD analyses every sample whatever the frame size: the spans for a
    stream are the same fed whole, in the backend's 40 ms frames, or in odd
    sizes (it used to drop each frame's tail past the last 30 ms window -- a
    quarter of every 40 ms frame),
  * SessionAudio returns exactly the samples a full-history array would, while
    holding only what an unfinished utterance can still need,
  * a long live stream keeps a bounded buffer (it used to keep, and copy per
    frame, every sample since connect: quadratic CPU, 230 MB per hour),
  * a slow ASR call does not stop the receive loop (it used to: the backend's
    keepalive then dropped the stream with 1011 after ~20-40 s),
  * utterances queued behind a slow call are sent in order, and all of them
    before the server closes after "finalize",
  * with whisper.cpp, a stopped server is started at the speech onset, so a
    cold start overlaps the utterance instead of following it,
  * continuous speech is cut every OMI_VAD_MAX_UTTERANCE_S at the quietest
    recent moment, with no audio lost or repeated between the pieces,
  * the live stream decodes with OMI_ASR_LIVE_BEAM_SIZE / _BEST_OF while
    uploads keep OMI_ASR_BEAM_SIZE / _BEST_OF.

Run:  python3 test_audio_stream_pipeline.py   (no pytest required; needs
fastapi, numpy, httpx, e.g. backend/.venv/bin/python)
"""

import asyncio
import os
import sys
import threading

import numpy as np

os.environ.update({
    "OMI_ASR_ENGINE": "openai",
    "OMI_ASR_URL": "http://127.0.0.1:9/v1",  # never called: transcribe() is replaced below
    "OMI_TTS_ENGINE": "openai",
    "OMI_TTS_URL": "http://127.0.0.1:9/v1",
    "OMI_DISABLE_DIA": "1",
})
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

import local_audio_service as las  # noqa: E402

SR = 16000
FRAME = 640  # 40 ms: the size the backend relays


def _tone(seconds: float, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SR * seconds), dtype=np.float32)


def _session(n: int, speech_s: float = 1.5, gap_s: float = 1.5) -> np.ndarray:
    parts = [_silence(0.5)]
    for _ in range(n):
        parts += [_tone(speech_s), _silence(gap_s)]
    return np.concatenate(parts)


def _frames(audio: np.ndarray, size: int = FRAME):
    pcm = las.float_to_pcm16(audio)
    return [pcm[i:i + size * 2] for i in range(0, len(pcm), size * 2)]


def _spans(audio: np.ndarray, sizes) -> list:
    vad = las.EnergyVAD()
    out, i, k = [], 0, 0
    while i < len(audio):
        n = sizes[k % len(sizes)]
        out += vad.feed(audio[i:i + n])
        i += n
        k += 1
    f = vad.flush()
    return out + ([f] if f else [])


class _FakeASR:
    """Replaces las.transcribe: records each utterance, optionally blocks."""

    def __init__(self) -> None:
        self.calls = []
        self.gate = None  # threading.Event the first call waits on
        self.gate_opened = None  # whether it opened while that call was blocked

    def __call__(self, audio16, language=None, live=False):
        self.calls.append(len(audio16))
        if self.gate is not None and len(self.calls) == 1:
            self.gate_opened = self.gate.wait(timeout=5)
        return [{"text": f"utterance {len(self.calls)}", "start": 0.0, "end": len(audio16) / SR}]


# --------------------------------------------------------------------------- tests
def test_vad_spans_do_not_depend_on_frame_size():
    # Energy only in the last 10 ms of every 40 ms frame: a VAD that skips
    # each frame's tail never hears it.
    audio = _silence(6.0)
    for start in range(13 * FRAME, int(3.0 * SR), FRAME):
        audio[start + 480:start + FRAME] = _tone(10 / 1000)[:160]
    ref = _spans(audio, [len(audio)])
    assert ref, "the bursts are speech for the VAD fed in one piece"
    for sizes in ([FRAME], [320], [480], [1000, 7, 333]):
        got = _spans(audio, sizes)
        assert got == ref, (sizes, got, ref)


def test_session_audio_matches_a_full_history_array():
    rng = np.random.default_rng(3)
    full = np.zeros(0, dtype=np.float32)
    sa = las.SessionAudio(capacity=1000)
    for _ in range(400):
        x = rng.standard_normal(int(rng.integers(0, 900))).astype(np.float32)
        sa.append(x)
        full = np.concatenate([full, x])
        if rng.random() < 0.3:
            sa.discard_before(int(rng.integers(sa.start, sa.end + 1)))
        a = int(rng.integers(sa.start, sa.end + 1))
        b = int(rng.integers(a, sa.end + 1))
        assert np.array_equal(sa.slice(a, b), full[a:b])
        assert sa.end == len(full)
    # Clipped to what is held, never wrapping into stale data.
    assert len(sa.slice(0, sa.start)) == 0
    assert np.array_equal(sa.slice(sa.start - 50, sa.start + 10), full[sa.start:sa.start + 10])


def test_long_stream_holds_a_bounded_buffer():
    held = []

    class Recording(las.SessionAudio):
        def discard_before(self, idx):
            super().discard_before(idx)
            held.append((len(self), len(self._data)))

    asr = _FakeASR()
    saved = (las.SessionAudio, las.transcribe)
    las.SessionAudio, las.transcribe = Recording, asr
    try:
        audio = _session(60)  # ~3 minutes
        with TestClient(las.app) as client:
            with client.websocket_connect("/v3/stream?sample_rate=16000") as ws:
                assert ws.receive_json() == {"type": "ready"}
                for f in _frames(audio):
                    ws.send_bytes(f)
                ws.send_text("finalize")
                got = []
                while True:
                    msg = ws.receive()
                    if msg["type"] == "websocket.close":
                        break
                    got.append(msg)
    finally:
        las.SessionAudio, las.transcribe = saved
    assert len(got) == 60 and len(asr.calls) == 60, (len(got), len(asr.calls))
    max_held = max(h for h, _cap in held)
    max_cap = max(cap for _h, cap in held)
    # One utterance (1.5 s) plus the VAD's silence tail; never the session.
    assert max_held <= int(3.0 * SR), max_held
    assert max_cap <= 64 * SR, max_cap
    assert len(audio) > 150 * SR


def test_slow_asr_does_not_stop_the_receive_loop_and_order_is_kept():
    asr = _FakeASR()
    asr.gate = threading.Event()
    fed = {"n": 0}
    audio = _session(4)
    orig_feed = las.EnergyVAD.feed

    def feed(self, chunk):
        fed["n"] += len(chunk)
        if fed["n"] >= len(audio):
            asr.gate.set()  # every frame reached the VAD while ASR call 1 was blocked
        return orig_feed(self, chunk)

    saved = las.transcribe
    las.transcribe = asr
    las.EnergyVAD.feed = feed
    try:
        with TestClient(las.app) as client:
            with client.websocket_connect("/v3/stream?sample_rate=16000") as ws:
                assert ws.receive_json() == {"type": "ready"}
                for f in _frames(audio):
                    ws.send_bytes(f)
                ws.send_text("finalize")
                texts = []
                while True:
                    msg = ws.receive()
                    if msg["type"] == "websocket.close":
                        break
                    texts.append(msg)
    finally:
        las.transcribe = saved
        las.EnergyVAD.feed = orig_feed
    assert asr.gate_opened, "the receive loop stalled behind the first ASR call"
    import json
    segs = [json.loads(m["text"]) for m in texts]
    assert [s["text"] for s in segs] == [f"utterance {i}" for i in range(1, 5)], segs
    starts = [s["start"] for s in segs]
    assert starts == sorted(starts), starts


def test_whisper_server_is_started_at_speech_onset_not_after_the_utterance():
    events = []
    started = threading.Event()

    class FakeServer:
        running = False

        def is_running(self):
            return self.running

        def ensure_running(self):
            events.append("start")
            self.running = True
            started.set()

        def stop(self):
            self.running = False

    asr = _FakeASR()

    def transcribe(audio16, language=None, live=False):
        started.wait(timeout=2)  # the prewarm runs on another executor thread
        events.append("transcribe")
        return asr(audio16, language)

    saved = (las.OMI_ASR_ENGINE, las.WHISPER_CPP_SERVER, las.transcribe)
    las.OMI_ASR_ENGINE, las.WHISPER_CPP_SERVER, las.transcribe = "whispercpp", FakeServer(), transcribe
    try:
        # Two utterances, each sent before the VAD has closed it.
        audio = _session(2)
        with TestClient(las.app) as client:
            with client.websocket_connect("/v3/stream?sample_rate=16000") as ws:
                assert ws.receive_json() == {"type": "ready"}
                for f in _frames(audio):
                    ws.send_bytes(f)
                ws.send_text("finalize")
                while ws.receive()["type"] != "websocket.close":
                    pass
    finally:
        las.OMI_ASR_ENGINE, las.WHISPER_CPP_SERVER, las.transcribe = saved
    # Started once, before the first transcription; already running for the second.
    assert events == ["start", "transcribe", "transcribe"], events
    assert las._whisper_cpp_in_flight == 0


def test_whisper_server_start_is_polled_finely():
    # The first utterance after an idle unload waits on this poll.
    assert las.WHISPER_CPP_START_POLL_S <= 0.2


def test_parse_wav_reads_in_memory():
    audio = _tone(0.5)
    wav = las.wav_bytes(las.float_to_pcm16(audio), SR)
    got, sr = las.parse_wav(wav)
    assert sr == SR and got.dtype == np.float32 and len(got) == len(audio)
    assert np.max(np.abs(got - audio)) < 1e-3


def _monologue(seconds: float, dip_every_s: float = 0.7, dip_s: float = 0.06) -> np.ndarray:
    """Talking without a pause long enough to end an utterance: short dips between 'words'."""
    parts, t = [_silence(0.5)], 0.0
    while t < seconds:
        parts += [_tone(dip_every_s - dip_s), _tone(dip_s, amp=0.02)]
        t += dip_every_s
    parts.append(_silence(1.5))
    return np.concatenate(parts)


def test_continuous_speech_is_cut_at_quiet_moments_without_losing_audio():
    audio = _monologue(40.0)
    spans = _spans(audio, [FRAME])
    max_len = int(las.VAD_MAX_UT_S * SR)
    assert len(spans) >= 3, spans
    for (a, b), (c, _d) in zip(spans, spans[1:]):
        assert c == b, "pieces of one monologue must meet exactly: no lost or repeated audio"
    for a, b in spans[:-1]:
        assert b - a <= max_len + las.VAD_STEP, (a, b)
        # The cut is inside a dip (a quiet 'between words' moment), not mid-tone.
        assert abs(float(audio[b + las.VAD_STEP // 2])) < 0.05, b
    # First text no longer waits for the speaker to pause: it is due after one max-length piece.
    assert spans[0][1] <= int(0.5 * SR) + max_len + las.VAD_STEP


def test_live_stream_uses_the_live_decode_settings():
    calls = []

    def fake_whispercpp(audio16, language, beam_size=las.WHISPER_CPP_BEAM_SIZE, best_of=las.WHISPER_CPP_BEST_OF,
                        audio_ctx=0):
        calls.append((beam_size, best_of, audio_ctx))
        return []

    saved = (las.OMI_ASR_ENGINE, las._transcribe_whispercpp, las.WHISPER_CPP_LIVE_BEAM_SIZE,
             las.WHISPER_CPP_LIVE_BEST_OF, las.WHISPER_CPP_LIVE_AUDIO_CTX)
    las.OMI_ASR_ENGINE, las._transcribe_whispercpp = "whispercpp", fake_whispercpp
    las.WHISPER_CPP_LIVE_BEAM_SIZE, las.WHISPER_CPP_LIVE_BEST_OF, las.WHISPER_CPP_LIVE_AUDIO_CTX = 1, 1, "auto"
    try:
        las.transcribe(_tone(4.0), "en", live=True)
        las.transcribe(_tone(4.0), "en")
        assert las.live_audio_ctx(40 * SR) == 1500
        las.WHISPER_CPP_LIVE_AUDIO_CTX = "0"
        assert las.live_audio_ctx(4 * SR) == 0
    finally:
        (las.OMI_ASR_ENGINE, las._transcribe_whispercpp, las.WHISPER_CPP_LIVE_BEAM_SIZE,
         las.WHISPER_CPP_LIVE_BEST_OF, las.WHISPER_CPP_LIVE_AUDIO_CTX) = saved
    # Live: greedy and an encoder sized to 4 s (200 frames + margin); uploads keep the full settings.
    assert calls == [(1, 1, 264), (las.WHISPER_CPP_BEAM_SIZE, las.WHISPER_CPP_BEST_OF, 0)]


def main() -> None:
    tests = [
        test_vad_spans_do_not_depend_on_frame_size,
        test_session_audio_matches_a_full_history_array,
        test_long_stream_holds_a_bounded_buffer,
        test_slow_asr_does_not_stop_the_receive_loop_and_order_is_kept,
        test_whisper_server_is_started_at_speech_onset_not_after_the_utterance,
        test_whisper_server_start_is_polled_finely,
        test_parse_wav_reads_in_memory,
        test_continuous_speech_is_cut_at_quiet_moments_without_losing_audio,
        test_live_stream_uses_the_live_decode_settings,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
