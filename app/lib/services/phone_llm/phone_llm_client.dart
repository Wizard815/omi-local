// The HTTP client for the phone's own model (llama-server in Termux, OpenAI API). It never
// throws from a chat stream: every failure ends the stream as one error chunk.

import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:uuid/uuid.dart';

import 'package:omi/backend/schema/message.dart';

/// Error codes a phone reply's error chunk carries (`ServerMessageChunk.errorCode`).
abstract final class PhoneLlmError {
  /// The phone model refused the API key (HTTP 401/403).
  static const unauthorized = 'phone_unauthorized';

  /// Nothing answered at the address, or the connection broke.
  static const unreachable = 'phone_unreachable';

  /// The model answered with an error, or the stream ended before it finished.
  static const failed = 'phone_failed';
}

class PhoneLlmException implements Exception {
  const PhoneLlmException(this.code, [this.detail]);
  final String code;
  final String? detail;

  @override
  String toString() => 'PhoneLlmException($code)';
}

/// Talks to an OpenAI-compatible server on the phone (llama.cpp `llama-server` in Termux) and maps
/// its streamed answer onto the chat's chunk types: `delta.content` → data,
/// `delta.reasoning_content` → think (one chunk per line), `[DONE]` → done, anything else → error.
class PhoneLlmClient {
  PhoneLlmClient({required this.baseUrl, this.apiKey = '', http.Client? client}) : _client = client;

  final String baseUrl;
  final String apiKey;
  final http.Client? _client;

