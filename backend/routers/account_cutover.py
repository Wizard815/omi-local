"""Authenticated account-cutover bootstrap/control projection."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from database import account_cutover as account_cutover_db
from database.read_boundary import MalformedDocError
from models.account_cutover import AccountCutoverControl
from utils.account_cutover.control import build_account_cutover_control
from utils.executors import db_executor, run_blocking
from utils.other import endpoints as auth

router = APIRouter(prefix='/v1/account/cutover', tags=['account-cutover'])


@router.get('/control', response_model=AccountCutoverControl)
async def get_account_cutover_control(
    request: Request,
    uid: str = Depends(auth.get_current_user_uid),
    x_app_platform: Optional[str] = Header(None, alias='X-App-Platform'),
    x_app_build: Optional[str] = Header(None, alias='X-App-Build'),
    x_app_version: Optional[str] = Header(None, alias='X-App-Version'),
) -> AccountCutoverControl:
    """Stable authenticated bootstrap projection for bridge clients.

    Always reachable for signed-in users, including while product traffic is
    fenced for migrating / force-upgrade / stranded states. Does not migrate
    any account; absent documents project as legacy. Malformed authoritative
    documents fail closed.
    """

    del request  # Request retained for OpenAPI/middleware parity with other control routes.

    def _load() -> AccountCutoverControl:
        try:
            record = account_cutover_db.get_account_cutover_record(uid)
        except MalformedDocError as error:
            # Self-hosted offline deployments have no whole-account cutover to
            # run — this record can only be a Firestore-emulator hiccup (e.g.
            # a restart), never a real migration. Fail open to the legacy
            # projection instead of fencing the whole app, same bypass
            # PROVIDER_MODE=='offline' already gets in subscription.py.
            if os.getenv('PROVIDER_MODE', '').strip().lower() == 'offline':
                record = account_cutover_db.default_legacy_record(uid)
            else:
                raise HTTPException(
                    status_code=503,
                    detail={'code': 'account_cutover_state_unavailable', 'retryable': True},
                ) from error
        return build_account_cutover_control(
            record,
            platform=x_app_platform,
            x_app_build=x_app_build,
            x_app_version=x_app_version,
        )

    return await run_blocking(db_executor, _load)


__all__ = ['router']
