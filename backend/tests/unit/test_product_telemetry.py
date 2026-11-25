import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from database import local_usage
from utils import product_telemetry as telemetry


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    telemetry.set_product_telemetry_client_for_tests(None)
    for key in ('OMI_LOCAL_USAGE', 'OMI_SELF_HOSTED', 'OMI_LOCAL_USAGE_RETENTION_DAYS', 'OMI_ENV_STAGE', 'ENVIRONMENT'):
        monkeypatch.delenv(key, raising=False)
    yield
    telemetry.set_product_telemetry_client_for_tests(None)


def test_defaults_and_privacy():
    fake = Mock()
    telemetry.set_product_telemetry_client_for_tests(fake)
    for uid in ('omi-release-probe', 'cfc_synthetic', 'external'):
        assert telemetry.emit_product_event(
            uid=uid,
            event='Example',
            properties={
                'uid': uid,
                'conversation_id': 'private',
                'query': 'private',
                'count': 2,
                'internal': True,
            },
        )
    props = [call.kwargs['properties'] for call in fake.capture.call_args_list]
    assert props[2] == {
        'count': 2,
        'internal': True,
        'source_platform': 'unknown',
        'test': False,
        'emitter': 'server',
        'environment': 'unknown',
    }
    assert all(p['test'] for p in props[:2])


@pytest.mark.parametrize('platform', sorted(telemetry.SOURCE_PLATFORMS))
def test_request_context_and_background_propagation(platform):
    from utils.executors import submit_with_context

    with ThreadPoolExecutor(max_workers=1) as executor:
        with telemetry.product_telemetry_context(
            headers={
                'x-app-platform': platform,
                'x-build-channel': 'testflight',
                'x-internal': 'true',
            }
        ):
            props = submit_with_context(executor, telemetry.server_event_properties, 'u', {}).result()
            assert props['source_platform'] == platform
            assert props['build_channel'] == 'testflight'
            assert 'internal' not in props
            assert props['test'] is False
            assert (
                telemetry.server_event_properties('u', {'source_platform': 'android'})['source_platform'] == 'android'
            )
        assert telemetry.server_event_properties('u', {})['source_platform'] == 'unknown'


def test_concurrent_requests_cannot_leak_context():
    async def request(platform):
        with telemetry.product_telemetry_context(headers={'x-app-platform': platform}):
            await asyncio.sleep(0)
            return telemetry.server_event_properties('u', {})['source_platform']

    async def run():
        return await asyncio.gather(request('ios'), request('web'))

    assert asyncio.run(run()) == ['ios', 'web']


def test_invalid_metadata_is_bounded():
    props = telemetry.server_event_properties(
        'u', {'app_platform': 'Darwin', 'build_channel': 'private@example.com', 'internal': 'true'}
    )
    assert props['source_platform'] == 'unknown'
    assert 'build_channel' not in props
    assert 'internal' not in props


def test_capture_failure_and_queue_rejection_are_not_success():
    fake = Mock()
    telemetry.set_product_telemetry_client_for_tests(fake)
    fake.capture.side_effect = RuntimeError('secret')
    assert not telemetry.emit_product_event(uid='u', event='Example', properties={})
    fake.capture.side_effect = None
    fake.capture.return_value = False
    assert not telemetry.emit_product_event(uid='u', event='Example', properties={})


def test_emitters_route_through_shared_capture():
    from utils import integration_telemetry

    fake = Mock()
    telemetry.set_product_telemetry_client_for_tests(fake)
    integration_telemetry.emit_posthog_event('u', 'Integration Sync Succeeded', {'app_platform': 'ios'})
    integration_telemetry.emit_posthog_event('u', 'Integration Auth Refresh Succeeded', {'source_platform': 'mcp'})
    assert fake.capture.call_args_list[0].kwargs['properties']['source_platform'] == 'ios'
    assert fake.capture.call_args_list[1].kwargs['properties']['source_platform'] == 'mcp'
    assert all(call.kwargs['properties']['emitter'] == 'server' for call in fake.capture.call_args_list)


def test_closed_experiment_names_survive_but_raw_identifiers_do_not():
    assert (
        telemetry.server_event_properties('u', {'experiment_id': 'EXP-001-day3-reengagement'})['experiment_id']
        == 'EXP-001-day3-reengagement'
    )
    assert 'experiment_id' not in telemetry.server_event_properties('u', {'experiment_id': 'private-uid'})


def test_events_go_to_the_local_store_only_when_enabled(monkeypatch):
    submitted = []
    monkeypatch.setattr(
        telemetry, 'submit_with_context', lambda executor, fn, *args, **kwargs: submitted.append((fn, args, kwargs))
    )
    # Not self-hosted and not switched on: dropped, nothing written.
    assert not telemetry.emit_product_event(uid='u', event='Example', properties={})
    assert submitted == []

    monkeypatch.setenv('OMI_SELF_HOSTED', '1')
    monkeypatch.setenv('OMI_LOCAL_USAGE_RETENTION_DAYS', '30')
    at = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)
    assert telemetry.emit_product_event(uid='u', event='Example', properties={'count': 2, 'query': 'x'}, timestamp=at)
    fn, args, kwargs = submitted[0]
    assert fn is local_usage.record_event
    assert args[:2] == ('u', 'Example')
    assert args[2]['count'] == 2 and 'query' not in args[2]
    assert kwargs == {'at': at, 'retention_days': 30}

    monkeypatch.setenv('OMI_LOCAL_USAGE', 'off')
    assert not telemetry.emit_product_event(uid='u', event='Example', properties={})
    assert len(submitted) == 1


def test_no_third_party_analytics_sdk_is_loaded():
    import sys

    assert 'posthog' not in sys.modules
