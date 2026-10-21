"""The one switch for omi-local's self-hosted deployment.

omi-host's entrypoint exports OMI_SELF_HOSTED=1. A self-hosted backend has no
billing plane, no whole-account cutover, no Cloud Tasks queue and, unless the
operator adds keys, no cloud STT providers, so the fork's bypasses for those
key on this switch.

PROVIDER_MODE=offline is not that signal: upstream's own hermetic stacks run
with it and still expect the billing and backfill gates to hold.
"""

import os
from typing import Mapping, Optional


def is_self_hosted(environ: Optional[Mapping[str, str]] = None) -> bool:
    source = os.environ if environ is None else environ
    return source.get('OMI_SELF_HOSTED', '').strip().lower() in {'1', 'true', 'yes'}


def modulate_credentials_available() -> bool:
    """Whether live and pre-recorded STT selection may pick cloud Modulate.

    Hosted deployments always carry MODULATE_API_KEY, so upstream selects Modulate
    on serving policy alone. A self-hosted backend without the key must fall
    through to the local Parakeet that omi-audio serves, the way Soniox already
    requires SONIOX_API_KEY.
    """
    return not is_self_hosted() or bool(os.getenv('MODULATE_API_KEY'))
