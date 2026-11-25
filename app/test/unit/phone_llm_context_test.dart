import 'package:flutter_test/flutter_test.dart';

import 'package:omi/backend/schema/conversation.dart';
import 'package:omi/backend/schema/message.dart';
import 'package:omi/backend/schema/structured.dart';
import 'package:omi/services/phone_llm/phone_llm_context.dart';

/// What the phone model reads: a token budget, newest turns first, conversations only when allowed.
void main() {
  final now = DateTime(2026, 11, 25, 12);

  ServerMessage turn(int i, MessageSender sender, String text) =>
      ServerMessage('m$i', DateTime(2026, 11, 25, 9, i), text, sender, MessageType.text, null, false, [], [], []);

  List<ServerMessage> history(int pairs, {int chars = 40}) => [
        for (var i = 0; i < pairs; i++) ...[
          turn(i * 2, MessageSender.human, 'question $i ${'q' * chars}'),
          turn(i * 2 + 1, MessageSender.ai, 'answer $i ${'a' * chars}'),
        ],
      ];

  ServerConversation conversation(String id, String title, String overview, {bool discarded = false}) =>
      ServerConversation(
        id: id,
        createdAt: DateTime(2026, 11, 24),
        startedAt: DateTime(2026, 11, 24),
        structured: Structured(title, overview),
        discarded: discarded,
      );

  int tokens(List<Map<String, String>> prompt) =>
      prompt.fold(0, (sum, m) => sum + PhoneLlmContext.estimateTokens(m['content']!));

  test('system prompt first, the question last, the turns in order between', () {
    final prompt = const PhoneLlmContext().build(question: 'And now?', history: history(2), now: now);
    expect(prompt.first['role'], 'system');
    expect(prompt.first['content'], contains('2026-11-25'));
    expect(prompt.last, {'role': 'user', 'content': 'And now?'});
    expect(prompt.sublist(1, prompt.length - 1).map((m) => m['role']), ['user', 'assistant', 'user', 'assistant']);
    expect(prompt[1]['content'], startsWith('question 0'));
  });

  test('a long chat keeps the newest turns and stays within the budget', () {
    const context = PhoneLlmContext(tokenBudget: 600, maxTurns: 100);
    final prompt = context.build(question: 'Last one?', history: history(40, chars: 120), now: now);
    expect(tokens(prompt), lessThanOrEqualTo(600));
    final kept = prompt.sublist(1, prompt.length - 1);
    expect(kept, isNotEmpty);
    expect(kept.last['content'], startsWith('answer 39'), reason: 'the newest turn always survives');
    expect(kept.any((m) => m['content']!.startsWith('question 0 ')), isFalse, reason: 'the oldest are dropped');
    expect(kept.first['role'], 'user', reason: 'never starts on an answer');
  });

  test('turn cap, and empty or whitespace turns are skipped', () {
    final turns = [...history(10), turn(99, MessageSender.ai, '   ')];
    final prompt = const PhoneLlmContext(maxTurns: 4).build(question: 'q', history: turns, now: now);
    expect(prompt.length, 1 + 4 + 1);
  });

  test('recent conversations only when given, newest first, discarded left out, overviews clipped', () {
    final conversations = [
      conversation('a', 'Standup', 'Ship on Friday. ${'x' * 1000}'),
      conversation('b', 'Junk', 'noise', discarded: true),
      conversation('c', 'Dentist', 'Tuesday at 9.'),
    ];
    final without = const PhoneLlmContext().build(question: 'q', history: const [], now: now);
    expect(without.first['content'], isNot(contains('Recent conversations')));

    final withNotes =
        const PhoneLlmContext().build(question: 'q', history: const [], conversations: conversations, now: now);
    final system = withNotes.first['content']!;
    expect(system, contains('Standup: Ship on Friday.'));
    expect(system, contains('Dentist: Tuesday at 9.'));
    expect(system, isNot(contains('Junk')));
    expect(system.indexOf('Standup'), lessThan(system.indexOf('Dentist')));
    expect(system, isNot(contains('x' * (PhoneLlmContext.overviewChars + 1))));
  });

  test('conversations give way first: they get at most their share of the budget', () {
    final many = [for (var i = 0; i < 50; i++) conversation('c$i', 'Meeting $i', 'o' * 300)];
    const context = PhoneLlmContext(tokenBudget: 800, maxConversations: 50);
    final prompt = context.build(question: 'q', history: history(3), conversations: many, now: now);
    expect(tokens(prompt), lessThanOrEqualTo(800));
    expect(prompt.length, 1 + 6 + 1, reason: 'the chat turns still fit next to the notes');
    expect('Meeting'.allMatches(prompt.first['content']!).length, lessThan(50));
  });
}
