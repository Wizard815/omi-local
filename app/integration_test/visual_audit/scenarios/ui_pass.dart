// omi-local UI pass: states the self-hosted app now meets often and the other scenarios do not
// show. A conversation whose notes a slow local model writes in the background (Home row and the
// open conversation), a chat reply that waits minutes for that model, and the Day reviews list
// (loaded, empty and failed). Synthetic data only.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';

import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/conversation.dart';
import 'package:omi/backend/schema/episode.dart';
import 'package:omi/backend/schema/message.dart';
import 'package:omi/backend/schema/structured.dart';
import 'package:omi/backend/schema/transcript_segment.dart';
import 'package:omi/pages/chat/widgets/ai_message.dart';
import 'package:omi/pages/conversation_detail/conversation_detail_provider.dart';
import 'package:omi/pages/conversation_detail/page.dart';
import 'package:omi/pages/day_review/day_reviews_list_page.dart';
import 'package:omi/providers/conversation_provider.dart';
import 'package:omi/providers/episode_provider.dart';
import 'package:omi/widgets/conversation_bottom_bar.dart' show ConversationTab;

import '../harness.dart';
import 'capture.dart' show HomeFrame;

final _now = DateTime.now();
DateTime _today(int hour, [int minute = 0]) => DateTime(_now.year, _now.month, _now.day, hour, minute);
String _key(DateTime d) => '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

TranscriptSegment _line(String id, double start, String text, {bool mine = false}) => TranscriptSegment(
      id: id,
      text: text,
      speaker: mine ? 'SPEAKER_0' : 'SPEAKER_1',
      isUser: mine,
      personId: null,
      start: start,
      end: start + 9,
      translations: [],
    );

/// A conversation stored at once by a self-hosted server: transcript intact, notes still being
/// written by the local model (`status: processing`, `home_step: writing_notes`).
ServerConversation _writingNotes() => ServerConversation(
      id: 'writing-notes',
      createdAt: _today(9, 12),
      startedAt: _today(9, 12),
      finishedAt: _today(9, 31),
      status: ConversationStatus.processing,
      homeStep: 'writing_notes',
      structured: Structured('Garage shelves with the hardware store', ''),
      transcriptSegments: [
        _line('0', 0, 'How long should the boards be for the alcove?'),
        _line('1', 10, 'One hundred twenty centimetres, three shelves.', mine: true),
        _line('2', 20, 'We have them in stock; delivery is Friday.'),
      ],
    );

ServerMessage _reply(String id, DateTime createdAt, List<String> steps) => ServerMessage(
      id,
      createdAt,
      '',
      MessageSender.ai,
      MessageType.text,
      null,
      false,
      [],
      [],
      [],
      askForNps: false,
    )..thinkings.addAll(steps);

Widget _working(ServerMessage m) => AIMessage(
      message: m,
      sendMessage: (_) {},
      displayOptions: false,
      updateConversation: (_) {},
      setMessageNps: (_, {reason}) {},
      showTypingIndicator: true,
    );

Map<String, Object?> _review(String date, String title) => {
      'date': date,
      'title': title,
      'summary_text': 'A day.',
      'final_text': 'A day.',
    };

