#!/usr/bin/env python3
"""Regression test for the omi-audio whisper.cpp idle lifecycle.

What this pins down:

  * whisper-server is NOT running when the service starts (lazy: started by the
    first ASR call, so the container boots without holding VRAM), and
  * after WHISPER_CPP_IDLE_UNLOAD_S of no ASR calls the idle unloader STOPS the
    process (which is what actually frees the ggml model's VRAM), and
  * the next ASR call starts it again -- the start/stop/repeat cycle.

Before this change the idle path POSTed to whisper-server's `/unload`, a route
whisper.cpp's server does not have (see examples/server/server.cpp: only
/inference, /load and /health), so the model was pinned in VRAM for the life of
the container and every retry logged a 404 warning forever.

Run:  python3 test_audio_idle_unload.py     (no pytest required)
"""

import asyncio
import os
import sys
import tempfile
import time
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import local_audio_service as las  # noqa: E402

SPAWNED = []


class _FakePopen:
    """Stands in for subprocess.Popen; records lifetime, never really spawns."""

    def __init__(self, args, **kwargs):
        self.args = list(args)
        self.terminated = False
        self.killed = False
        self._alive = True
        SPAWNED.append(self)

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def kill(self):
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        return 0


class _FakeResp:
    status_code = 200


def _fake_get(url, timeout=None):
    return _FakeResp()


def _point_at_a_real_model_file():
    """WhisperCppServer.model_path must resolve to an existing file, else
    ensure_running() rightly refuses to start."""
    tmp = tempfile.mkdtemp(prefix="whisper-idle-test-")
    model = os.path.join(tmp, f"ggml-{_MODEL_NAME}.bin")
    with open(model, "wb"):
        pass
    las.WHISPER_CPP_MODELS_DIR = tmp
    las.WHISPER_CPP_MODEL_NAME = _MODEL_NAME
    las.WHISPER_CPP_SERVER_BIN = "/bin/true"
    las.WHISPER_CPP_START_TIMEOUT_S = 5.0
    return model


_MODEL_NAME = "unit-test"


def _reset_state():
    del SPAWNED[:]
    las.WHISPER_CPP_SERVER.stop()
    las._whisper_cpp_loaded = False
    las._whisper_cpp_in_flight = 0
    las._whisper_cpp_last_used = time.time()


def test_server_is_not_running_at_startup():
    """Lazy start: nothing is loaded until an ASR call needs it."""
    _reset_state()
    assert las.WHISPER_CPP_SERVER.is_running() is False, "server must not be up at boot"
    assert SPAWNED == [], "no process may be spawned before the first ASR call"
    assert las._whisper_cpp_loaded is False, "nothing is loaded at boot"


def test_first_use_starts_it_and_reuses_the_same_process():
    _reset_state()
    with mock.patch.object(las.subprocess, "Popen", _FakePopen), \
         mock.patch.object(las.requests, "get", _fake_get):
        las.WHISPER_CPP_SERVER.ensure_running()
        assert len(SPAWNED) == 1, "first ASR call must start exactly one server"
        assert las.WHISPER_CPP_SERVER.is_running() is True

        # A second call must reuse it, not spawn a twin.
        las.WHISPER_CPP_SERVER.ensure_running()
        assert len(SPAWNED) == 1, "already-running server must not be started again"

    # The spawn must carry the model and the configured host/port.
    args = SPAWNED[0].args
    expected_model = os.path.join(las.WHISPER_CPP_MODELS_DIR, f"ggml-{_MODEL_NAME}.bin")
    assert args[0] == "/bin/true", args
    assert "-m" in args and expected_model in args, args
    assert "--host" in args and "--port" in args, args


