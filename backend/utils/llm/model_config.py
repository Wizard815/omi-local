"""Model/profile configuration for backend LLM feature routing.

This module is the source of truth for feature → (model, provider) routing.
Provider-specific client construction lives in ``providers.py``; callers should
continue to use ``clients.get_llm(feature)``.

Runtime model switching (self-hosting):
  When OPENAI_BASE_URL is set, the Omi backend routes through LiteLLM.
  The active model is resolved lazily at each call — first checking Redis
  (set by the dashboard model picker), then falling back to the OMI_LOCAL_MODEL
  / LOCAL_LLM_MODEL env vars. This means you can switch models via the
  dashboard without restarting the container.
"""

import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Union

from utils.llm.gateway_client import is_auto_lane_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExplicitRouteRef:
    feature: str
    model: str
    provider: str
    options: Dict[str, object]


@dataclass(frozen=True)
class AutoLaneRouteRef:
    feature: str
    lane_id: str


RouteRef = Union[ExplicitRouteRef, AutoLaneRouteRef]

# ---------------------------------------------------------------------------
# Runtime model overrides (self-hosting with LiteLLM).
#
# These are read LAZILY on every call — not at import time. This lets the
# dashboard model picker change models at runtime via Redis, and the next LLM
# call picks up the new model without restarting.
#
# Resolution order for the LOCAL model name:
#   1. Redis key omi:model:primary  /  omi:model:chat_agent  (runtime overrides)
#   2. Env var OMI_LOCAL_MODEL  /  LOCAL_LLM_MODEL  (container defaults)
#   3. Empty string — local mode not active
# ---------------------------------------------------------------------------


def _redis_model(kind: str) -> Optional[str]:
    """Read a runtime model override from Redis, returning None on any error."""
    try:
        from database.redis_db import get_runtime_model
        return get_runtime_model(kind)
    except Exception:
        return None


def _get_local_primary_model() -> str:
    """Resolve the primary local model at call time (Redis → env → empty)."""
    return (_redis_model('primary') or os.getenv('OMI_LOCAL_MODEL', '')).strip()


def _get_local_chat_agent_model() -> str:
    """Resolve the chat-agent local model at call time (Redis → env → empty)."""
    return (_redis_model('chat_agent') or os.getenv('LOCAL_LLM_MODEL', '')).strip()


def _is_local_mode() -> bool:
    """Return True when a local LLM backend (LiteLLM) is configured."""
    return bool(os.getenv('OPENAI_BASE_URL', '').strip() and _get_local_primary_model())


# ---------------------------------------------------------------------------
# Model QoS Profile System
#
# Each profile maps every feature to a (model, provider) tuple.
# The profile is the SINGLE SOURCE OF TRUTH for both model and provider.
# Provider is never inferred from model name — it is declared explicitly.
#
# This means the same model can be hosted by different providers:
#   feature_a: ('gemini-2.5-flash', 'gemini')      → Google direct
#   feature_b: ('gemini-2.5-flash', 'openrouter')   → OpenRouter
#
# Global switch:     MODEL_QOS=premium        (selects entire profile)
#
# Profiles:
#   premium  — maximize cost savings while preserving 80% of max quality
#   max      — 100% quality, best models available, no cost optimization
#   byok     — same models as max (BYOK users pay their own API costs)
# ---------------------------------------------------------------------------

