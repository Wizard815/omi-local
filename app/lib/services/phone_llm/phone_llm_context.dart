import 'package:omi/backend/schema/conversation.dart';
import 'package:omi/backend/schema/message.dart';

/// Builds what the phone model reads for one question: a short system prompt, the newest chat
/// turns and, when the user allows it, titles and overviews of a few recent conversations the app
/// already keeps — all within [tokenBudget] for a small (about 4k) context.
///
/// Order of what is dropped first when it does not fit: older conversations, then older chat
/// turns. The system prompt and the question always go.
class PhoneLlmContext {
  const PhoneLlmContext({this.tokenBudget = 2500, this.maxTurns = 12, this.maxConversations = 5});

  final int tokenBudget;
  final int maxTurns;
  final int maxConversations;

  /// At most this share of the budget left after the prompt and question goes to conversations.
  static const conversationShare = 0.4;

  /// Longest overview kept per conversation, in characters.
  static const overviewChars = 400;

  /// A rough count that errs high for English (about four characters a token) plus framing.
  static int estimateTokens(String text) => (text.length / 4).ceil() + 4;

  static String systemPrompt(DateTime now) =>
      'You are Omi, a personal assistant. You are running on the user\'s phone, without access to '
      'their Omi server, memories, tasks or tools. Answer briefly and plainly. If a question needs '
      'something you cannot see, say so. Today is ${now.toIso8601String().substring(0, 10)}.';

  List<Map<String, String>> build({
    required String question,
    required List<ServerMessage> history,
    List<ServerConversation> conversations = const [],
    DateTime? now,
  }) {
    final system = StringBuffer(systemPrompt(now ?? DateTime.now()));
    var left = tokenBudget - estimateTokens(system.toString()) - estimateTokens(question);

    final notes = <String>[];
    var notesBudget = (left * conversationShare).floor();
    for (final c in conversations) {
      if (notes.length == maxConversations) break;
      if (c.discarded || c.deleted) continue;
      final title = c.structured.title.trim();
      var overview = c.structured.overview.trim();
      if (title.isEmpty && overview.isEmpty) continue;
      if (overview.length > overviewChars) overview = '${overview.substring(0, overviewChars)}…';
      final day = (c.startedAt ?? c.createdAt).toLocal().toIso8601String().substring(0, 10);
      final note = '- $day ${title.isEmpty ? '' : '$title: '}$overview';
      final cost = estimateTokens(note);
      if (cost > notesBudget) break;
      notesBudget -= cost;
      notes.add(note);
    }
    if (notes.isNotEmpty) {
      const header = '\n\nRecent conversations the user recorded (newest first):\n';
      system
        ..write(header)
        ..write(notes.join('\n'));
      left -= estimateTokens(header + notes.join('\n'));
    }

    // Newest turns first until the budget runs out; failed and empty replies are skipped.
    final turns = <Map<String, String>>[];
    for (final m in history.reversed) {
      if (turns.length == maxTurns) break;
      final text = m.text.trim();
      if (text.isEmpty) continue;
      final cost = estimateTokens(text);
      if (cost > left) break;
      left -= cost;
      turns.insert(0, {'role': m.sender == MessageSender.human ? 'user' : 'assistant', 'content': text});
    }
    // A history that starts with an answer confuses some chat templates.
    while (turns.isNotEmpty && turns.first['role'] == 'assistant') {
      turns.removeAt(0);
    }

    return [
      {'role': 'system', 'content': system.toString()},
      ...turns,
      {'role': 'user', 'content': question},
    ];
  }
}
