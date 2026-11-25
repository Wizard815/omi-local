# Phone LLM (Android, v1)

Chat answered by a model that runs on the phone itself: llama.cpp's `llama-server` in Termux on
`127.0.0.1:8081` with an API key (OpenAI API under `/v1`). Setting it up on the phone is
`omi-host/PHONE-LLM.md`. Nothing here starts or installs a model.

| File | Job |
|---|---|
| `phone_llm_settings.dart` | AI source (Server / This phone / Phone when the server can't be reached), address, model, context toggle. Own preference keys; the API key is in secure storage. |
| `phone_llm_client.dart` | `GET /models`, a one-token Test, and the streamed `POST /chat/completions` mapped to chat chunks: `content` → data, `reasoning_content` → think (one line each), `[DONE]` → done, anything else → error (`PhoneLlmError`). |
| `phone_llm_router.dart` | Per message: server or phone. Auto switches only when the server **cannot be reached** (refused, DNS, no network) within a ~2.5 s probe *and* `GET /health` says the phone model is running. A slow server keeps the turn. |
| `phone_llm_context.dart` | System prompt, the newest chat turns and, if allowed, titles and overviews of cached conversations, within ~2.5k tokens. Conversations give way first, then older turns. |
| `phone_llm_status.dart` | The phone model's last `/health` result, checked only while the app is in front, for the AI source widget. |

`MessageProvider` asks the router before each typed reply. A phone answer:

- is labelled "Answered on this phone (model)" with a note that it is not on the server;
- is never sent to the server, and gets no server follow-ups (no chat naming, no server session);
- is kept in memory for the chat while Omi runs, so a history reload does not drop it;
- can be stopped like any reply. A failed server reply offers "Ask This Phone Instead".

The audio record (the WAL) stays the source of truth: phone answers are chat only and create
nothing on the server. Saving them to the server needs a backend endpoint (not in v1).

The AI source home-screen widget (`OmiAiSourceWidget`) shows the last known state. Its Switch opens
Omi on `omi://app/ai-source/next`, which moves to the next source inside the app: an activity
started by a tap, so it needs no background work and no extra permission.
