"""A chat stream sends its keep-alive from the start, not only after the first model event.

A Cloudflare Tunnel (omi-host/REMOTE-ACCESS.md) drops an HTTP response that stays idle about
100 s, and a slow self-hosted model can think far longer than that before its first token.
The keep-alive is the existing ``think: Still working…`` frame, which the phone (Dart
``parseMessageChunk``) and desktop (``parseMessagesSse``/``useChat``) clients already treat as
a status line, never as reply text.
"""

import asyncio
import os
from contextlib import ExitStack
from unittest.mock import patch

os.environ.setdefault('ENCRYPTION_SECRET', '0123456789abcdef0123456789abcdef')
os.environ.setdefault('OPENAI_API_KEY', 'sk-test')
os.environ.setdefault('TYPESENSE_API_KEY', 'test-typesense-key')
os.environ.setdefault('TYPESENSE_HOST', 'localhost')
os.environ.setdefault('TYPESENSE_HOST_PORT', '8108')
os.environ.setdefault('TYPESENSE_PROTOCOL', 'http')

# Module scope: the heavy import cost lands in collection, not in a test's time budget.
import utils.retrieval.graph as graph  # noqa: E402
import utils.retrieval.agentic as agentic  # noqa: E402

HEARTBEAT = f'think: {agentic.AGENT_STREAM_PROGRESS_HEARTBEAT}'


async def _agentic_chunks(producer, callback_data=None):
    with ExitStack() as stack:
        stack.enter_context(patch.object(agentic, 'get_user_timezone', lambda _uid: 'UTC'))
        stack.enter_context(patch.object(agentic, '_get_agentic_qa_prompt', lambda *_a, **_k: 'SYSTEM'))
        stack.enter_context(patch.object(agentic, 'load_app_tools', lambda _uid: []))
        stack.enter_context(patch.object(agentic, 'get_current_datetime_block', lambda _u, tz=None, location=None: ''))
        stack.enter_context(patch.object(agentic, '_convert_tools', lambda _core, _app: ([], {})))
        stack.enter_context(patch.object(agentic, '_messages_to_anthropic', lambda _messages: []))
        stack.enter_context(patch.object(agentic, '_inject_current_datetime', lambda messages, _block: messages))
        stack.enter_context(patch.object(agentic, '_run_openai_agent_stream', producer))
        return [
            chunk
            async for chunk in agentic.execute_agentic_chat_stream(
                'uid1',
                [],
                app=None,
                callback_data=callback_data if callback_data is not None else {},
                chat_session=None,
            )
        ]


def test_stream_wait_heartbeats_only_while_a_deadline_is_further_away():
    assert agentic.stream_wait(20.0, 300.0) == (20.0, True)
    assert agentic.stream_wait(20.0, 12.5) == (12.5, False)
    assert agentic.stream_wait(20.0, 20.0) == (20.0, False)
    assert agentic.stream_wait(20.0, -3.0) == (0.0, False)


async def test_keep_alive_arrives_before_the_first_model_event():
    callback_data = {}

    async def slow_first_token(*args, **_kwargs):
        callback, full_response = args[4], args[5]
        await asyncio.sleep(0.08)  # a local model thinking, silent
        await callback.put_data('hello')
        full_response.append('hello')
        await callback.end()

    with patch.object(agentic, 'AGENT_STREAM_PROGRESS_HEARTBEAT_SECONDS', 0.01), patch.object(
        agentic, 'AGENT_STREAM_FIRST_EVENT_TIMEOUT_SECONDS', 5.0
    ):
        chunks = await _agentic_chunks(slow_first_token, callback_data)

    assert chunks[0] == f'think: {agentic.AGENT_STREAM_SETUP_PROGRESS}'
    first_data = chunks.index('data: hello')
    assert HEARTBEAT in chunks[1:first_data], chunks
    assert chunks[-1] is None
    assert callback_data['answer'] == 'hello'
    assert 'error' not in callback_data


async def test_keep_alives_do_not_extend_the_first_event_deadline():
    callback_data = {}

    async def silent(*_args, **_kwargs):
        await asyncio.Event().wait()

    with patch.object(agentic, 'AGENT_STREAM_PROGRESS_HEARTBEAT_SECONDS', 0.01), patch.object(
        agentic, 'AGENT_STREAM_FIRST_EVENT_TIMEOUT_SECONDS', 0.06
    ), patch.object(agentic, 'AGENT_STREAM_CANCEL_GRACE_SECONDS', 0.05):
        chunks = await _agentic_chunks(silent, callback_data)

    assert HEARTBEAT in chunks
    assert chunks[-2:] == [f'error: {agentic.AGENT_STREAM_TIMEOUT_MESSAGE}', None]
    assert callback_data['error'] == 'idle_timeout'


async def test_drained_callback_streams_keep_alive_before_the_first_event_too():
    callback = agentic.AsyncStreamingCallback()

    async def slow_first_token():
        await asyncio.sleep(0.08)
        await callback.put_data('hi')
        await callback.end()
        # Still running while the consumer reads the queue, as a real producer unwinding.
        await asyncio.sleep(0.02)

    task = asyncio.create_task(slow_first_token())
    with patch.object(graph, 'AGENT_STREAM_PROGRESS_HEARTBEAT_SECONDS', 0.01), patch.object(
        graph, 'AGENT_STREAM_FIRST_EVENT_TIMEOUT_SECONDS', 5.0
    ):
        chunks = [chunk async for chunk in graph._drain_chat_callback(callback, task, route='persona')]

    assert chunks[-1] == 'data: hi'
    assert chunks[0] == HEARTBEAT
    assert set(chunks[:-1]) == {HEARTBEAT}
