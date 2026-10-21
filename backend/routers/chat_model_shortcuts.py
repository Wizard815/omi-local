"""Chat model shortcuts ("/adult" → a bigger model) for self-hosted deployments.

The app's chat input lists these as slash commands and its /model sheet edits
them; utils/retrieval/graph.execute_chat_stream applies one per turn. See utils/llm/chat_model_shortcuts.py.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from utils.executors import db_executor, run_blocking
from utils.llm.chat_model_shortcuts import (
    InvalidShortcutName,
    delete_shortcut,
    get_shortcuts,
    list_shortcut_names,
    set_shortcut,
)
from utils.llm.local_models import list_proxy_models
from utils.llm.model_config import is_local_mode, local_base_chat_model
from utils.other import endpoints as auth

router = APIRouter()


class ChatModelShortcut(BaseModel):
    name: str
    model: Optional[str] = None


class AvailableChatModel(BaseModel):
    id: str
    owned_by: str = ''


class ChatModelShortcutsResponse(BaseModel):
    enabled: bool = Field(
        description='False unless the backend runs in local-LLM mode (shortcuts need the merge proxy).'
    )
    base_model: str = ''
    shortcuts: List[ChatModelShortcut] = Field(default_factory=list)
    available_models: List[AvailableChatModel] = Field(default_factory=list)


class SetChatModelShortcutRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)


async def _shortcuts_response() -> ChatModelShortcutsResponse:
    if not await run_blocking(db_executor, is_local_mode):
        return ChatModelShortcutsResponse(enabled=False)
    configured = await run_blocking(db_executor, get_shortcuts)
    names = await run_blocking(db_executor, list_shortcut_names)
    base_model = await run_blocking(db_executor, local_base_chat_model)
    return ChatModelShortcutsResponse(
        enabled=True,
        base_model=base_model,
        shortcuts=[ChatModelShortcut(name=name, model=configured.get(name)) for name in names],
        available_models=[AvailableChatModel(**m) for m in await list_proxy_models()],
    )


async def _require_local_mode() -> None:
    if not await run_blocking(db_executor, is_local_mode):
        raise HTTPException(status_code=409, detail='Chat model shortcuts need a local LLM backend (OPENAI_BASE_URL).')


@router.get('/v1/chat/model-shortcuts', tags=['chat'], response_model=ChatModelShortcutsResponse)
async def get_chat_model_shortcuts(uid: str = Depends(auth.get_current_user_uid)):
    return await _shortcuts_response()


@router.put('/v1/chat/model-shortcuts/{name}', tags=['chat'], response_model=ChatModelShortcutsResponse)
async def put_chat_model_shortcut(
    name: str, request: SetChatModelShortcutRequest, uid: str = Depends(auth.get_current_user_uid)
):
    await _require_local_mode()
    try:
        await run_blocking(db_executor, set_shortcut, name, request.model)
    except InvalidShortcutName as e:
        raise HTTPException(status_code=422, detail=str(e))
    return await _shortcuts_response()


@router.delete('/v1/chat/model-shortcuts/{name}', tags=['chat'], response_model=ChatModelShortcutsResponse)
async def delete_chat_model_shortcut(name: str, uid: str = Depends(auth.get_current_user_uid)):
    await _require_local_mode()
    try:
        await run_blocking(db_executor, delete_shortcut, name)
    except InvalidShortcutName as e:
        raise HTTPException(status_code=422, detail=str(e))
    return await _shortcuts_response()