# All QoS profiles deliberately share this two-tier map. Keeping independent
# copies below retains profile selection semantics while preventing a higher
# tier or BYOK route from reintroducing a retired OpenAI text model.
_TWO_TIER_MODEL_PROFILE: Dict[str, Tuple[str, str]] = {
    # OpenAI — default intelligence
    'conv_action_items': ('gpt-5.6-luna', 'openai'),
    'wake_word_adjudication': ('gpt-5.6-luna', 'openai'),
    'conv_structure': ('gpt-5.6-luna', 'openai'),
    'conv_app_result': ('gpt-5.6-luna', 'openai'),
    'daily_summary': ('gpt-5.6-luna', 'openai'),
    'external_structure': ('gpt-5.6-luna', 'openai'),
    'memories': ('gpt-5.6-luna', 'openai'),
    'x_memory_extraction_flex': ('gpt-5.6-luna', 'openai'),
    'learnings': ('gpt-5.6-luna', 'openai'),
    'memory_conflict': ('gpt-5.6-luna', 'openai'),
    'memory_conflict_flex': ('gpt-5.6-luna', 'openai'),
    'knowledge_graph': ('gpt-5.6-luna', 'openai'),
    'memory_l1': ('gpt-5.6-luna', 'openai'),
    'memory_l2': ('gpt-5.6-luna', 'openai'),
    'memory_l2_flex': ('gpt-5.6-luna', 'openai'),
    'chat_responses': ('gpt-5.6-luna', 'openai'),
    'file_chat_vision': ('gpt-5.6-luna', 'openai'),
    'file_chat_documents': ('gpt-5.6-luna', 'openai'),
    'chat_agent': ('gpt-5.6-luna', 'openai'),
    'chat_extraction': ('gpt-5.6-luna', 'openai'),
    'chat_graph': ('gpt-5.6-luna', 'openai'),
    'goals': ('gpt-5.6-luna', 'openai'),
    'goals_advice': ('gpt-5.6-luna', 'openai'),
    'notifications': ('gpt-5.6-luna', 'openai'),
    'proactive_notification': ('gpt-5.6-luna', 'openai'),
    'desktop_proactive_reasoning': ('gpt-5.6-luna', 'openai'),
    'what_matters_now': ('gpt-5.6-luna', 'openai'),
    'openglass': ('gpt-5.6-luna', 'openai'),
    'app_generator': ('gpt-5.6-luna', 'openai'),
    'persona_clone': ('gpt-5.6-luna', 'openai'),
    'persona_chat_premium': ('gpt-5.6-luna', 'openai'),
    # OpenAI — cheapest light/binary work
    'conv_app_select': ('gpt-5-nano', 'openai'),
    'conv_folder': ('gpt-5-nano', 'openai'),
    'conv_discard': ('gpt-5-nano', 'openai'),
    'daily_summary_simple': ('gpt-5-nano', 'openai'),
    'memory_category': ('gpt-5-nano', 'openai'),
    'smart_glasses': ('gpt-5-nano', 'openai'),
    'persona_chat': ('gpt-5-nano', 'openai'),
    'desktop_proactive_extraction': ('gpt-5-nano', 'openai'),
    # Non-OpenAI routes remain intentionally unchanged.
    'session_titles': ('gemini-2.5-flash-lite', 'gemini'),
    'followup': ('gemini-2.5-flash-lite', 'gemini'),
    'onboarding': ('gemini-2.5-flash-lite', 'gemini'),
    'app_integration': ('gemini-2.5-flash-lite', 'gemini'),
    'trends': ('gemini-2.5-flash-lite', 'gemini'),
    'translation': ('gemini-2.5-flash-lite', 'gemini'),
    'screen_frame_judge': ('gemini-2.5-flash-lite', 'gemini'),
    'wrapped_analysis': ('gemini-3-flash-preview', 'openrouter'),
    'web_search': ('sonar-pro', 'perplexity'),
}

MODEL_QOS_PROFILES: Dict[str, Dict[str, Tuple[str, str]]] = {
    profile_name: dict(_TWO_TIER_MODEL_PROFILE) for profile_name in ('premium', 'max', 'byok')
}

# Pinned features — (model, provider) fixed regardless of profile or env override.
_PINNED_FEATURES: Dict[str, Tuple[str, str]] = {
    'fair_use': (os.getenv('FAIR_USE_CLASSIFIER_MODEL', 'gpt-5.6-luna').strip() or 'gpt-5.6-luna', 'openai'),
}

# Resolve active profile once at startup.
_active_profile_name = os.environ.get('MODEL_QOS', 'premium').strip().lower()
if _active_profile_name not in MODEL_QOS_PROFILES:
    logger.warning('MODEL_QOS=%s is not a valid profile, falling back to premium', _active_profile_name)
    _active_profile_name = 'premium'
_active_profile = MODEL_QOS_PROFILES[_active_profile_name]

# BYOK QoS — all BYOK users get routed to 'byok' profile (top-tier all-OpenAI).
# BYOK users pay their own API costs, so we give them maximum quality models.
_byok_profile_name = 'byok'
_byok_profile = MODEL_QOS_PROFILES[_byok_profile_name]

# Features that can't go through get_llm() (non-ChatOpenAI providers).
# chat_agent is OpenAI/Luna via get_llm(); the Anthropic Messages path is not a chat lane.
_ANTHROPIC_ONLY_FEATURES: set[str] = set()
_PERPLEXITY_ONLY_FEATURES = {'web_search'}

# Feature-specific client config (temperature, headers — orthogonal to model choice).
# Only applied when a feature resolves to an OpenRouter model.
_OPENROUTER_TEMPERATURES: Dict[str, float] = {
    'wrapped_analysis': 0.7,
}

# Prompt-cache capability detection — prefixed-based detection so model family
# additions don't silently break caching.
_CACHE_KEY_MODEL_PREFIXES = ('gpt-5', 'gpt-4o', 'o1', 'o3', 'o4')
_CACHE_RETENTION_MODEL_PREFIXES = ('gpt-5', 'o1', 'o3', 'o4')