def test_idle_stop_releases_then_next_use_restarts():
    """The user-visible contract: 5 min with no transcripts -> model released;
    the next transcript starts it again."""
    _reset_state()
    with mock.patch.object(las.subprocess, "Popen", _FakePopen), \
         mock.patch.object(las.requests, "get", _fake_get):
        las.WHISPER_CPP_SERVER.ensure_running()
        assert len(SPAWNED) == 1
        first = SPAWNED[0]

        # Idle release.
        las.WHISPER_CPP_SERVER.stop()
        assert first.terminated is True, "idle release must terminate the server process"
        assert las.WHISPER_CPP_SERVER.is_running() is False

        # Next ASR call: a fresh process comes up.
        las.WHISPER_CPP_SERVER.ensure_running()
        assert len(SPAWNED) == 2, "the next ASR call must start a new server process"
        assert SPAWNED[1] is not first
        assert las.WHISPER_CPP_SERVER.is_running() is True


async def _one_unloader_pass():
    """Run the unloader's loop body exactly once: the first sleep returns, the
    body executes, and the second sleep ends the loop."""
    sleeps = {"n": 0}

    async def _fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] > 1:
            raise asyncio.CancelledError()

    with mock.patch.object(las.asyncio, "sleep", _fake_sleep):
        await las._whisper_cpp_idle_unloader()


def test_idle_unloader_stops_the_server_after_the_threshold():
    _reset_state()
    with mock.patch.object(las.subprocess, "Popen", _FakePopen), \
         mock.patch.object(las.requests, "get", _fake_get):
        las.WHISPER_CPP_SERVER.ensure_running()
        las._whisper_cpp_loaded = True
        # Older than the threshold, so the unloader must act on its first pass.
        las.WHISPER_CPP_IDLE_UNLOAD_S = 300.0
        las._whisper_cpp_last_used = time.time() - 1000

        try:
            asyncio.run(_one_unloader_pass())
        except asyncio.CancelledError:
            pass

        assert SPAWNED[0].terminated is True, "idle unloader must stop the server"
        assert las._whisper_cpp_loaded is False, "loaded flag must clear after release"
        assert las.WHISPER_CPP_SERVER.is_running() is False


def test_idle_unloader_leaves_a_busy_server_alone():
    """in_flight > 0 means a transcription is mid-flight: never stop under it."""
    _reset_state()
    with mock.patch.object(las.subprocess, "Popen", _FakePopen), \
         mock.patch.object(las.requests, "get", _fake_get):
        las.WHISPER_CPP_SERVER.ensure_running()
        las._whisper_cpp_loaded = True
        las._whisper_cpp_last_used = time.time() - 1000
        las._whisper_cpp_in_flight = 1  # busy

        try:
            asyncio.run(_one_unloader_pass())
        except asyncio.CancelledError:
            pass

        assert SPAWNED[0].terminated is False, "must not stop a server with work in flight"
        assert las.WHISPER_CPP_SERVER.is_running() is True


def test_unloader_stops_orphaned_server_after_a_failed_request():
    """The 4GB-for-12-hours case: ensure_running() started the process, then the
    inference failed, so _whisper_cpp_loaded stayed False. The unloader must
    still stop it -- the running process is the ground truth, not the flag."""
    _reset_state()
    with mock.patch.object(las.subprocess, "Popen", _FakePopen), \
         mock.patch.object(las.requests, "get", _fake_get):
        las.WHISPER_CPP_SERVER.ensure_running()
        assert las.WHISPER_CPP_SERVER.is_running() is True

        las._whisper_cpp_loaded = False          # exactly what a failed request left behind
        las.WHISPER_CPP_IDLE_UNLOAD_S = 300.0
        las._whisper_cpp_last_used = time.time() - 1000

        try:
            asyncio.run(_one_unloader_pass())
        except asyncio.CancelledError:
            pass

        assert SPAWNED[0].terminated is True, "an orphaned server must still be stopped on idle"
        assert las.WHISPER_CPP_SERVER.is_running() is False


def main() -> None:
    _point_at_a_real_model_file()
    tests = [
        test_server_is_not_running_at_startup,
        test_first_use_starts_it_and_reuses_the_same_process,
        test_idle_stop_releases_then_next_use_restarts,
        test_idle_unloader_stops_the_server_after_the_threshold,
        test_idle_unloader_leaves_a_busy_server_alone,
        test_unloader_stops_orphaned_server_after_a_failed_request,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    main()