  Uri _uri(String path) => Uri.parse('${baseUrl.replaceFirst(RegExp(r'/+$'), '')}/$path');

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (apiKey.isNotEmpty) 'Authorization': 'Bearer $apiKey',
      };

  Future<T> _use<T>(Future<T> Function(http.Client client) run) async {
    final client = _client ?? http.Client();
    try {
      return await run(client);
    } finally {
      if (_client == null) client.close();
    }
  }

  static String _codeFor(int status) =>
      status == 401 || status == 403 ? PhoneLlmError.unauthorized : PhoneLlmError.failed;

  /// The model ids `GET {base}/models` lists.
  Future<List<String>> listModels({Duration timeout = const Duration(seconds: 5)}) => _use((client) async {
        final http.Response response;
        try {
          response = await client.get(_uri('models'), headers: _headers).timeout(timeout);
        } catch (e) {
          throw PhoneLlmException(PhoneLlmError.unreachable, e.runtimeType.toString());
        }
        if (response.statusCode != 200) throw PhoneLlmException(_codeFor(response.statusCode));
        try {
          final data = (jsonDecode(response.body) as Map)['data'] as List;
          return [
            for (final m in data)
              if (m is Map && m['id'] is String && (m['id'] as String).isNotEmpty) m['id'] as String,
          ];
        } catch (_) {
          throw const PhoneLlmException(PhoneLlmError.failed, 'models');
        }
      });

  /// One tiny completion: the round trip the Test button shows (it also checks the API key).
  Future<Duration> test(String model, {Duration timeout = const Duration(seconds: 60)}) => _use((client) async {
        final watch = Stopwatch()..start();
        final http.Response response;
        try {
          response = await client
              .post(
                _uri('chat/completions'),
                headers: _headers,
                body: jsonEncode({
                  if (model.isNotEmpty) 'model': model,
                  'max_tokens': 1,
                  'messages': [
                    {'role': 'user', 'content': 'Hi'},
                  ],
                }),
              )
              .timeout(timeout);
        } catch (e) {
          throw PhoneLlmException(PhoneLlmError.unreachable, e.runtimeType.toString());
        }
        if (response.statusCode != 200) throw PhoneLlmException(_codeFor(response.statusCode));
        return watch.elapsed;
      });

  /// Streams the answer to [messages] (OpenAI chat format). Completing [cancel] stops it at once;
  /// so does cancelling the subscription. Never throws: a failure is the last chunk, an error.
  Stream<ServerMessageChunk> streamChat(
    List<Map<String, String>> messages, {
    required String model,
    Future<void>? cancel,
  }) async* {
    final id = 'phone-${const Uuid().v4()}';
    final client = _client ?? http.Client();
    final request = http.Request('POST', _uri('chat/completions'))
      ..headers.addAll({..._headers, 'Accept': 'text/event-stream'})
      ..body = jsonEncode({if (model.isNotEmpty) 'model': model, 'stream': true, 'messages': messages});
    ServerMessageChunk error(String code) => ServerMessageChunk(id, '', MessageChunkType.error, errorCode: code);

    final answer = StringBuffer();
    final thinking = StringBuffer();
    var finished = false;
    var stopped = false;
    unawaited(cancel?.then((_) {
      stopped = true;
      if (_client == null) client.close(); // Aborts the open request.
    }));
    try {
      final http.StreamedResponse response;
      try {
        response = await client.send(request);
      } catch (_) {
        if (!stopped) yield error(PhoneLlmError.unreachable);
        return;
      }
      if (response.statusCode != 200) {
        unawaited(response.stream.drain<void>().catchError((_) {}));
        yield error(_codeFor(response.statusCode));
        return;
      }
      // Reasoning streams token by token; it is shown one line at a time.
      Iterable<ServerMessageChunk> flushThinking({bool all = false}) sync* {
        while (true) {
          final text = thinking.toString();
          final nl = text.indexOf('\n');
          if (nl < 0 && !all) return;
          final line = (nl < 0 ? text : text.substring(0, nl)).trim();
          thinking.clear();
          if (nl >= 0) thinking.write(text.substring(nl + 1));
          if (line.isNotEmpty) yield ServerMessageChunk(id, line, MessageChunkType.think);
          if (nl < 0) return;
        }
      }

      try {
        await for (final line in response.stream.transform(utf8.decoder).transform(const LineSplitter())) {
          if (stopped) return;
          final event = parseSseLine(line);
          if (event == null) continue;
          if (event.done) {
            for (final chunk in flushThinking(all: true)) {
              yield chunk;
            }
            yield done(id, answer.toString());
            return;
          }
          if (event.error) {
            yield error(PhoneLlmError.failed);
            return;
          }
          if (event.reasoning.isNotEmpty) {
            thinking.write(event.reasoning);
            for (final chunk in flushThinking()) {
              yield chunk;
            }
          }
          if (event.content.isNotEmpty) {
            for (final chunk in flushThinking(all: true)) {
              yield chunk;
            }
            answer.write(event.content);
            yield ServerMessageChunk(id, event.content, MessageChunkType.data);
          }
          if (event.finished) finished = true;
        }
      } catch (_) {
        if (!stopped) yield error(PhoneLlmError.unreachable);
        return;
      }
      if (stopped) return;
      // Some servers end without `[DONE]`; a stream that reported its finish reason is complete.
      if (finished) {
        for (final chunk in flushThinking(all: true)) {
          yield chunk;
        }
        yield done(id, answer.toString());
      } else {
        yield error(PhoneLlmError.failed);
      }
    } finally {
      if (_client == null) client.close();
    }
  }

  static ServerMessageChunk done(String id, String text) => ServerMessageChunk(
        id,
        '',
        MessageChunkType.done,
        message: ServerMessage(id, DateTime.now(), text, MessageSender.ai, MessageType.text, null, false, [], [], []),
      );

  /// One `data:` line of an OpenAI stream, or null for anything else (comments, blank lines).
  static PhoneSseEvent? parseSseLine(String line) {
    if (line.startsWith('error:')) return const PhoneSseEvent(error: true);
    if (!line.startsWith('data:')) return null;
    final payload = line.substring(5).trim();
    if (payload.isEmpty) return null;
    if (payload == '[DONE]') return const PhoneSseEvent(done: true);
    final Object? json;
    try {
      json = jsonDecode(payload);
    } catch (_) {
      return null;
    }
    if (json is! Map) return null;
    if (json['error'] != null) return const PhoneSseEvent(error: true);
    final choices = json['choices'];
    if (choices is! List || choices.isEmpty || choices.first is! Map) return null;
    final choice = choices.first as Map;
    final delta = choice['delta'] is Map ? choice['delta'] as Map : const {};
    final content = delta['content'];
    final reasoning = delta['reasoning_content'];
    return PhoneSseEvent(
      content: content is String ? content : '',
      reasoning: reasoning is String ? reasoning : '',
      finished: choice['finish_reason'] is String,
    );
  }
}

class PhoneSseEvent {
  const PhoneSseEvent({
    this.content = '',
    this.reasoning = '',
    this.finished = false,
    this.done = false,
    this.error = false,
  });

  final String content;
  final String reasoning;
  final bool finished;
  final bool done;
  final bool error;
}