final uiPassScenarios = <AuditScenario>[
  AuditScenario(
    id: 'ui-pass-home-writing-notes',
    title: 'Home: a conversation whose notes a slow local model is still writing',
    page: 'lib/pages/home/home_content.dart (HomeFrame)',
    state: 'One self-hosted conversation stored at once (status processing, writing notes) and a finished '
        'one with a long title',
    run: (a) async {
      final done = ServerConversation(
        id: 'done',
        createdAt: _today(7, 40),
        startedAt: _today(7, 40),
        finishedAt: _today(8, 5),
        status: ConversationStatus.completed,
        structured: Structured(
          'Planning the weekend with grandma, the birthday cake order and who picks up Leo from practice',
          'Cake at four; Leo at six.',
          emoji: '\u{1F382}',
        ),
      );
      final history = ConversationProvider(isSignedIn: () => false)
        ..isLoadingConversations = false
        ..processingConversations = [_writingNotes()]
        ..conversations = [done]
        ..groupConversationsByDate();
      final episodes = EpisodeProvider(
        fetchLive: () async => const ApiSuccess(EpisodeRead(enabled: false)),
        fetchPage: (from, to, {cursor}) async => const ApiSuccess(EpisodePageRead(enabled: false, episodes: [])),
      )..debugSetLive(null);
      addTearDown(history.dispose);
      await a.pump(const HomeFrame(), scaffold: false, providers: [
        ChangeNotifierProvider<ConversationProvider>.value(value: history),
        ChangeNotifierProvider<EpisodeProvider>.value(value: episodes),
      ]);
      await a.shot('Home with a row whose notes are being written, and a long title', step: 'rows');
      await a.tester.pumpWidget(const SizedBox.shrink());
      episodes.dispose();
    },
  ),
  AuditScenario(
    id: 'ui-pass-detail-writing-notes',
    title: 'The open conversation while a slow local model writes its notes',
    page: 'lib/pages/conversation_detail/page.dart (ConversationDetailPage)',
    state: 'Status processing with the transcript already stored; Summary tab, then Transcript tab',
    run: (a) async {
      final conversation = _writingNotes();
      Future<void> open(ConversationTab tab) => a.pump(
            ConversationDetailPage(key: ValueKey(tab), conversation: conversation, initialTab: tab),
            providers: [
              ChangeNotifierProvider<ConversationProvider>.value(
                value: ConversationProvider(isSignedIn: () => true)..conversations = [conversation],
              ),
              ChangeNotifierProvider(
                create: (_) =>
                    ConversationDetailProvider()..selectedDate = conversationLocalDayKey(conversation.createdAt),
              ),
            ],
          );
      await open(ConversationTab.summary);
      await a.shot('Summary tab while the notes are written', step: 'summary');
      await open(ConversationTab.transcript);
      await a.shot('Transcript tab: the stored transcript reads already', step: 'transcript');
      await a.tester.pumpWidget(const SizedBox.shrink());
    },
  ),
  AuditScenario(
    id: 'ui-pass-chat-slow-reply',
    title: 'Ask Omi: a reply that waits minutes for a slow local model',
    page: 'lib/pages/chat/widgets/ai_message.dart (AIMessage)',
    state: 'A question sent 3 minutes ago; the server streamed one step, then a keep-alive every 20 s',
    run: (a) async {
      final started = DateTime.now().subtract(const Duration(minutes: 3));
      final slow = _reply('slow', started, [
        'Searching conversations',
        'Still working…',
        'Still working…',
        'Still working…',
        'Still working…',
        'Still working…',
        'Still working…',
      ]);
      final waiting = _reply('waiting', started, []);
      await a.pump(
        ListView(
          padding: const EdgeInsets.all(18),
          children: [
            const Align(alignment: Alignment.centerRight, child: Text('What did the plumber say?')),
            const SizedBox(height: 16),
            _working(slow),
            const SizedBox(height: 32),
            const Align(alignment: Alignment.centerRight, child: Text('And the electrician?')),
            const SizedBox(height: 16),
            _working(waiting),
          ],
        ),
      );
      await a.shot('Two replies still working after 3 minutes', step: 'waiting');
    },
  ),
  AuditScenario(
    id: 'ui-pass-day-reviews-list',
    title: 'Day reviews: days to sort and reviewed days; then a failed load',
    page: 'lib/pages/day_review/day_reviews_list_page.dart (DayReviewsListPage)',
    state: 'Two days waiting to be sorted and two reviewed days; then the pending read fails',
    run: (a) async {
      final yesterday = DateTime(_now.year, _now.month, _now.day - 1);
      final before = DateTime(_now.year, _now.month, _now.day - 2);
      final older = DateTime(_now.year, _now.month, _now.day - 3);
      a.server.jsonRoutes['GET /v1/users/day-reviews/pending'] = {
        'today': _key(_now),
        'pending_dates': [_key(before), _key(yesterday)],
        'draft_dates': <String>[],
      };
      a.server.jsonRoutes['GET /v1/users/day-reviews'] = {
        'reviews': [
          _review(_key(older), 'A dentist, a practice and grandma\'s 80th birthday at Luigi\'s'),
          _review(_key(DateTime(_now.year, _now.month, _now.day - 4)), 'Shelves planned'),
        ],
      };
      await a.pump(const DayReviewsListPage());
      await a.shot('Day reviews with days to sort and reviewed days', step: 'loaded');
      a.server.jsonRoutes.remove('GET /v1/users/day-reviews/pending');
      a.server.failNext('GET', '/v1/users/day-reviews/pending', status: 500);
      await a.pump(const DayReviewsListPage(key: ValueKey('failed')));
      await a.shot('The read fails: say so and offer Try Again', step: 'failed');
    },
  ),
];
