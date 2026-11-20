"""Speed settings for a self-hosted (local) LLM. Pure env reads, no provider imports.

A home model server (llama.cpp, LM Studio, Ollama, a reasoning model behind
OpenRouter) can take minutes per call. The cloud deadlines (60s foreground,
120s HTTP) are tuned for a warm managed model and cut those calls off, so a
self-hosted stack derives every LLM deadline from one setting instead:

- ``OMI_LOCAL_LLM_TIMEOUT_SECONDS`` (default 300): the longest one model call
  may take. Foreground and background LLM calls, memory intake and the notes
  agent use it; the HTTP request limit is derived from it.
- ``OMI_LOCAL_LLM_REASONING_EFFORT`` (default unset): when set (``low``,
  ``medium``, ``high``, ``none``…), short structured calls send it as
  ``reasoning_effort`` so a reasoning model does not spend thousands of tokens
  on a three-word answer. Unset sends nothing.
- ``OMI_LOCAL_BACKGROUND_PROCESSING`` (default on): a conversation uploaded from
  segments is stored at once (transcript intact, status ``processing``) and its
  notes, tasks and memories are written in the background, retried with backoff
  up to ``OMI_LOCAL_PROCESSING_MAX_ATTEMPTS`` (default 5) times. ``off`` keeps the
  upload waiting for the model.

Everything here applies only when a local LLM is configured
(``OPENAI_BASE_URL`` plus ``OMI_SELF_HOSTED`` or ``OMI_LOCAL_MODEL``). Cloud
deployments read ``None`` and keep their deadlines unchanged.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

TIMEOUT_ENV = 'OMI_LOCAL_LLM_TIMEOUT_SECONDS'
REASONING_EFFORT_ENV = 'OMI_LOCAL_LLM_REASONING_EFFORT'
DEFAULT_TIMEOUT_SECONDS = 300.0
# Shorter than this is never useful for a local model: a cold load alone can take a minute.
MIN_TIMEOUT_SECONDS = 30.0
# The HTTP limit has to hold a few sequential model calls plus the database work around them.
HTTP_TIMEOUT_MULTIPLIER = 3

_TRUE = {'1', 'true', 'yes', 'on'}
_EFFORTS = {'none', 'minimal', 'low', 'medium', 'high'}


def _env(environ: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def local_llm_configured(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when LLM calls go to a self-hosted OpenAI-compatible server."""
    env = _env(environ)
    if not env.get('OPENAI_BASE_URL', '').strip():
        return False
    return env.get('OMI_SELF_HOSTED', '').strip().lower() in _TRUE or bool(env.get('OMI_LOCAL_MODEL', '').strip())


def local_llm_timeout_seconds(environ: Optional[Mapping[str, str]] = None) -> Optional[float]:
    """The per-call deadline for a local model, or None on cloud deployments."""
    env = _env(environ)
    if not local_llm_configured(env):
        return None
    raw = env.get(TIMEOUT_ENV, '').strip()
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        value = DEFAULT_TIMEOUT_SECONDS
    return max(MIN_TIMEOUT_SECONDS, value)


def local_http_timeout_seconds(environ: Optional[Mapping[str, str]] = None) -> Optional[float]:
    """The HTTP request limit a local model needs, or None on cloud deployments."""
    timeout = local_llm_timeout_seconds(environ)
    return None if timeout is None else timeout * HTTP_TIMEOUT_MULTIPLIER


def local_reasoning_effort(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The reasoning effort to request for short structured calls, or None to send nothing."""
    env = _env(environ)
    if not local_llm_configured(env):
        return None
    value = env.get(REASONING_EFFORT_ENV, '').strip().lower()
    return value if value in _EFFORTS else None


BACKGROUND_ENV = 'OMI_LOCAL_BACKGROUND_PROCESSING'
MAX_ATTEMPTS_ENV = 'OMI_LOCAL_PROCESSING_MAX_ATTEMPTS'
DEFAULT_MAX_ATTEMPTS = 5
# Seconds before attempt n+1 (index n-1); the last value repeats past the end.
RETRY_DELAYS_SECONDS = (60.0, 300.0, 900.0, 3600.0)


def local_background_processing_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Whether uploads return at once and notes are written in the background."""
    env = _env(environ)
    if not local_llm_configured(env):
        return False
    return env.get(BACKGROUND_ENV, 'on').strip().lower() not in {'0', 'false', 'no', 'off'}


def local_processing_max_attempts(environ: Optional[Mapping[str, str]] = None) -> int:
    raw = _env(environ).get(MAX_ATTEMPTS_ENV, '').strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_ATTEMPTS
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS


def local_processing_retry_delay(attempts: int) -> float:
    """Wait after ``attempts`` failed attempts (1-based) before the next one."""
    index = min(max(attempts, 1), len(RETRY_DELAYS_SECONDS)) - 1
    return RETRY_DELAYS_SECONDS[index]


TASK_DEDUP_THRESHOLD_ENV = 'OMI_LOCAL_TASK_DEDUP_THRESHOLD'
# Cosine similarity between a whole transcript and a short task description, as the
# local Chroma store scores it with a small embedding model: a related task scored
# 0.52-0.54, unrelated ones 0.07-0.30 (OMI_LOCAL_TASK_DEDUP_THRESHOLD overrides).
DEFAULT_LOCAL_TASK_DEDUP_THRESHOLD = 0.45


def local_task_dedup_threshold(environ: Optional[Mapping[str, str]] = None) -> Optional[float]:
    """Similarity a task needs to reach the notes prompt as a possible duplicate, or None.

    Only for the local Chroma store; the cloud Pinecone threshold is the caller's own.
    """
    env = _env(environ)
    if env.get('LOCAL_VECTOR_DB', '').strip().lower() != 'chroma':
        return None
    raw = env.get(TASK_DEDUP_THRESHOLD_ENV, '').strip()
    try:
        value = float(raw) if raw else DEFAULT_LOCAL_TASK_DEDUP_THRESHOLD
    except ValueError:
        value = DEFAULT_LOCAL_TASK_DEDUP_THRESHOLD
    return min(1.0, max(0.0, value))
