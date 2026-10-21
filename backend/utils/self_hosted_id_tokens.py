"""A self-hosted emulator stack accepts only ID tokens this backend signed.

omi-local runs on the Firebase Auth *emulator*, which issues unsigned tokens,
and firebase_admin skips signature checks whenever FIREBASE_AUTH_EMULATOR_HOST
is set: anyone who could reach the backend could write a token for any uid and
act as that user. On such a stack (OMI_SELF_HOSTED=1 plus the emulator) main.py
installs this guard over firebase_admin.auth.verify_id_token, the function
every token check in the backend calls, so call sites added later are covered
too. The one credential it accepts is the session token /v1/auth/local-login
issues, signed with LOCAL_AUTH_JWT_SECRET (utils/local_auth.py). Anything else
raises InvalidIdTokenError, which every caller already answers with a 401.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from utils.self_hosting import is_self_hosted


def self_hosted_id_token_guard_enabled(environ: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environ is None else environ
    return is_self_hosted(source) and bool(source.get('FIREBASE_AUTH_EMULATOR_HOST', '').strip())


def install_self_hosted_id_token_guard(
    environ: Mapping[str, str] | None = None, *, auth_module: Any | None = None
) -> bool:
    """Replace firebase_admin.auth.verify_id_token on a self-hosted emulator stack."""
    if not self_hosted_id_token_guard_enabled(environ):
        return False
    if auth_module is None:
        from firebase_admin import auth as auth_module
    invalid_token = auth_module.InvalidIdTokenError

    def verify_id_token(id_token: Any, *_args: Any, **_kwargs: Any) -> dict[str, str]:
        # Imported on use: it loads the Firestore and Redis clients.
        from utils.local_auth import decode_local_session_token

        uid = decode_local_session_token(id_token) if isinstance(id_token, str) else None
        if uid is None:
            raise invalid_token(
                'Firebase emulator ID tokens are unsigned and refused on a self-hosted stack; '
                'sign in through /v1/auth/local-login'
            )
        return {'uid': uid}

    auth_module.verify_id_token = verify_id_token
    return True
