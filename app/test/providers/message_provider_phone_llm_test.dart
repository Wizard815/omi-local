/// Chat answered by this phone's own model (lib/services/phone_llm): the answer streams, is
/// labelled, never reaches the server, survives a history reload, stops, and "Ask this phone
/// instead" works on a failed server reply.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/backend/http/api/chat_sessions.dart';
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/preferences.dart';
import 'package:omi/backend/schema/chat_session.dart';
import 'package:omi/backend/schema/message.dart';
import 'package:omi/providers/message_provider.dart';
import 'package:omi/services/phone_llm/phone_llm_client.dart';
import 'package:omi/services/phone_llm/phone_llm_router.dart';
import 'package:omi/services/phone_llm/phone_llm_settings.dart';

class _Sessions extends ChatSessionsApi {
  int creates = 0;
  final titles = <String>[];
  List<ServerMessage> history = [];

  @override
  Future<ApiResult<ChatSessionSummary>> create() async {
    creates++;
    return ApiSuccess(ChatSessionSummary(id: 'new', title: '', updatedAt: DateTime.utc(2026)));
  }

  @override
  Future<ApiResult<List<ServerMessage>>> messages(String id, {int offset = 0, int limit = 100}) async =>
      ApiSuccess(history);

  @override
  Future<ApiResult<String>> title(String id, List<ServerMessage> messages) async {
    titles.add(id);
    return const ApiSuccess('Title');
  }
}

