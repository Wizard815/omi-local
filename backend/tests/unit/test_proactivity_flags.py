"""proactivity_v2 reads only the Omi flag store (omi-local: no PostHog source)."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from config.proactivity_v2 import ProactivityDenied
from utils import proactivity_flags as flags


@pytest.fixture
def store(monkeypatch):
    now = [100.0]
    resolve = Mock()
    monkeypatch.setattr(flags, '_next_warning_at', 0.0)
    monkeypatch.setattr(flags, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(flags.omi_flags, 'resolve', resolve)
    return SimpleNamespace(now=now, resolve=resolve)


@pytest.mark.parametrize('value', [True, False])
def test_store_answer_is_served(store, value):
    store.resolve.return_value = SimpleNamespace(enabled=value)
    assert flags.enabled('u') is value
    store.resolve.assert_called_once_with('proactivity_v2', 'u')


def test_absent_flag_fails_closed_with_rate_limited_diagnostic(store, caplog):
    store.resolve.return_value = None
    for _ in range(2):
        with pytest.raises(ProactivityDenied, match='flag_unavailable'):
            flags.enabled('private-uid')
    assert [record.message for record in caplog.records] == [
        'proactivity_v2_flag_unavailable error_type=omi_flag_absent'
    ]
    store.now[0] += 60
    with pytest.raises(ProactivityDenied, match='flag_unavailable'):
        flags.enabled('private-uid')
    assert len(caplog.records) == 2


def test_store_error_fails_closed_without_leaking_the_message(store, caplog):
    store.resolve.side_effect = ConnectionError('private-response private-key')
    with pytest.raises(flags.ProactivityFlagUnavailable) as raised:
        flags.enabled('private-uid')
    assert raised.value.error_type == 'ConnectionError'
    assert 'private' not in caplog.text


@pytest.mark.parametrize('value', [True, False])
def test_cohort_resolves_v2_only_for_flagged_users(store, monkeypatch, value):
    monkeypatch.setenv('MENTOR_PIPELINE', 'cohort')
    store.resolve.return_value = SimpleNamespace(enabled=value)
    assert flags.mentor_pipeline('synthetic') == ('v2' if value else None)


@pytest.mark.parametrize('pipeline', ['legacy', 'v2', 'typo', None])
def test_non_cohort_env_denies_without_flag_lookup(store, monkeypatch, pipeline):
    if pipeline is None:
        monkeypatch.delenv('MENTOR_PIPELINE', raising=False)
    else:
        monkeypatch.setenv('MENTOR_PIPELINE', pipeline)
    assert flags.mentor_pipeline('synthetic') is None
    store.resolve.assert_not_called()
