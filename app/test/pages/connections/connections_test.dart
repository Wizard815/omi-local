// omi-local Connections, against a fake server:
//  * the Connections page lists the six targets, turns one on, saves an address and a token (the token
//    field is never filled from the server) and shows a connection test's result;
//  * Send to agent sends only the preview on screen: Send is off until "Show what will be sent", and
//    changing the prompt asks for a new preview; the suggested agent can be taken in one tap;
//  * Send to wiki shows exactly what goes and where, and a second copy is a "revised copy";
//  * agent jobs: a Hermes question is answered with Allow once / Deny, a Studio one points to Studio;
//  * Send to Coinpile: a transaction needs an account and an amount, and sends exactly the fields.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/backend/schema/connections.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/connections/agent_jobs_page.dart';
import 'package:omi/pages/connections/connections_page.dart';
import 'package:omi/pages/connections/send_to_agent_sheet.dart';
import 'package:omi/pages/connections/send_to_coinpile_sheet.dart';
import 'package:omi/pages/connections/send_to_wiki_sheet.dart';

import 'fake_connections.dart';

Future<void> _pump(WidgetTester tester, Widget child) async {
  tester.view.physicalSize = const Size(390 * 3, 1400 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    localizationsDelegates: AppLocalizations.localizationsDelegates,
    supportedLocales: AppLocalizations.supportedLocales,
    home: Scaffold(body: child),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('Connections lists every target off, turns the wiki on and saves its token write-only', (tester) async {
    final api = FakeConnections();
    await _pump(tester, ConnectionsPage(api: api));

    for (final t in connectionTargets) {
      expect(find.byKey(Key('connections_card_$t')), findsOneWidget);
    }
    expect(find.text('Off'), findsNWidgets(6));
    expect(find.textContaining('Nothing leaves Omi without your OK'), findsOneWidget);

    await tester.tap(find.byKey(const Key('connections_tile_wiki')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('connections_enabled_wiki')));
    await tester.pumpAndSettle();
    expect(api.updates.last.$1, 'wiki');
    expect(api.updates.last.$2, {'enabled': true});

    await tester.enterText(find.byKey(const Key('connections_wiki_field_url')), 'http://192.168.1.5:27123');
    await tester.enterText(find.byKey(const Key('connections_wiki_field_token')), 'family-token');
    await tester.ensureVisible(find.byKey(const Key('connections_save_wiki')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('connections_save_wiki')));
    await tester.pumpAndSettle();
    final saved = api.updates.last.$2;
    expect(api.updates.last.$1, 'wiki');
    expect(saved['url'], 'http://192.168.1.5:27123');
    expect(saved['token'], 'family-token');
    expect(find.text('On'), findsOneWidget);
    // The token field is cleared after saving and never shows the stored token.
    final token = tester.widget<TextField>(find.byKey(const Key('connections_wiki_field_token')));
    expect(token.controller!.text, isEmpty);
    expect(find.text('A token is saved; leave empty to keep it.'), findsOneWidget);

    await tester.ensureVisible(find.byKey(const Key('connections_test_wiki')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('connections_test_wiki')));
    await tester.pumpAndSettle();
    expect(api.tests, ['wiki']);
    expect(find.text('Connected: You are Wizard (member).'), findsOneWidget);

    await tester.ensureVisible(find.text('When I keep'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('When I keep'));
    await tester.pumpAndSettle();
    expect(api.updates.last.$2, {'send_kept_conversations': 'on_keep'});
  });

  testWidgets('the webhook notice shows when the developer webhook points at the wiki', (tester) async {
    final api = FakeConnections(
        state:
            const ConnectionsState(targets: {'wiki': ConnectionTarget(name: 'wiki')}, conversationWebhookToWiki: true));
    await _pump(tester, ConnectionsPage(api: api));
    expect(find.byKey(const Key('connections_webhook_notice')), findsOneWidget);
  });

  testWidgets('Send to agent sends exactly the preview on screen', (tester) async {
    final api = FakeConnections();
    await _pump(tester, SendToAgentSheet(jobContext: const AgentJobContext(conversationIds: ['conv-1']), api: api));

    expect(
        find.text('For something simple: a quick question, a short summary, a reminder, a look-up.'), findsOneWidget);
    expect(find.textContaining('For a job: research, a plan, several steps.'), findsOneWidget);
    final send = find.byKey(const Key('send_to_agent_send'));
    await tester.enterText(find.byKey(const Key('send_to_agent_prompt')), 'How long?');
    await tester.pump();
    await tester.tap(send);
    await tester.pumpAndSettle();
    expect(api.created, isEmpty, reason: 'no Send before the preview');

    await tester.tap(find.byKey(const Key('send_to_agent_show_preview')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('send_to_agent_preview')), findsOneWidget);
    expect(find.textContaining('Context from Omi'), findsOneWidget);

    // A new prompt makes the preview stale: Send is off again until it is refreshed.
    await tester.enterText(find.byKey(const Key('send_to_agent_prompt')),
        'Research brackets, compare three with prices and write a shopping list');
    await tester.pump();
    expect(find.byKey(const Key('send_to_agent_preview')), findsNothing);
    await tester.tap(send);
    await tester.pumpAndSettle();
    expect(api.created, isEmpty);

    await tester.tap(find.byKey(const Key('send_to_agent_show_preview')));
    await tester.pumpAndSettle();
    expect(find.text('Hermes fits this better.'), findsOneWidget);
    await tester.tap(find.byKey(const Key('send_to_agent_use_suggested')));
    await tester.pump();
    await tester.tap(find.byKey(const Key('send_to_agent_show_preview')));
    await tester.pumpAndSettle();
    expect(api.previews.last.target, 'hermes');
    await tester.tap(send);
    await tester.pumpAndSettle();
    expect(api.created.single.target, 'hermes');
    expect(api.created.single.prompt, startsWith('Research brackets'));
    expect(api.created.single.attach, isTrue);
  });

  testWidgets('Send to wiki shows what goes where, and a second copy is a revised copy', (tester) async {
    final api = FakeConnections();
    Future<void> open() async {
      await tester.tap(find.byKey(const Key('open_sheet')));
      await tester.pumpAndSettle();
    }

    await _pump(
      tester,
      Builder(
        builder: (context) => TextButton(
          key: const Key('open_sheet'),
          onPressed: () => showSendToWikiSheet(context, 'conv-1', api: api),
          child: const Text('open'),
        ),
      ),
    );
    await open();
    expect(find.text('Conversation - Garage shelves (conv-1)'), findsOneWidget);
    expect(find.textContaining('raw/sources/2026-11-16'), findsOneWidget);
    expect(find.textContaining('Planned shelves.'), findsOneWidget);
    await tester.tap(find.byKey(const Key('send_to_wiki_send')));
    await tester.pumpAndSettle();
    expect(api.sends.single, (id: 'conv-1', revised: false));

    api.wikiPreview = OutboundPreview(
      title: 'Conversation - Garage shelves (conv-1) (revised 1)',
      content: 'x',
      receipts: [OutboundReceipt(target: 'wiki', sentAt: DateTime(2026, 11, 16, 9))],
    );
    await tester.pump(const Duration(seconds: 5));
    await open();
    expect(find.byKey(const Key('send_to_wiki_already_sent')), findsOneWidget);
    expect(find.text('Send revised copy'), findsOneWidget);
    await tester.tap(find.byKey(const Key('send_to_wiki_send')));
    await tester.pumpAndSettle();
    expect(api.sends.last, (id: 'conv-1', revised: true));
  });

  testWidgets('a Hermes question is answered here, a Studio one in Studio', (tester) async {
    final api = FakeConnections(jobs: const [
      AgentJob(
        id: 'h1',
        target: 'hermes',
        title: 'Clean up',
        status: 'waiting_for_approval',
        approval: AgentJobApproval(
            requestId: 'req-1',
            command: 'rm -rf /tmp/scratch',
            description: 'Clean scratch',
            choices: ['once', 'deny']),
      ),
      AgentJob(
        id: 's1',
        target: 'studio',
        title: 'Refactor',
        status: 'waiting_for_approval',
        approval: AgentJobApproval(requestId: 'x', description: 'Studio is waiting for you: answer it in Studio'),
      ),
      AgentJob(id: 'o1', target: 'odysseus', title: 'How long?', status: 'completed', result: 'Use 120 cm boards.'),
    ]);
    await _pump(tester, AgentJobsPage(api: api));
    expect(find.text('rm -rf /tmp/scratch'), findsOneWidget);
    expect(find.text('Use 120 cm boards.'), findsOneWidget);
    expect(find.text('Answer this in Studio; Omi shows the result when it finishes.'), findsOneWidget);
    expect(find.byKey(const Key('agent_job_allow_s1')), findsNothing);
    await tester.tap(find.byKey(const Key('agent_job_allow_h1')));
    await tester.pumpAndSettle();
    expect(api.answers, [('h1', 'once')]);
    expect(find.byKey(const Key('agent_job_allow_h1')), findsNothing, reason: 'the job runs again');
  });

  testWidgets('Send to Coinpile: a transaction needs an account and sends exactly the fields', (tester) async {
    final api = FakeConnections();
    await _pump(tester, SendToCoinpileSheet(conversationId: 'c1', api: api));
    expect(find.text('Plumber'), findsOneWidget);
    await tester.tap(find.text('Transaction'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('send_to_coinpile_send')));
    await tester.pumpAndSettle();
    expect(api.coinpile, isEmpty);
    expect(find.text('Pick an account and enter an amount'), findsOneWidget);

    await tester.tap(find.byKey(const Key('send_to_coinpile_account')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cash (cash)').last);
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('send_to_coinpile_amount')), '-120');
    await tester.tap(find.byKey(const Key('send_to_coinpile_send')));
    await tester.pumpAndSettle();
    expect(api.coinpile.single, {
      'kind': 'transaction',
      'conversation_id': 'c1',
      'title': 'Plumber',
      'body': 'Paid the plumber \$120 cash.',
      'account_id': 'ff:1',
      'amount': -120.0,
      'description': 'Plumber',
    });
  });
}
