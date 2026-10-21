/// Slash commands typed in the Ask Omi chat input.
///
/// Pure (no Flutter, no I/O) so the parsing and the "/" popup's suggestions are
/// unit-tested; the chat page maps each [SlashCommandKind] to its action.
library;

enum SlashCommandKind {
  /// `/adult <message>` — send one message with that shortcut's model.
  sendWithShortcut,

  /// `/adult toggle` (`on` / `off`) — keep using the shortcut's model.
  toggleShortcut,

  /// `/adult` with nothing after it.
  shortcutNeedsArgument,

  /// `/model` — pick the models behind the shortcuts.
  openModelSettings,

  /// `/clear` — clear this chat.
  clearChat,

  /// `/apps` — the chat apps drawer.
  openApps,

  /// `/review` — sort today's conversations (end-of-day review).
  openDayReview,

  /// `/help` — list the commands.
  help,
}

/// The app's own commands. A server-side shortcut may not reuse these names.
const builtInSlashCommands = <String, SlashCommandKind>{
  'model': SlashCommandKind.openModelSettings,
  'clear': SlashCommandKind.clearChat,
  'apps': SlashCommandKind.openApps,
  'review': SlashCommandKind.openDayReview,
  'help': SlashCommandKind.help,
};

class ParsedSlashCommand {
  final SlashCommandKind kind;

  /// The shortcut a shortcut command refers to (e.g. `adult`).
  final String? shortcut;

  /// The message to send (for [SlashCommandKind.sendWithShortcut]).
  final String? message;

  /// For [SlashCommandKind.toggleShortcut]: true = on, false = off, null = flip.
  final bool? turnOn;

  const ParsedSlashCommand(this.kind, {this.shortcut, this.message, this.turnOn});
}

final _command = RegExp(r'^\s*/([A-Za-z][A-Za-z0-9_-]{0,23})(?:\s+([\s\S]*))?$');

/// Parse a submitted chat message. Returns null when it is not a known command
/// (a message that merely starts with "/" — e.g. a path — is sent as text).
ParsedSlashCommand? parseSlashCommand(String input, Iterable<String> shortcutNames) {
  final match = _command.firstMatch(input);
  if (match == null) return null;
  final name = match.group(1)!.toLowerCase();
  final rest = (match.group(2) ?? '').trim();

  final builtIn = builtInSlashCommands[name];
  if (builtIn != null) {
    return rest.isEmpty ? ParsedSlashCommand(builtIn) : null;
  }
  if (!shortcutNames.contains(name)) return null;

  switch (rest.toLowerCase()) {
    case '':
      return ParsedSlashCommand(SlashCommandKind.shortcutNeedsArgument, shortcut: name);
    case 'toggle':
      return ParsedSlashCommand(SlashCommandKind.toggleShortcut, shortcut: name);
    case 'on':
      return ParsedSlashCommand(SlashCommandKind.toggleShortcut, shortcut: name, turnOn: true);
    case 'off':
      return ParsedSlashCommand(SlashCommandKind.toggleShortcut, shortcut: name, turnOn: false);
  }
  return ParsedSlashCommand(SlashCommandKind.sendWithShortcut, shortcut: name, message: rest);
}

class SlashCommandSuggestion {
  /// What tapping inserts, e.g. `/adult ` or `/adult toggle`.
  final String insert;

  /// What the row shows, e.g. `/adult <message>`.
  final String usage;
  final String description;

  /// True when tapping should run the command right away (no message to type).
  final bool runsImmediately;

  const SlashCommandSuggestion({
    required this.insert,
    required this.usage,
    required this.description,
    this.runsImmediately = false,
  });
}

/// Localized wording for the popup rows; the chat page fills it from l10n.
class SlashCommandTexts {
  final String Function(String shortcut, String? model) sendWithShortcut;
  final String Function(String shortcut, bool active) toggleShortcut;
  final String model;
  final String clear;
  final String apps;
  final String review;
  final String help;

  const SlashCommandTexts({
    required this.sendWithShortcut,
    required this.toggleShortcut,
    required this.model,
    required this.clear,
    required this.apps,
    required this.review,
    required this.help,
  });
}

/// Rows for the "/" popup, filtered by what has been typed so far.
///
/// Shown only while the input is a single "/word" being typed; once there is a
/// space (the user is writing the message) the popup steps aside, except for
/// a shortcut's own `toggle` hint.
List<SlashCommandSuggestion> slashCommandSuggestions(
  String input, {
  required Map<String, String?> shortcuts,
  required String? activeShortcut,
  required SlashCommandTexts texts,
}) {
  if (!input.startsWith('/') || input.contains('\n')) return const [];
  final typed = input.substring(1);
  final spaceAt = typed.indexOf(' ');
  final word = (spaceAt == -1 ? typed : typed.substring(0, spaceAt)).toLowerCase();
  final afterWord = spaceAt == -1 ? null : typed.substring(spaceAt + 1).toLowerCase();

  final rows = <SlashCommandSuggestion>[];
  final names = shortcuts.keys.toList()..sort();
  for (final name in names) {
    if (afterWord == null && name.startsWith(word)) {
      rows.add(SlashCommandSuggestion(
        insert: '/$name ',
        usage: '/$name <message>',
        description: texts.sendWithShortcut(name, shortcuts[name]),
      ));
      rows.add(SlashCommandSuggestion(
        insert: '/$name toggle',
        usage: '/$name toggle',
        description: texts.toggleShortcut(name, activeShortcut == name),
        runsImmediately: true,
      ));
    } else if (afterWord != null && name == word && 'toggle'.startsWith(afterWord) && afterWord.isNotEmpty) {
      rows.add(SlashCommandSuggestion(
        insert: '/$name toggle',
        usage: '/$name toggle',
        description: texts.toggleShortcut(name, activeShortcut == name),
        runsImmediately: true,
      ));
    }
  }
  if (afterWord == null) {
    final builtIns = <String, String>{
      'model': texts.model,
      'review': texts.review,
      'apps': texts.apps,
      'clear': texts.clear,
      'help': texts.help,
    };
    builtIns.forEach((name, description) {
      if (name.startsWith(word)) {
        rows.add(SlashCommandSuggestion(
          insert: '/$name',
          usage: '/$name',
          description: description,
          runsImmediately: true,
        ));
      }
    });
  }
  return rows;
}