# Features that call .with_structured_output() — logged when resolving to Gemini for compat monitoring.
_STRUCTURED_OUTPUT_FEATURES = {
    'chat_extraction',
    'proactive_notification',
    'desktop_proactive_extraction',
    'desktop_proactive_reasoning',
    'conv_app_select',
    'external_structure',
    'trends',
    'what_matters_now',
    'translation',
    'screen_frame_judge',
}
STRUCTURED_OUTPUT_FEATURES = _STRUCTURED_OUTPUT_FEATURES

# Features whose prompt summarizes a whole conversation (or a whole day) inside a request a
# user is waiting on. They cannot answer inside the shared gateway transport deadline (15s to
# first response byte, DEFAULT_GATEWAY_FIRST_BYTE_TIMEOUT_SECONDS), which is sized for
# background feature calls, and a first-byte timeout there loses the user's summary outright.
#
# The deadline is declared per feature rather than per call site because the call-site version
# of this rule failed three times: `daily_summary` on POST /test-prompt and `conv_structure` on
# conversation finalization both died at ~15.2-15.8s in prod on 2026-08-19, and `conv_app_result`
# — the app/template summary — was still on the background deadline on 2026-09-04, failing 128 of
# 412 app-selected POST /v1/conversations/{id}/reprocess calls (31%) with
# `httpcore.ReadTimeout` -> `Error executing app: Request timed out.` A new call site for one of
# these features now inherits the deadline instead of having to remember it.
# Env-overridable (default unchanged) for self-hosted local LLM backends where
# a cold model load (e.g. llama-swap loading a model for the first request
# since idle-unload) can plausibly take longer than 60s — raising this only
# affects deployments that explicitly set FOREGROUND_REQUEST_TIMEOUT_SECONDS.
FOREGROUND_REQUEST_TIMEOUT_SECONDS = float(os.environ.get('FOREGROUND_REQUEST_TIMEOUT_SECONDS') or 60.0)
_FOREGROUND_TIMEOUT_FEATURES = frozenset(
    {
        'conv_structure',
        'conv_app_result',
        'daily_summary',
        # Fifth instance of the class, 2026-09-05: the L1 memory extractor
        # (`get_llm('memory_l1')` behind extract_l1_memory_archive_items_from_text)
        # runs in conversation finalization with strict=True, so every extraction
        # that outlived the 15s background gateway deadline raised
        # APITimeoutError and dropped that conversation's whole memory batch —
        # prod pusher 2026-09-01..05: "Error extracting memory L1 archive items:
        # invoke_failed:APITimeoutError" 9-21×/day, no retry. The extractor reads
        # the whole transcript in one structured call, like its siblings above.
        'memory_l1',
    }
)


# Future migration point for features that should call the gateway via an auto
# lane. Keep empty until a ticket explicitly wires and verifies shadow/live
# traffic; existing direct LLM routing never consults this map.
_AUTO_LANE_FEATURES: Dict[str, str] = {}

# All cloud providers that can route through an OpenAI-compatible local server.
# 'anthropic' is included so the chat_agent feature routes through LiteLLM
# instead of going through local_llm_adapter.py — LiteLLM handles the
# Anthropic → OpenAI conversion transparently.
_LOCAL_REWRITABLE = frozenset({"openai", "gemini", "openrouter", "perplexity", "anthropic"})


class UnknownLLMFeature(KeyError):
    """A feature has no explicit map entry. Fail closed; never fall through to luna."""

    def __init__(self, feature: str) -> None:
        self.feature = feature
        super().__init__(f"Unknown LLM feature {feature!r}; explicit entries are required")


def _get_model_config(feature: str) -> Tuple[str, str]:
    """Get the (model, provider) tuple for a feature.

    Resolution order:
      1. Pinned features (never overridden)
      2. Local mode — if OPENAI_BASE_URL is set, rewrite ALL rewritable
         features to use the runtime-selected local model (Redis → env fallback).
      3. Active profile entry

    Unknown features raise UnknownLLMFeature. The local-mode check runs on
    EVERY call, so dashboard model switches take effect immediately without
    restarting.
    """
    if feature in _PINNED_FEATURES:
        return _PINNED_FEATURES[feature]

    try:
        base_model, base_provider = _active_profile[feature]
    except KeyError as exc:
        raise UnknownLLMFeature(feature) from exc

    # --- Runtime local-mode rewrite ---
    # chat_agent gets its own model; everything else uses the primary model.
    if _is_local_mode() and base_provider in _LOCAL_REWRITABLE:
        if feature == 'chat_agent':
            return (_get_local_chat_agent_model(), 'openai')
        return (_get_local_primary_model(), 'openai')

    return base_model, base_provider