String _sse(String content, {bool done = true}) => [
      'data: ${jsonEncode({
            'choices': [
              {
                'delta': {'content': content},
              },
            ],
          })}\n\n',
      if (done) 'data: [DONE]\n\n',
    ].join();

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  late _Sessions sessions;
  late List<String> serverSends;
  late List<Map> phoneRequests;
  late Stream<String> Function() phoneBody;
  late int phoneStatus;

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await SharedPreferencesUtil.init();
    sessions = _Sessions();
    serverSends = [];
    phoneRequests = [];
    phoneStatus = 200;
    phoneBody = () => Stream.value(_sse('From the phone.'));
  });

  Future<MessageProvider> provider(
    PhoneLlmMode mode, {
    String model = 'small-q4',
    bool serverFails = false,
    ServerReach server = ServerReach.up,
  }) async {
    final settings = PhoneLlmSettings(secrets: MemorySecretStore('k'));
    await settings.ensureLoaded();
    await settings.setMode(mode);
    await settings.setModel(model);
    final p = MessageProvider(sessionsApi: sessions)
      ..chatSessionId = 's1'
      ..replyStreamOverride = (text, {appId, filesId, context, chatSessionId}) async* {
        serverSends.add(text);
        if (serverFails) throw const SocketException('Connection refused');
        yield ServerMessageChunk(
          'srv',
          '',
          MessageChunkType.done,
          message: ServerMessage(
              'srv-1', DateTime.now(), 'From the server.', MessageSender.ai, MessageType.text, null, false, [], [], []),
        );
      }
      ..phoneLlm = PhoneLlmRouter(
        settings: settings,
        server: () async => server,
        phone: (_) async => PhoneHealth.running,
        clientFor: (s) => PhoneLlmClient(
          baseUrl: s.baseUrl,
          apiKey: s.apiKey,
          client: MockClient.streaming((request, _) async {
            phoneRequests.add(jsonDecode((request as http.Request).body) as Map);
            return http.StreamedResponse(phoneBody().transform(utf8.encoder), phoneStatus);
          }),
        ),
      );
    return p;
  }

  Future<void> send(MessageProvider p, String text) async {
    p.addMessageLocally(text);
    await p.sendMessageStreamToServer(text);
  }

  test('This phone: the answer streams from the phone, is labelled and never reaches the server', () async {
    final p = await provider(PhoneLlmMode.phone);
    await send(p, 'What is on today?');

    final reply = p.messages.last;
    expect(reply.text, 'From the phone.');
    expect(p.isReplyFailed(reply), isFalse);
    expect(p.phoneAnswerModel(reply), 'small-q4');
    expect(serverSends, isEmpty, reason: 'the question is not sent to the server');
    expect(sessions.creates, 0);
    expect(sessions.titles, isEmpty, reason: 'server-only follow-ups (chat naming) are skipped');
    final asked = phoneRequests.single['messages'] as List;
    expect(asked.last, {'role': 'user', 'content': 'What is on today?'});
    expect(asked.where((m) => m['content'] == 'What is on today?'), hasLength(1), reason: 'asked once, not twice');
    expect(p.sendingMessage, isFalse);
  });

  test('a history reload from the server keeps the phone exchange while Omi runs', () async {
    final p = await provider(PhoneLlmMode.phone);
    await send(p, 'Hello phone');
    sessions.history = [
      ServerMessage('old', DateTime(2020), 'Earlier on the server', MessageSender.human, MessageType.text, null, false,
          [], [], []),
    ];

    await p.refreshMessages();

    expect(p.messages.map((m) => m.text), ['Earlier on the server', 'Hello phone', 'From the phone.']);
    expect(p.phoneAnswerModel(p.messages.last), 'small-q4');
  });

  test('Server mode stays on the server and is unlabelled', () async {
    final p = await provider(PhoneLlmMode.server);
    await send(p, 'hi');
    expect(serverSends, ['hi']);
    expect(phoneRequests, isEmpty);
    expect(p.phoneAnswerModel(p.messages.last), isNull);
    expect(sessions.titles, ['s1']);
  });

  test('Auto: an unreachable server goes to the phone; a slow one never does', () async {
    final down = await provider(PhoneLlmMode.auto, server: ServerReach.down);
    await send(down, 'offline question');
    expect(serverSends, isEmpty);
    expect(down.messages.last.text, 'From the phone.');

    final slow = await provider(PhoneLlmMode.auto, server: ServerReach.slow);
    await send(slow, 'slow question');
    expect(serverSends, ['slow question']);
    expect(phoneRequests, hasLength(1), reason: 'only the offline question went to the phone');
  });

  test('Stop ends a phone answer and keeps what was shown, labelled', () async {
    final hold = StreamController<String>();
    phoneBody = () => hold.stream;
    final p = await provider(PhoneLlmMode.phone);
    p.addMessageLocally('long one');
    final turn = p.sendMessageStreamToServer('long one');
    await pumpEventQueue();
    hold.add(_sse('Partial answer', done: false));
    await Future<void>.delayed(const Duration(milliseconds: 150)); // let the text flush
    expect(p.canStopReply, isTrue);

    await p.stopReply();
    await turn;

    final reply = p.messages.last;
    expect(reply.text, 'Partial answer');
    expect(p.isReplyFailed(reply), isFalse);
    expect(p.phoneAnswerModel(reply), 'small-q4');
    expect(p.canStopReply, isFalse);
    await hold.close();
  });

  test('Ask this phone instead: a failed server reply is answered by the phone', () async {
    final p = await provider(PhoneLlmMode.server, serverFails: true);
    await send(p, 'Are you there?');

    final failed = p.messages.last;
    expect(p.isReplyFailed(failed), isTrue);
    expect(p.canAskPhone(failed), isTrue);
    expect(p.phoneReplyError(failed), isNull, reason: 'a server failure, not a phone one');

    await p.askPhoneInstead(failed);

    expect(serverSends, ['Are you there?'], reason: 'the server is not asked again');
    expect(p.messages.where((m) => identical(m, failed)), isEmpty);
    expect(p.messages.where((m) => m.sender == MessageSender.human), hasLength(1));
    final answer = p.messages.last;
    expect(answer.text, 'From the phone.');
    expect(p.phoneAnswerModel(answer), 'small-q4');
    expect(p.sendingMessage, isFalse);
  });

  test('no phone model picked: no "Ask this phone instead"', () async {
    final p = await provider(PhoneLlmMode.server, model: '', serverFails: true);
    await send(p, 'hi');
    expect(p.canAskPhone(p.messages.last), isFalse);
  });

  test('a refused API key fails on the phone, says so, and Try Again asks the phone again', () async {
    phoneStatus = 401;
    final p = await provider(PhoneLlmMode.phone);
    await send(p, 'secret?');

    final failed = p.messages.last;
    expect(p.isReplyFailed(failed), isTrue);
    expect(p.phoneReplyError(failed), PhoneLlmError.unauthorized);
    expect(p.canAskPhone(failed), isFalse, reason: 'it already was the phone');

    phoneStatus = 200;
    await p.retryFailedReply(failed);
    expect(phoneRequests, hasLength(2));
    expect(serverSends, isEmpty);
    expect(p.messages.last.text, 'From the phone.');
  });
}
