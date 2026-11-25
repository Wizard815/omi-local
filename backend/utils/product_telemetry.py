"""Shared fail-open server capture seam; no content or raw identity properties.

omi-local: there is no third-party analytics. An event either lands in this
server's own usage store (database/local_usage.py: per-day counts and recent
events, readable by the person and, aggregated, by the server admin) or is
dropped. OMI_LOCAL_USAGE switches the store off (config/local_usage.py).
The write runs on the DB pool, so callers never wait on it, and a failure
never fails the owning operation.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from config.local_usage import retention_days, usage_enabled
from utils.executors import db_executor, submit_with_context
from utils.observability.fallback import record_fallback

logger = logging.getLogger(__name__)
_injected_client: Optional[Any] = None
_context: ContextVar[Mapping[str, Any]] = ContextVar('server_product_telemetry', default={})
SOURCE_PLATFORMS = frozenset({'macos', 'ios', 'android', 'windows', 'web', 'mcp', 'server', 'unknown'})
BUILD_CHANNELS = frozenset(
    {
        'stable',
        'beta',
        'dev',
        'production',
        'testflight',
        'app_store',
        'appstore',
        'debug',
        'release',
        'canary',
        'unknown',
    }
)


def normalize_source_platform(value: Any) -> str:
    value = value.strip().lower() if isinstance(value, str) else ''
    return value if value in SOURCE_PLATFORMS else 'unknown'


@contextmanager
def product_telemetry_context(*, headers: Mapping[str, str]):
    """Request-scoped, non-authoritative client metadata; never trust exclusion headers."""
    token = _context.set(
        {
            'source_platform': normalize_source_platform(headers.get('x-app-platform')),
            'build_channel': headers.get('x-build-channel')
            or headers.get('x-app-build-channel')
            or headers.get('x-release-channel'),
        }
    )
    try:
        yield
    finally:
        _context.reset(token)


def bind_websocket_telemetry_context(headers: Mapping[str, str]) -> None:
    # The async listen dependency and handler share one connection-scoped task.
    _context.set(
        {
            'source_platform': normalize_source_platform(headers.get('x-app-platform')),
            'build_channel': headers.get('x-build-channel')
            or headers.get('x-app-build-channel')
            or headers.get('x-release-channel'),
        }
    )


def server_event_properties(uid: str, properties: Mapping[str, Any]) -> dict[str, Any]:
    """Enrich the emitter's closed dictionary, rejecting raw IDs and content.

    Explicit source metadata wins over request context (e.g. a background
    conversation was captured on a different device than the current viewer).
    """
    forbidden = {
        'uid',
        'user_id',
        'distinct_id',
        'email',
        'ip',
        'user_agent',
        'content',
        'text',
        'query',
        'arguments',
        'result',
        'token',
        'transcript',
        'prompt',
    }
    safe = {
        key: value
        for key, value in properties.items()
        if (key not in forbidden and not key.endswith(('_id', '_ids'))) or key == '$insert_id'
    }
    # Experiment names are code-owned enum dimensions, not person/device IDs.
    # Preserve the existing experiment dashboard contract without admitting IDs
    # from arbitrary future callers.
    experiment = properties.get('experiment_id')
    if isinstance(experiment, str) and experiment in {
        'EXP-001-day3-reengagement',
    }:
        safe['experiment_id'] = experiment
    context = _context.get()
    source = next(
        (
            properties[key]
            for key in ('source_platform', 'client_platform', 'app_platform', 'platform')
            if properties.get(key) is not None
        ),
        context.get('source_platform'),
    )
    safe['source_platform'] = normalize_source_platform(source)
    safe['emitter'] = 'server'
    safe['environment'] = os.getenv('OMI_ENV_STAGE') or os.getenv('ENVIRONMENT') or 'unknown'
    channel = properties.get('build_channel', context.get('build_channel'))
    safe.pop('build_channel', None)
    if isinstance(channel, str) and channel.strip().lower() in BUILD_CHANNELS:
        safe['build_channel'] = channel.strip().lower()
    if type(safe.get('internal')) is not bool:
        safe.pop('internal', None)
    synthetic = uid == 'omi-release-probe' or uid.startswith('cfc_')
    safe['test'] = synthetic or safe.get('test') is True
    return safe


def _fallback(reason: str = 'other') -> None:
    record_fallback(component='other', from_mode='local_usage', to_mode='none', reason=reason, outcome='degraded')


class LocalUsageSink:
    """Writes accepted events to this server's usage store, off the caller's thread."""

    def capture(
        self, *, distinct_id: str, event: str, properties: Mapping[str, Any], timestamp: Optional[datetime] = None
    ) -> bool:
        # Import at call time: this seam is imported by dozens of modules, including
        # ones tests load fresh under import stubs; pulling the Firestore client stack
        # in at import would make every importer load it (and re-register its protos).
        from database import local_usage as local_usage_db

        submit_with_context(
            db_executor,
            local_usage_db.record_event,
            distinct_id,
            event,
            properties,
            at=timestamp or datetime.now(timezone.utc),
            retention_days=retention_days(),
        )
        return True


_local_sink = LocalUsageSink()


def get_usage_sink() -> Optional[Any]:
    if _injected_client is not None:
        return _injected_client
    return _local_sink if usage_enabled() else None


def emit_product_event(
    *,
    uid: str,
    event: str,
    properties: Mapping[str, Any],
    timestamp: Optional[datetime] = None,
) -> bool:
    """Return acceptance; usage-store failures never fail the owning operation."""
    if not uid:
        return False
    try:
        sink = get_usage_sink()
        if sink is None:
            return False
        capture_args: dict[str, Any] = dict(
            distinct_id=uid, event=event, properties=server_event_properties(uid, properties)
        )
        if timestamp is not None:
            capture_args['timestamp'] = timestamp
        if sink.capture(**capture_args) is False:
            _fallback('enqueue_failed')
            return False
        return True
    except Exception as error:
        logger.warning('product telemetry emit failed event=%s error_type=%s', event, type(error).__name__)
        _fallback()
        return False


def set_product_telemetry_client_for_tests(client: Optional[Any]) -> None:
    global _injected_client
    _injected_client = client
