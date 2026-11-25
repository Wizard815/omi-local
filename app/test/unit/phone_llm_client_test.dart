import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:omi/backend/schema/message.dart';
import 'package:omi/services/phone_llm/phone_llm_client.dart';

/// The phone model client against recorded OpenAI-style SSE (llama-server), with no network.
void main() {
  String data(Map<String, Object?> delta, {String? finish}) => 'data: ${jsonEncode({
            'choices': [
              {'index': 0, 'delta': delta, 'finish_reason': finish},
            ],
          })}\n\n';

  late List<http.BaseRequest> requests;

  PhoneLlmClient client(Stream<String> Function() body, {int status = 200}) {
    requests = [];
    return PhoneLlmClient(
      baseUrl: 'http://127.0.0.1:8081/v1',
      apiKey: 'k-test',
      client: MockClient.streaming((request, _) async {
        requests.add(request);
        return http.StreamedResponse(body().transform(utf8.encoder), status);
      }),
    );
  }

  Future<List<ServerMessageChunk>> collect(Stream<ServerMessageChunk> s) => s.toList();

  final ask = [
    {'role': 'user', 'content': 'hi'},
  ];

  test('content streams as data and [DONE] ends with the whole answer', () async {
    final c = client(() => Stream.fromIterable([
          ': keep-alive\n\n',
          data({'role': 'assistant'}),
          data({'content': 'Hel'}),
          data({'content': 'lo.'}),
          data({}, finish: 'stop'),
          'data: [DONE]\n\n',
        ]));
    final chunks = await collect(c.streamChat(ask, model: 'small-q4'));

    expect(chunks.where((c) => c.type == MessageChunkType.data).map((c) => c.text), ['Hel', 'lo.']);
    expect(chunks.last.type, MessageChunkType.done);
    expect(chunks.last.message!.text, 'Hello.');
    expect(chunks.last.message!.sender, MessageSender.ai);
    final request = requests.single as http.Request;
    expect(request.url.toString(), 'http://127.0.0.1:8081/v1/chat/completions');
    expect(request.headers['Authorization'], 'Bearer k-test');
    final body = jsonDecode(request.body) as Map;
    expect(body['stream'], isTrue);
    expect(body['model'], 'small-q4');
    expect(body['messages'], ask);
  });

  test('reasoning_content becomes think chunks, one line each, before the answer', () async {
    final c = client(() => Stream.fromIterable([
          data({'reasoning_content': 'First I '}),
          data({'reasoning_content': 'read it.\nThen '}),
          data({'reasoning_content': 'I answer.'}),
          data({'content': 'Yes.'}),
          'data: [DONE]\n\n',
        ]));
    final chunks = await collect(c.streamChat(ask, model: ''));

    expect(chunks.map((c) => '${c.type.value}:${c.text}'), [
      'think:First I read it.',
      'think:Then I answer.',
      'data:Yes.',
      'done:',
    ]);
    final body = jsonDecode((requests.single as http.Request).body) as Map;
    expect(body.containsKey('model'), isFalse, reason: 'no model picked: the server uses the one it loaded');
  });

  test('an error event ends the reply with an error', () async {
    final c = client(() => Stream.fromIterable([
          data({'content': 'Partial'}),
          'data: ${jsonEncode({
                'error': {'code': 500, 'message': 'context size exceeded', 'type': 'server_error'},
              })}\n\n',
        ]));
    final chunks = await collect(c.streamChat(ask, model: 'm'));

    expect(chunks.last.type, MessageChunkType.error);
    expect(chunks.last.errorCode, PhoneLlmError.failed);
  });

  test('a stream cut before it finished is an error, not a done', () async {
    final c = client(() => Stream.fromIterable([
          data({'content': 'Half an ans'})
        ]));
    final chunks = await collect(c.streamChat(ask, model: 'm'));

    expect(chunks.map((c) => c.type), [MessageChunkType.data, MessageChunkType.error]);
    expect(chunks.last.errorCode, PhoneLlmError.failed);
  });

  test('a broken connection mid-stream is an error', () async {
    final c = client(() async* {
      yield data({'content': 'Half'});
      throw const FormatException('connection reset');
    });
    final chunks = await collect(c.streamChat(ask, model: 'm'));

    expect(chunks.last.type, MessageChunkType.error);
    expect(chunks.last.errorCode, PhoneLlmError.unreachable);
  });

  test('a finish reason without [DONE] still completes', () async {
    final c = client(() => Stream.fromIterable([
          data({'content': 'Ok'}),
          data({}, finish: 'length')
        ]));
    final chunks = await collect(c.streamChat(ask, model: 'm'));

    expect(chunks.last.type, MessageChunkType.done);
    expect(chunks.last.message!.text, 'Ok');
  });

  test('401 from the API key is its own error', () async {
    final c = client(() => Stream.value('{"error":{"code":401,"message":"Invalid API Key"}}'), status: 401);
    final chunks = await collect(c.streamChat(ask, model: 'm'));

    expect(chunks.single.type, MessageChunkType.error);
    expect(chunks.single.errorCode, PhoneLlmError.unauthorized);
  });

  test('nothing listening is unreachable', () async {
    final c = PhoneLlmClient(
      baseUrl: 'http://127.0.0.1:8081/v1',
      client: MockClient.streaming((_, __) async => throw http.ClientException('Connection refused')),
    );
    final chunks = await collect(c.streamChat(ask, model: 'm'));

    expect(chunks.single.errorCode, PhoneLlmError.unreachable);
  });

  test('Stop ends the stream without an error or a done', () async {
    final body = StreamController<String>();
    final cancel = Completer<void>();
    final c = client(() => body.stream);
    final chunks = <ServerMessageChunk>[];
    final done = c.streamChat(ask, model: 'm', cancel: cancel.future).listen(chunks.add).asFuture<void>();

    body.add(data({'content': 'Before stop'}));
    await pumpEventQueue();
    cancel.complete();
    await pumpEventQueue();
    body.add(data({'content': 'after stop'}));
    body.add('data: [DONE]\n\n');
    await body.close();
    await done;

    expect(chunks.map((c) => c.text), ['Before stop']);
    expect(chunks.single.type, MessageChunkType.data);
  });

  test('listModels reads the OpenAI model list; 401 is unauthorized', () async {
    final ok = PhoneLlmClient(
      baseUrl: 'http://127.0.0.1:8081/v1/',
      client: MockClient((request) async {
        expect(request.url.toString(), 'http://127.0.0.1:8081/v1/models');
        return http.Response(
            jsonEncode({
              'object': 'list',
              'data': [
                {'id': 'small-q4', 'object': 'model'},
                {'id': '', 'object': 'model'},
              ],
            }),
            200);
      }),
    );
    expect(await ok.listModels(), ['small-q4']);

    final refused = PhoneLlmClient(
        baseUrl: 'http://127.0.0.1:8081/v1',
        client: MockClient((_) async {
          return http.Response('', 401);
        }));
    await expectLater(
      refused.listModels(),
      throwsA(isA<PhoneLlmException>().having((e) => e.code, 'code', PhoneLlmError.unauthorized)),
    );
  });

  test('Test asks for one token and reports the round trip', () async {
    late Map body;
    final c = PhoneLlmClient(
      baseUrl: 'http://127.0.0.1:8081/v1',
      client: MockClient((request) async {
        body = jsonDecode(request.body) as Map;
        return http.Response('{"choices":[]}', 200);
      }),
    );
    expect(await c.test('small-q4'), isA<Duration>());
    expect(body['max_tokens'], 1);
    expect(body['model'], 'small-q4');
  });

  test('parseSseLine ignores comments, blank and non-JSON lines', () {
    expect(PhoneLlmClient.parseSseLine(''), isNull);
    expect(PhoneLlmClient.parseSseLine(': ping'), isNull);
    expect(PhoneLlmClient.parseSseLine('data: not json'), isNull);
    expect(PhoneLlmClient.parseSseLine('data: [DONE]')!.done, isTrue);
    expect(PhoneLlmClient.parseSseLine('error: {"message":"x"}')!.error, isTrue);
  });
}
