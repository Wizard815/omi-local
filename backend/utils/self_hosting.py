"""The one switch for omi-local's self-hosted deployment.

omi-host's entrypoint exports OMI_SELF_HOSTED=1. A self-hosted backend has no
billing plane, no whole-account cutover, no Cloud Tasks queue and, unless the
operator adds keys, no cloud STT providers, so the fork's bypasses for those
key on this switch.

PROVIDER_MODE=offline is not that signal: upstream's own hermetic stacks run
with it and still expect the billing and backfill gates to hold.
"""

import os


def is_self_hosted() -> bool:
    return os.getenv('OMI_SELF_HOSTED', '').strip().lower() in {'1', 'true', 'yes'}
