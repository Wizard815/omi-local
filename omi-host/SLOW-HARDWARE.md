# Running Omi on slow hardware

A home LLM or a CPU-only transcriber is slower than the cloud models Omi was tuned
for. This page lists the settings that decide how Omi copes with that, and what each
one trades. All of them go in `omi-host/.env`; compose passes them to the containers.

## A slow language model

| Setting | Default | What it does |
|---|---|---|
| `OMI_LOCAL_LLM_TIMEOUT_SECONDS` | `300` | The longest one model call may take. Every LLM deadline derives from it: summaries, memories, tasks, chat helpers, memory intake, the day summary, the notes agent (3 calls), a chat answer (first words within 1 call, the whole answer within 4) and the HTTP request limit (3x). Raise it if a call still times out in `docker logs omi-local`; lower it if a hung model server should fail sooner. |
| `OMI_LOCAL_BACKGROUND_PROCESSING` | `on` | A conversation uploaded from the phone or desktop is stored at once with its transcript and answered as "processing". Its notes, tasks and memories are written in the background, one conversation at a time. `off` makes the upload wait for the model (and time out if it is slower than the HTTP limit). |
| `OMI_LOCAL_PROCESSING_MAX_ATTEMPTS` | `5` | How often background processing tries one conversation. Waits between tries: 1, 5, 15, then 60 minutes. The transcript is kept whatever happens; after the last try, opening the conversation in the app tries once more. |
| `OMI_LOCAL_TASK_DEDUP_THRESHOLD` | `0.45` | How similar (cosine, local Chroma store) an open task must be to a new transcript to be shown to the model as a possible duplicate. Measured with `text-embedding-3-small`: related tasks 0.52-0.54, unrelated 0.07-0.30. Raise it if unrelated tasks get suppressed. |
| `OMI_LOCAL_LLM_REASONING_EFFORT` | `low` | Sent as `reasoning_effort` on short structured calls only (a folder pick, three-word metadata, a memory route, chat classifiers). Long writing (notes, memory extraction, chat answers) is left to the model. `none` is fastest; empty sends nothing. |

Measured with a reasoning model (30-100 s per call) through OpenRouter:

| | before | after |
|---|---|---|
| Upload of one conversation (from-segments) | 120 s, then `504`; 6 of 8 failed, 2 lost on a model timeout | 0.10-0.18 s, answered `processing` with the transcript stored; notes written in the background |
| Metadata call (people/topics/dates), `reasoning_effort` unset → `none` | 26.6 s, 5 261 reasoning tokens | 1.7 s, 0 reasoning tokens |

`low` barely changes that model (24.1 s); other models (gpt-oss, Qwen3, models served
by Ollama) honour it more. llama.cpp, LM Studio, Ollama and OpenRouter accept the field;
a server that does not know it ignores it.

What the app shows meanwhile: the conversation appears on Home with its title and a
"writing notes" step, and its transcript is readable at once. Nothing is shown as an
error while the model works.

## Slow live transcription

Live text appears once an utterance ends. How long that takes is the sum of
**how long the speaker talks** (up to `OMI_VAD_MAX_UTTERANCE_S`), **the pause that
ends the utterance** (`OMI_VAD_MIN_SILENCE_MS`), and **the decode** (model, beam, encoder
window). These are `omi-audio` settings for the default whisper.cpp engine.

| Setting | Default | Faster | Trade-off |
|---|---|---|---|
| `WHISPER_MODEL` | `large-v3-turbo` | `small.en`, `base.en` | The biggest lever on a CPU. Smaller models miss more words, names especially. |
| `OMI_VAD_MAX_UTTERANCE_S` | `12` (was 20) | `8` | Someone talking without pausing gets text every N seconds. Pieces are cut at the quietest moment of the last 1.5 s (`OMI_VAD_CUT_SEARCH_MS`), so words are not split; shorter pieces give the model less context. |
| `OMI_VAD_MIN_SILENCE_MS` | `1000` | `600` | A shorter pause ends an utterance, so text comes sooner; a sentence with a long pause in it may arrive as two lines. |
| `OMI_ASR_LIVE_BEAM_SIZE`, `OMI_ASR_LIVE_BEST_OF` | same as `OMI_ASR_BEAM_SIZE`/`_BEST_OF` (5/5) | `1`/`1` (greedy) | Live decode only; uploaded recordings keep the upload settings. Greedy is 10-30 % faster on a CPU and slightly less accurate. |
| `OMI_ASR_LIVE_AUDIO_CTX` | `0` (full 30 s window) | `auto` | whisper.cpp otherwise encodes 30 s of audio for every utterance. `auto` sizes it to the utterance: 2-3x faster per short utterance on a CPU, with some risk of clipped or repeated words on very short clips. |
| `THREADS` | `8` | the machine's physical cores | whisper.cpp CPU threads. |

Measured on a 4-core CPU with whisper.cpp `small.en`, streaming a LibriSpeech
sentence (4.4 s) in real time through the bundled service
(`omi-host/bench_audio_pipeline.py --engine whispercpp`):

| | before | defaults now | fast live (`LIVE_BEAM 1/1`, `LIVE_AUDIO_CTX=auto`, `MIN_SILENCE 600`) |
|---|---|---|---|
| Text after the speaker pauses (mean) | 6.9 s | 7.1 s | **2.0 s** |
| First text in a 38 s monologue with no pauses | 28.5 s | **18.9 s** | **15.0 s** |

Decode of one utterance on the same CPU (median of 3, word recall 1.0 in every case):

| model | 5 s, beam 5 | 5 s, greedy | 5 s, greedy + `audio_ctx=auto` | 12 s, beam 5 | 12 s, greedy + `audio_ctx=auto` |
|---|---|---|---|---|---|
| tiny.en | 0.60 s | 0.47 s | — | 1.08 s | — |
| base.en | 1.33 s | 0.97 s | 0.37 s | 2.04 s | 1.13 s |
| small.en | 3.24 s | 3.11 s | 1.05 s | 5.90 s | 3.24 s |

On a GPU the decode is already short and the VAD settings dominate.
