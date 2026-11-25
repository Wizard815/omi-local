# A language model and speech-to-text on your Android phone

This page runs a small language model (llama.cpp `llama-server`) and a Whisper
speech-to-text server (whisper.cpp `whisper-server`) inside Termux on an Android phone,
and connects them to Omi. Nothing in the app or on the server changes.

- **Speech-to-text works with Omi today.** The app's **Local Whisper** provider sends
  each chunk of audio to `http://<host>:<port>/inference`, which is exactly the
  endpoint `whisper-server` answers.
- **Chat does not go through Omi yet.** Until the app gets a "This phone" chat option,
  you chat with the phone's model in `llama-server`'s own web page.
- **Your Omi server stays the default.** Use the phone when you are away from home,
  have no signal, or want a request to stay on the phone.

Written for a Snapdragon phone with 12-16 GB of RAM. Android itself usually holds
4-6 GB, which leaves **about 7-10 GB for a model**. Lines marked *to verify* depend on
your phone, its Android version or the current Termux packages. Check them once and
note the result.

## Which model

A model needs free RAM for its file plus about **1 GB** for the context (4096 tokens)
and the runtime. All sizes are Q4_K_M GGUF files unless noted, as listed on Hugging Face.

| Model | File | Free RAM it needs | Good for | Weak at |
|---|---|---|---|---|
| Qwen3 0.6B | 0.4 GB (Q8_0: 0.64 GB) | ~1.5 GB | Titles, yes/no classifying, very short rewrites | Facts, anything long |
| **Qwen3 1.7B** (fast pick) | 1.1 GB (Q8_0: 1.8 GB) | ~2-3 GB | Quick replies, a one-paragraph summary of a short clip | Long transcripts, reliable JSON |
| Llama 3.2 1B | 0.8 GB | ~2 GB | Same as Qwen3 1.7B, a bit weaker | Same |
| Llama 3.2 3B | 2.0 GB | ~3 GB | Decent chat, short summaries | Multi-step reasoning, tools |
| SmolLM3 3B | 1.9 GB | ~3 GB | Chat, short summaries; has a thinking mode like Qwen3 | Multilingual beyond its main languages |
| Phi-4-mini (3.8B) | 2.5 GB | ~3.5 GB | Reasoning, structured output | Multilingual |
| **Qwen3 4B** (default) | 2.5 GB | ~3.5 GB | Best all-rounder: chat, summary of one conversation, a simple task list | A day summary over many clips, tool calls |
| Gemma 3n E2B | 3.0 GB | ~4 GB | Multilingual chat, short translation | See the note below |
| Gemma 3n E4B | 4.5 GB | ~5.5 GB | Closer to a 4B-8B model in quality | See the note below |
| Qwen3 8B | 5.0 GB | ~6-7 GB | Closest to your server's quality | Hot and slow on a phone; leaves little RAM for other apps |

**Default: Qwen3 4B Q4_K_M with thinking off.** Qwen3 "thinks" before it answers,
which on a phone costs tens of seconds. The start command below turns that off for the
whole server (`--reasoning-budget 0`). You can also add `/no_think` to the end of any
single message.

**Fast pick: Qwen3 1.7B** (or 0.6B for titles and classifying only).

**Gemma 3n.** Google built these "E2B/E4B" models for phones: with Google's own runtime
they run in the memory of a 2B or 4B model although they have more parameters. The
llama.cpp in Termux knows the `gemma3n` architecture (text input; audio and image input are *to verify*), but loads all the
weights, so the files above need their full size in RAM. Whether they beat Qwen3 4B on
your phone is *to verify*.

**Speed (estimates, to verify on your phone).** On a recent Snapdragon 8-series CPU,
a 3-4B Q4 model writes about 8-15 tokens/s. A 1.7B model writes about twice that.
Reading a long prompt is the slow part: a 30-minute conversation is about 7-8k tokens,
which can take minutes before the first word. Step 7 shows how to measure your phone.

**Speech-to-text models** (whisper.cpp ggml files from `ggerganov/whisper.cpp`):

| Model | File | Notes |
|---|---|---|
| tiny.en | 78 MB | Fast; misses names. What Omi's built-in On-Device option uses. |
| **base.en** (default) | 148 MB | 2-3x faster than small.en with full recall on short clips ([SLOW-HARDWARE.md](SLOW-HARDWARE.md)). |
| small.en / small.en-q5_1 | 488 / 190 MB | More accurate; about real time on a phone CPU (*to verify*). |
| large-v3-turbo-q5_0 | 574 MB | Flagship phones only; slower than real time (*to verify*). |

Use a model without `.en` (for example `ggml-base.bin`) if you speak other languages.

## 1. Install Termux

Install Termux from **F-Droid** or from its **GitHub releases**
(`github.com/termux/termux-app/releases`). The Play Store build is outdated. Install
add-ons such as Termux:Boot from the **same source**: Android refuses to mix
signatures.

Open Termux and update it:

```sh
pkg update && pkg upgrade
```

## 2. Install llama.cpp and whisper.cpp

**llama.cpp** is a Termux package. It includes `llama-server` and `llama-bench`:

```sh
pkg install llama-cpp
```

The packages `llama-cpp-backend-opencl` and `llama-cpp-backend-vulkan` add GPU
backends for the Adreno GPU. Whether they are faster than the CPU on your phone is
*to verify*: Vulkan has often been slower on Adreno, and OpenCL needs the vendor
`libOpenCL.so` to be reachable from Termux. Start with the CPU.

**whisper.cpp** has no Termux package (checked in the main and TUR repositories),
so build it. This takes a few minutes:

```sh
pkg install git cmake clang
git clone --depth 1 https://github.com/ggml-org/whisper.cpp ~/whisper.cpp
cd ~/whisper.cpp
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 4 --target whisper-server
cp build/bin/whisper-server $PREFIX/bin/
```

If the build fails on a missing tool, install it with `pkg install` and run the last
two `cmake` lines again.

## 3. Download the models

Keep the models in Termux's home folder; it is private to Termux.

```sh
mkdir -p ~/models && cd ~/models

# Default chat model (2.5 GB)
curl -L -o Qwen3-4B-Q4_K_M.gguf \
  https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf

# Fast pick (1.1 GB)
curl -L -o Qwen3-1.7B-Q4_K_M.gguf \
  https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf

# Speech-to-text (148 MB)
curl -L -o ggml-base.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
```

Other models from the table, same pattern (`https://huggingface.co/<repo>/resolve/main/<file>`):

| Model | Repo | File |
|---|---|---|
| Qwen3 0.6B | `unsloth/Qwen3-0.6B-GGUF` | `Qwen3-0.6B-Q4_K_M.gguf` |
| Qwen3 0.6B / 1.7B, official Q8_0 | `Qwen/Qwen3-0.6B-GGUF`, `Qwen/Qwen3-1.7B-GGUF` | `Qwen3-0.6B-Q8_0.gguf`, `Qwen3-1.7B-Q8_0.gguf` |
| Qwen3 8B | `Qwen/Qwen3-8B-GGUF` | `Qwen3-8B-Q4_K_M.gguf` |
| Llama 3.2 1B / 3B | `bartowski/Llama-3.2-1B-Instruct-GGUF`, `bartowski/Llama-3.2-3B-Instruct-GGUF` | `Llama-3.2-1B-Instruct-Q4_K_M.gguf`, `Llama-3.2-3B-Instruct-Q4_K_M.gguf` |
| SmolLM3 3B | `ggml-org/SmolLM3-3B-GGUF` | `SmolLM3-Q4_K_M.gguf` |
| Phi-4-mini | `bartowski/microsoft_Phi-4-mini-instruct-GGUF` | `microsoft_Phi-4-mini-instruct-Q4_K_M.gguf` |
| Gemma 3n E2B / E4B | `unsloth/gemma-3n-E2B-it-GGUF`, `unsloth/gemma-3n-E4B-it-GGUF` | `gemma-3n-E2B-it-Q4_K_M.gguf`, `gemma-3n-E4B-it-Q4_K_M.gguf` |

Llama and Gemma models may ask you to accept their licence on Hugging Face first. If a
download gives a small HTML file instead of a model, open the repo's "Files" page and
check the name.

## 4. Start the servers

Both servers listen on `127.0.0.1` only. **Any app on the phone can reach localhost**,
so `llama-server` also requires a key. Create it once:

```sh
head -c 24 /dev/urandom | base64 | tr -d '/+=' > ~/.llama-key
chmod 600 ~/.llama-key
cat ~/.llama-key    # you type this into the web page in step 6
```

Save this as `~/bin/omi-phone-ai.sh`:

```sh
#!/data/data/com.termux/files/usr/bin/sh
# Starts the phone's chat model (8081) and Whisper (8080) for Omi.
MODEL="$HOME/models/Qwen3-4B-Q4_K_M.gguf"   # or Qwen3-1.7B-Q4_K_M.gguf
THREADS=4                                   # your phone's fast cores; see step 7
mkdir -p "$HOME/logs"
termux-wake-lock

pgrep -f llama-server >/dev/null || nohup llama-server -m "$MODEL" \
  --host 127.0.0.1 --port 8081 -c 4096 -t "$THREADS" \
  --jinja --reasoning-budget 0 --api-key "$(cat "$HOME/.llama-key")" \
  > "$HOME/logs/llama-server.log" 2>&1 &

pgrep -f whisper-server >/dev/null || nohup whisper-server \
  -m "$HOME/models/ggml-base.en.bin" \
  --host 127.0.0.1 --port 8080 -t "$THREADS" \
  > "$HOME/logs/whisper-server.log" 2>&1 &
```

```sh
mkdir -p ~/bin && chmod +x ~/bin/omi-phone-ai.sh
~/bin/omi-phone-ai.sh
```

