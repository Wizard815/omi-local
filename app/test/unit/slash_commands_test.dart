import 'package:flutter_test/flutter_test.dart';

import 'package:omi/utils/chat/slash_commands.dart';

const _texts = SlashCommandTexts(
  sendWithShortcut: _send,
  toggleShortcut: _toggle,
  model: 'model',
  clear: 'clear',
  apps: 'apps',
  review: 'review',
  help: 'help',
);

String _send(String name, String? model) => 'send $name ${model ?? '-'}';
String _toggle(String name, bool active) => active ? 'off $name' : 'on $name';

void main() {
  group('parseSlashCommand', () {
    const names = ['adult', 'code'];

    test('a shortcut with a message answers that message with the shortcut', () {
      final c = parseSlashCommand('/adult  what did Sam say?', names)!;
      expect(c.kind, SlashCommandKind.sendWithShortcut);
      expect(c.shortcut, 'adult');
      expect(c.message, 'what did Sam say?');
      expect(parseSlashCommand('/ADULT hi', names)!.shortcut, 'adult');
    });

    test('toggle / on / off keep the shortcut on or turn it off', () {
      expect(parseSlashCommand('/adult toggle', names)!.kind, SlashCommandKind.toggleShortcut);
      expect(parseSlashCommand('/adult toggle', names)!.turnOn, isNull);
      expect(parseSlashCommand('/adult on', names)!.turnOn, isTrue);
      expect(parseSlashCommand('/adult off', names)!.turnOn, isFalse);
      expect(parseSlashCommand('/adult', names)!.kind, SlashCommandKind.shortcutNeedsArgument);
    });

    test('built-ins run only bare; unknown commands and paths are plain messages', () {
      expect(parseSlashCommand('/model', names)!.kind, SlashCommandKind.openModelSettings);
      expect(parseSlashCommand('/review', names)!.kind, SlashCommandKind.openDayReview);
      expect(parseSlashCommand('/clear the table please', names), isNull);
      expect(parseSlashCommand('/etc/hosts is broken', names), isNull);
      expect(parseSlashCommand('/unknown hi', names), isNull);
      expect(parseSlashCommand('tell me /adult stuff', names), isNull);
      // Without shortcuts (cloud backend) "/adult x" is just text.
      expect(parseSlashCommand('/adult hi', const []), isNull);
    });
  });

  group('slashCommandSuggestions', () {
    const shortcuts = {'adult': 'llama/big', 'code': null};

    List<String> usages(String input, {String? active}) => slashCommandSuggestions(
          input,
          shortcuts: shortcuts,
          activeShortcut: active,
          texts: _texts,
        ).map((s) => s.usage).toList();

    test('"/" lists every command; typing narrows it', () {
      expect(usages('/'), [
        '/adult <message>',
        '/adult toggle',
        '/code <message>',
        '/code toggle',
        '/model',
        '/review',
        '/apps',
        '/clear',
        '/help',
      ]);
      expect(usages('/ad'), ['/adult <message>', '/adult toggle']);
      expect(usages('/m'), ['/model']);
    });

    test('once the message is being typed the popup steps aside, except the toggle hint', () {
      expect(usages('/adult what'), isEmpty);
      expect(usages('/adult tog'), ['/adult toggle']);
      expect(usages('hello /'), isEmpty);
    });

    test('rows describe the current state', () {
      final rows = slashCommandSuggestions('/adult', shortcuts: shortcuts, activeShortcut: 'adult', texts: _texts);
      expect(rows.map((r) => r.description), ['send adult llama/big', 'off adult']);
      expect(rows.first.insert, '/adult ');
      expect(rows.last.runsImmediately, isTrue);
    });
  });
}