def get_model_config(feature: str) -> Tuple[str, str]:
    """Get the (model, provider) tuple for a feature.

    Resolution order: pinned > active profile. Unknown features raise UnknownLLMFeature.
    """
    return _get_model_config(feature)


def get_model(feature: str) -> str:
    """Get the model name for a feature from the active Model QoS profile.

    Resolution order: pinned > active profile. Unknown features raise UnknownLLMFeature.

    Args:
        feature: Feature name (e.g. 'conv_action_items', 'chat_agent').

    Returns:
        Model name string (e.g. 'gpt-5.6-luna', 'claude-sonnet-4-6').
    """
    return _get_model_config(feature)[0]


def get_provider(feature: str) -> str:
    """Get the provider for a feature from the active Model QoS profile.

    Returns:
        Provider string: 'openai', 'gemini', 'openrouter', 'anthropic', 'perplexity'.
    """
    return _get_model_config(feature)[1]


def get_route_options(feature: str, model: str, provider: str) -> Dict[str, object]:
    """Return provider/model construction options for a resolved route."""
    options: Dict[str, object] = {}
    if supports_cache_retention(model):
        options['extra_body'] = {"prompt_cache_retention": "24h"}
    if provider == 'openrouter':
        temperature = _OPENROUTER_TEMPERATURES.get(feature)
        if temperature is not None:
            options['temperature'] = temperature
    if provider == 'gemini' and not is_structured_output_feature(feature):
        options['thinking_budget'] = 0
    return options


def feature_request_timeout(feature: str) -> float | None:
    """Return the request deadline a feature needs, or None to use the client default.

    Only features whose generation cannot finish inside the background gateway transport
    deadline declare one (see _FOREGROUND_TIMEOUT_FEATURES). Callers may still pass an
    explicit request_timeout to get_llm; this is the default when they do not.
    """
    if feature in _FOREGROUND_TIMEOUT_FEATURES:
        return FOREGROUND_REQUEST_TIMEOUT_SECONDS
    return None


def get_route_ref(feature: str) -> RouteRef:
    """Return the typed route reference for a feature without changing legacy routing.

    Existing features resolve to explicit provider/model refs by default. Auto-lane
    refs are opt-in through _AUTO_LANE_FEATURES and are not used by get_model(),
    get_provider(), or get_llm(). Unknown features raise before the auto-lane map
    is consulted, so an unmapped name cannot fail open onto a lane.
    """

    if feature not in get_all_configured_features():
        raise UnknownLLMFeature(feature)

    lane_id = _AUTO_LANE_FEATURES.get(feature)
    if lane_id is not None:
        if not is_auto_lane_id(lane_id):
            raise ValueError(f"Auto lane route for feature '{feature}' must use omi:auto: namespace")
        return AutoLaneRouteRef(feature=feature, lane_id=lane_id)

    model, provider = _get_model_config(feature)
    return ExplicitRouteRef(
        feature=feature,
        model=model,
        provider=provider,
        options=get_route_options(feature, model, provider),
    )


def supports_prompt_cache(model: str) -> bool:
    """Whether a model supports OpenAI prompt-cache routing (prompt_cache_key)."""
    return bool(model) and model.startswith(_CACHE_KEY_MODEL_PREFIXES)


def supports_cache_retention(model: str) -> bool:
    """Whether a model supports 24h OpenAI prompt-cache retention (prompt_cache_retention='24h')."""
    return bool(model) and not model.startswith('gpt-5.6') and model.startswith(_CACHE_RETENTION_MODEL_PREFIXES)


def is_structured_output_feature(feature: str) -> bool:
    return feature in _STRUCTURED_OUTPUT_FEATURES


def is_anthropic_only_feature(feature: str) -> bool:
    return feature in _ANTHROPIC_ONLY_FEATURES


def is_perplexity_only_feature(feature: str) -> bool:
    return feature in _PERPLEXITY_ONLY_FEATURES


def get_active_profile_name() -> str:
    return _active_profile_name


def get_active_profile() -> Dict[str, Tuple[str, str]]:
    return _active_profile


def get_all_configured_features() -> set[str]:
    return set(_active_profile.keys()) | set(_PINNED_FEATURES.keys())


def get_byok_profile() -> Dict[str, Tuple[str, str]]:
    return _byok_profile


def get_byok_profile_name() -> str:
    return _byok_profile_name


def get_openrouter_temperatures() -> Dict[str, float]:
    return _OPENROUTER_TEMPERATURES


def get_pinned_features() -> Dict[str, Tuple[str, str]]:
    return _PINNED_FEATURES


def get_anthropic_only_features() -> set[str]:
    return _ANTHROPIC_ONLY_FEATURES


def get_perplexity_only_features() -> set[str]:
    return _PERPLEXITY_ONLY_FEATURES