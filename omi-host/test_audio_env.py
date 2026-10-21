#!/usr/bin/env python3
"""audio-env.sh: where the backend sends audio, and its live-STT chain.

entrypoint.sh sources audio-env.sh under `set -uo pipefail`; this runs it the
same way with the environment docker compose would hand the container. Compose
passes every listed-but-unset variable as an EMPTY string, so each case sets
the audio variables to "" unless it means otherwise.

What this pins down:

  * the default stays self-contained: every audio surface -> bundled omi-audio,
    live chain = the local engine only,
  * OMI_AUDIO_URL moves all three surfaces at once, and an explicit per-surface
    URL still wins,
  * cloud STT keys go ahead of the local engine, in upstream's order, and a
    provider without its key is never listed (the backend refuses to start then),
  * OMI_AUDIO_URL=off: no local surfaces, a chain of cloud engines only, sync
    uploads on Modulate when its key exists, and a refusal with no key at all.

Run:  python3 test_audio_env.py     (no pytest required)
"""

import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "audio-env.sh")
COMPOSE_EMPTY = {
    name: ""
    for name in (
        "OMI_AUDIO_URL", "HOSTED_PARAKEET_API_URL", "HOSTED_SPEAKER_EMBEDDING_API_URL", "TTS_LOCAL_BASE_URL",
        "STT_SERVICE_MODELS", "STT_PRERECORDED_MODEL", "MODULATE_API_KEY", "SONIOX_API_KEY", "DEEPGRAM_API_KEY",
    )
}
SURFACES = ("HOSTED_PARAKEET_API_URL", "HOSTED_SPEAKER_EMBEDDING_API_URL", "TTS_LOCAL_BASE_URL")


def run(**env):
    """Source the script like entrypoint.sh does; return (exit code, env after, stderr)."""
    proc = subprocess.run(
        # The script's own status line goes to stdout too; a NUL keeps it apart.
        ["bash", "-c", 'set -uo pipefail; . "$1"; printf "\\0"; env -0', "_", SCRIPT],
        env={"PATH": os.environ["PATH"], **COMPOSE_EMPTY, **env},
        capture_output=True,
        text=True,
    )
    out = dict(item.split("=", 1) for item in proc.stdout.split("\0") if "=" in item)
    return proc.returncode, out, proc.stderr


def test_default_is_the_bundled_omi_audio():
    code, env, _ = run()
    assert code == 0
    assert {k: env[k] for k in SURFACES} == dict.fromkeys(SURFACES, "http://omi-audio:8790"), env
    assert env["STT_SERVICE_MODELS"] == "parakeet"
    assert env["STT_PRERECORDED_MODEL"] == "", "sync uploads keep the backend's default (local first)"


def test_audio_url_moves_every_surface():
    code, env, _ = run(OMI_AUDIO_URL="http://192.168.20.10:8790")
    assert code == 0
    assert {k: env[k] for k in SURFACES} == dict.fromkeys(SURFACES, "http://192.168.20.10:8790"), env
    assert env["STT_SERVICE_MODELS"] == "parakeet"


def test_an_explicit_surface_url_still_wins():
    code, env, _ = run(OMI_AUDIO_URL="http://192.168.20.10:8790", TTS_LOCAL_BASE_URL="http://tts-box:8790")
    assert code == 0
    assert env["HOSTED_PARAKEET_API_URL"] == "http://192.168.20.10:8790"
    assert env["TTS_LOCAL_BASE_URL"] == "http://tts-box:8790"


def test_cloud_keys_go_ahead_of_the_local_engine_in_upstream_order():
    code, env, _ = run(DEEPGRAM_API_KEY="dg", MODULATE_API_KEY="mod", SONIOX_API_KEY="sx")
    assert code == 0
    assert env["STT_SERVICE_MODELS"] == "modulate-velma-2,soniox,dg-nova-3,parakeet", env["STT_SERVICE_MODELS"]


def test_a_provider_without_its_key_is_never_listed():
    code, env, _ = run(DEEPGRAM_API_KEY="dg")
    assert code == 0
    assert env["STT_SERVICE_MODELS"] == "dg-nova-3,parakeet", env["STT_SERVICE_MODELS"]


def test_an_explicit_chain_is_kept():
    code, env, _ = run(STT_SERVICE_MODELS="parakeet,dg-nova-3", DEEPGRAM_API_KEY="dg")
    assert code == 0
    assert env["STT_SERVICE_MODELS"] == "parakeet,dg-nova-3"


def test_off_with_deepgram_serves_live_audio_from_the_cloud_only():
    code, env, err = run(OMI_AUDIO_URL="off", DEEPGRAM_API_KEY="dg")
    assert code == 0, err
    assert all(env[k] == "" for k in SURFACES), env
    assert env["STT_SERVICE_MODELS"] == "dg-nova-3"
    assert "synced recordings can't be transcribed" in err, err


def test_off_with_modulate_also_transcribes_sync_uploads_there():
    code, env, err = run(OMI_AUDIO_URL="off", MODULATE_API_KEY="mod")
    assert code == 0, err
    assert env["STT_SERVICE_MODELS"] == "modulate-velma-2"
    assert env["STT_PRERECORDED_MODEL"] == "modulate-velma-2"
    assert "warning" not in err, err


def test_off_without_any_cloud_key_refuses_to_start():
    code, _, err = run(OMI_AUDIO_URL="off")
    assert code == 1
    assert "needs at least one of" in err, err


def main() -> None:
    tests = [
        test_default_is_the_bundled_omi_audio,
        test_audio_url_moves_every_surface,
        test_an_explicit_surface_url_still_wins,
        test_cloud_keys_go_ahead_of_the_local_engine_in_upstream_order,
        test_a_provider_without_its_key_is_never_listed,
        test_an_explicit_chain_is_kept,
        test_off_with_deepgram_serves_live_audio_from_the_cloud_only,
        test_off_with_modulate_also_transcribes_sync_uploads_there,
        test_off_without_any_cloud_key_refuses_to_start,
    ]
    for t in tests:
        t()
        print(f"PASS: {t.__name__}")
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    main()