To stop both: `pkill llama-server; pkill whisper-server; termux-wake-unlock`.

`whisper-server` has no key, and the app's Local Whisper provider sends none. It only
turns audio into text, but any app on the phone could use it while it runs. Stop it
when you don't need it.

## 5. Keep them running

Android stops background work aggressively. Do all four:

1. **Wake lock.** The script runs `termux-wake-lock`. Termux's notification then shows
   "wake lock held".
2. **Battery.** Settings → Apps → Termux → Battery → **Unrestricted**. Do the same for
   Termux:Boot if you install it.
3. **Phantom process killer (Android 12 and later).** Android kills child processes of
   apps, which includes these servers, once there are too many. *To verify on your
   Android version:*
   - Android 14 and later: Developer options → **Disable child process
     restrictions**, or from a computer: `adb shell settings put global settings_enable_monitor_phantom_procs false`.
   - Android 12: only from a computer:
     `adb shell "/system/bin/device_config set_sync_disabled_for_tests persistent"` and
     `adb shell "/system/bin/device_config put activity_manager max_phantom_processes 2147483647"`.
4. **Start on boot (optional).** Install **Termux:Boot** (same source as Termux), open it
   once, then:

   ```sh
   mkdir -p ~/.termux/boot
   printf '#!/data/data/com.termux/files/usr/bin/sh\n~/bin/omi-phone-ai.sh\n' \
     > ~/.termux/boot/omi-phone-ai
   chmod +x ~/.termux/boot/omi-phone-ai
   ```

## 6. Use it in Omi

**Speech-to-text:** in the Omi app, open **Settings → Transcription**, choose
**Local Whisper**, and set **Host** `127.0.0.1` and **Port** `8080`. The app then posts
each chunk (up to 30 seconds) to `http://127.0.0.1:8080/inference` as field `file`,
with `response_format=verbose_json` (`SttProvider.localWhisper` in
`app/lib/models/stt_provider.dart`). Text arrives after each chunk, not word by word.

To go back, choose **Omi Parakeet (server)** again: that is your server's `omi-audio`.

**Chat:** open `http://127.0.0.1:8081` in the phone's browser. This is `llama-server`'s
built-in chat page. Enter the key from step 4 in its settings. It cannot see your Omi
memories or conversations, and its history stays in that browser.

## 7. Check that it works

```sh
# Chat model: expect a short JSON answer with "choices"
curl -s http://127.0.0.1:8081/v1/chat/completions \
  -H "Authorization: Bearer $(cat ~/.llama-key)" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Say hello in five words. /no_think"}],"max_tokens":32}'

# Whisper: expect the text of the clip (the sample comes with the source)
curl -s http://127.0.0.1:8080/inference \
  -F file=@$HOME/whisper.cpp/samples/jfk.wav -F response_format=json
```

Measure your phone's speed and pick the thread count. `pp512` is how fast it reads a
prompt, `tg128` how fast it writes; both in tokens per second:

```sh
pkill llama-server    # free the RAM first
llama-bench -m ~/models/Qwen3-4B-Q4_K_M.gguf -t 4,6,8 -p 512 -n 128
```

Use the `-t` value with the best `tg128` in the script, then start it again. A higher
thread count is often slower, because Snapdragon chips mix fast and efficient cores.

If a server does not answer, read `~/logs/llama-server.log` or
`~/logs/whisper-server.log`. "failed to allocate" means the model does not fit: close
other apps or take a smaller model.

## 8. Trade-offs

- **Speech-to-text on the phone is plainer than the server's.** No speaker labels, no
  voice profiles, no recognising people, and a smaller model than the server's
  large-v3-turbo. Keep **Omi Parakeet (server)** as your default and switch to Local
  Whisper when you are away or want audio to stay on the phone.
- **Phone speech-to-text still needs your server to make conversations.** The app hands
  the text to the server over its usual connection. If the server can't be reached,
  the recording is kept on the phone and transcribed by the server when it syncs.
- **Heat and battery.** A 4B model at full speed heats the phone within minutes and
  uses a lot of battery; long prompts throttle it. Expect less on a warm phone, and
  stop the servers when you don't need them.
- **What stays on the server:** day summaries, tasks, memories, translation, speaker
  identification, and chat about your memories and conversations. A 0.6B-8B phone
  model is too slow or too weak for them, and it can't see your server's data.

## What's next

- **v1:** a "This phone" option in the app's chat that talks to this `llama-server`,
  either always or when your server can't be reached, with every phone answer labelled
  as such. An AI setting chooses between this phone and the server.
- **v2:** the same servers run inside the Omi app, without Termux.

## Starting and stopping from Studio Own

If you use the Studio Own Android app (Hermes Studio), its *This phone* page can run
Termux commands after you confirm each one on the phone. Add
`allow-external-apps = true` to `~/.termux/termux.properties` (see
`hermes-studio/packages/android/README.md`). It can then run `~/bin/omi-phone-ai.sh`,
or the stop command from step 4.
