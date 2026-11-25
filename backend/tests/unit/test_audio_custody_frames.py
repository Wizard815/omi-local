"""Custody frame-table failure boundaries (framed writes from the pusher were retired 2026-11-25)."""

import hashlib
import json
import struct

import pytest

from utils.other import audio_outbox, audio_outbox_metrics, storage
from utils.other import audio_frame_table
from tests.unit.test_audio_durable_outbox import (
    remote,
    Socket,
    _window_harness,
    _audio_frames,
    pusher,
)


def _sent_receipts(sock):
    return [
        audio_outbox.AudioReceipt(**json.loads(message[4:])['receipt'])
        for message in sock.sent
        if struct.unpack('<I', message[:4])[0] == 203
    ]


def frame(pcm, start, sequence=0, offset=0, gap=False):
    return {
        'sequence': sequence,
        'byte_offset': offset,
        'bytes': len(pcm),
        'samples': len(pcm) // 2,
        'start': start,
        'gap_before': gap,
        'sha256': hashlib.sha256(pcm).hexdigest(),
    }


@pytest.mark.asyncio
async def test_mid_window_death_leaves_tail_held_and_prior_part_released(remote, monkeypatch):
    _window_harness(remote, monkeypatch, chunk_duration=0.01)
    real_commit = pusher.commit_pcm_window
    calls = 0

    def commit(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('injected tail death')
        return real_commit(*args, **kwargs)

    monkeypatch.setattr(pusher, 'commit_pcm_window', commit)
    captured = [b'\x01\x02' * 160, b'\x03\x04' * 160]
    sock = Socket(_audio_frames('conversation', captured))
    await pusher._websocket_util_trigger(sock, 'owner', 16000, audio_timeline=2, audio_custody=1)
    receipts = _sent_receipts(sock)
    assert len(receipts) == 1 and audio_outbox.read_pcm('owner', receipts[0]) == captured[0]
    assert receipts[0].samples == 160 and sock.close_code == 1011
    assert len(remote.list_blobs('raw-live/')) == len(remote.list_blobs('audio-outbox/')) == 1


def test_oversized_frame_manifest_never_issues_an_unreadable_receipt(remote, monkeypatch):
    # The writer must honor the reader bound before issuing a custody receipt.
    monkeypatch.setattr(audio_outbox, 'MAX_MANIFEST_BYTES', 1, raising=False)
    pcm = b'\x01\x02' * 160
    before = audio_outbox_metrics.CUSTODY_COMMITS._value.get()
    with pytest.raises(ValueError, match='frame manifest exceeds reader bound'):
        audio_outbox.commit_pcm_window(
            'owner', pcm, 'conversation', 1000, 16000, 'root', 0, [frame(pcm, 1000)], framed=True
        )
    assert len(remote.list_blobs('raw-live/')) == 1
    assert not remote.list_blobs('audio-outbox/')
    assert audio_outbox_metrics.CUSTODY_COMMITS._value.get() == before


@pytest.mark.parametrize('protection', ['standard', 'enhanced'])
@pytest.mark.parametrize('headroom', [0, 31, 32])
def test_manifest_receipt_bound_includes_actual_encryption_envelope(remote, monkeypatch, protection, headroom):
    pcm = b'\x01\x02' * 160

    def commit():
        return audio_outbox.commit_pcm_window(
            'owner',
            pcm,
            'conversation',
            1000,
            16000,
            'root',
            0,
            [frame(pcm, 1000)],
            framed=True,
            data_protection_level=protection,
        )

    original = commit()
    stored = remote.blob(original.manifest_path).data
    plaintext = storage.encryption.decrypt_audio_file(stored, 'owner') if protection == 'enhanced' else stored
    bound = len(plaintext) + headroom
    monkeypatch.setattr(audio_outbox, 'MAX_MANIFEST_BYTES', bound)
    monkeypatch.setattr(audio_frame_table, 'MAX_MANIFEST_BYTES', bound)
    before = audio_outbox_metrics.CUSTODY_COMMITS._value.get()
    if len(stored) > bound:
        with pytest.raises(ValueError, match='frame manifest exceeds reader bound'):
            commit()
        assert audio_outbox_metrics.CUSTODY_COMMITS._value.get() == before
    else:
        receipt = commit()
        assert audio_frame_table.receipt_frames(remote, 'owner', receipt.to_dict()) == ([frame(pcm, 1000)], 16000)
    assert audio_outbox.read_pcm('owner', original) == pcm
