// omi-local Connections: where Omi may send things (the family wiki, Odysseus, Hermes, Studio,
// Coinpile, Pondering Orb), the Send sheets with their exact previews, the agent jobs page, the
// agents' questions in Approvals, and the day review's per-clip keep, tap-through and receipts.
// Synthetic data: Alice's wiki and Odysseus are set up; Hermes is on but has no key yet.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:omi/backend/schema/connections.dart';
import 'package:omi/pages/approvals/approvals_page.dart';
import 'package:omi/pages/connections/agent_jobs_page.dart';
import 'package:omi/pages/connections/connections_page.dart';
import 'package:omi/pages/connections/send_to_agent_sheet.dart';
import 'package:omi/pages/connections/send_to_coinpile_sheet.dart';
import 'package:omi/pages/connections/send_to_wiki_sheet.dart';
import 'package:omi/pages/day_review/day_review_page.dart';

import '../harness.dart';

final _now = DateTime.now();
final _yesterday = DateTime(_now.year, _now.month, _now.day - 1);
String get _date =>
    '${_yesterday.year}-${_yesterday.month.toString().padLeft(2, '0')}-${_yesterday.day.toString().padLeft(2, '0')}';
String _utc(int hour, [int minute = 0, int plusDays = -1]) =>
    DateTime(_now.year, _now.month, _now.day + plusDays, hour, minute).toUtc().toIso8601String();

Map<String, Object?> _target({
  bool enabled = false,
  String url = '',
  bool hasToken = false,
  bool fromEnv = false,
  bool? checked,
  String error = '',
  Map<String, Object?> extra = const {},
}) =>
    {
      'enabled': enabled,
      'url': url,
      'own_url': fromEnv ? '' : url,
      'url_from_env': fromEnv,
      'has_token': hasToken,
      'token_from_env': false,
      'last_check_at': checked == null ? null : _utc(8, 12, 0),
      'last_check_ok': checked,
      'last_check_error': error,
      ...extra,
    };

final Map<String, Object?> _connections = {
  'wiki': _target(
    enabled: true,
    url: 'http://192.168.1.5:27123',
    hasToken: true,
    checked: true,
    extra: {
      'person': '',
      'send_day_reviews': true,
      'send_kept_conversations': 'ask',
      'include_transcript': false,
      'require_manual_approval': true,
    },
  ),
  'odysseus': _target(
    enabled: true,
    url: 'http://192.168.1.9:7000',
    hasToken: true,
    fromEnv: true,
    checked: true,
    extra: {'endpoint_id': 'ep-local-llama', 'model': 'llama-3.3-70b', 'session_id': ''},
  ),
  'hermes': _target(enabled: true, url: 'http://192.168.1.9:8642', fromEnv: true),
  'studio': _target(
    url: 'http://192.168.1.9:8648',
    fromEnv: true,
    extra: {
      'default_agent': 'claude-code',
      'workspace': '',
      'has_webhook_secret': false,
      'webhook_path': '/v1/integrations/studio/events'
    },
  ),
  'coinpile': _target(),
  'orb': _target(extra: {'log_day_approvals': false, 'project': 'General', 'require_manual_approval': true}),
  'conversation_webhook_to_wiki': true,
};

Map<String, Object?> _job(String id, String target, String title, String status,
        {String result = '', Map<String, Object?>? approval, int hour = 9}) =>
    {
      'id': id,
      'target': target,
      'title': title,
      'prompt': '$title\n\n---\nContext from Omi:\n\n## Conversation: Garage shelves ($_date 08:10)\n\n'
          'Planned shelves for the garage; need a cut list.',
      'agent_id': target == 'studio' ? 'claude-code' : '',
      'status': status,
      'external_id': '',
      'result': result,
      'error': '',
      'approval': approval,
      'conversation_ids': ['conv-garage'],
      'attach_result': true,
      'created_at': _utc(hour, 5, 0),
      'updated_at': _utc(hour, 6, 0),
    };

final _hermesAsking = _job('h1', 'hermes', 'Make a cut list and order the boards', 'waiting_for_approval', approval: {
  'request_id': 'req-1',
  'command': 'python3 cutlist.py --boards 3 --order hornbach',
  'description': 'Run the cut-list script and place the order',
  'choices': ['once', 'deny'],
});

final List<Map<String, Object?>> _jobs = [
  _hermesAsking,
  _job('s1', 'studio', 'Write a cut-list script with a test', 'running', hour: 8),
  _job('o1', 'odysseus', 'How long should garage shelf boards be?', 'completed',
      result: '120 cm boards fit the alcove; three shelves 35 cm apart leave room for the bins.', hour: 7),
];

Map<String, Object?> _clip(String id, int h, int m, String title, {bool starred = false, String overview = ''}) => {
      'id': id,
      'title': title,
      'emoji': '',
      'overview': overview,
      'started_at': _utc(h, m),
      'finished_at': _utc(h, m + 12),
      'starred': starred,
      'tags': <String>[],
      'rolled_up': !starred,
      'dropped': false,
      'held': false,
      'hold_rule': '',
      'transcript_snippet': '',
      'word_count': 120,
    };

Map<String, Object?> _section(String id, String title, String text, List<String> ids) =>
    {'id': id, 'title': title, 'text': text, 'tags': <String>[], 'conversation_ids': ids, 'favorited': false};

Map<String, Object?> _day() {
  final sections = [
    _section('s1', 'Garage shelves',
        'At 08:10 you measured the alcove and planned three shelves; the boards should be 120 cm.', ['c1', 'c2']),
    _section('s2', 'Plumber', 'The plumber fixes the sink on Thursday for 120 in cash.', ['c3']),
  ];
  final text = sections.map((s) => s['text']).join('\n\n');
  return {
    'date': _date,
    'conversations': [
      _clip('c1', 8, 10, 'Measuring the alcove', overview: 'Three shelves, 120 cm boards.'),
      _clip('c2', 8, 40, 'Hardware store call', overview: 'Boards in stock; delivery Friday.'),
      _clip('c3', 11, 5, 'Plumber visit', overview: 'Thursday, 120 cash.'),
      _clip('c4', 19, 30, 'Dinner with Sam', starred: true, overview: 'Sam starts the quilt on Sunday.'),
    ],
    'processing_count': 0,
    'dropped_auto_count': 0,
    'hold_enabled': true,
    'review': {
      'date': _date,
      'status': 'finalized',
      'draft': false,
      'title': 'Shelves planned, the plumber booked',
      'summary_text': text,
      'final_text': text,
      'corrections': <Object?>[],
      'sections': sections,
      'tags': <String>[],
      'kept_conversation_ids': ['c4'],
      'rolled_up_conversation_ids': ['c1', 'c2', 'c3'],
      'merged_groups': <Object?>[],
      'dropped_conversation_ids': <Object?>[],
      'generated_at': _utc(3, 0, 0),
      'generation_count': 1,
      'approved_at': _utc(7, 50, 0),
      'time_zone': 'UTC',
      'outbound_receipts': [
        {
          'target': 'wiki',
          'sent_at': _utc(8, 0, 0),
          'title': 'Omi day review',
          'revision': 0,
          'detail': 'Added people/alice/raw/sources/$_date - Omi day review.md (status: unprocessed)',
          'error': '',
        },
        {
          'target': 'orb',
          'sent_at': null,
          'title': '',
          'revision': 0,
          'detail': '',
          'error': 'The day was approved automatically, and this connection sends only days you approve',
        },
      ],
    },
  };
}

const _waiting = {
  'tasks': <Object?>[],
  'task_count': 0,
  'event_count': 0,
  'events': <Object?>[],
  'draft_dates': <Object?>[],
  'today': '',
  'auto_approve': false,
  'auto_approve_time': '21:00',
};

final connectionsScenarios = <AuditScenario>[
  AuditScenario(
    id: 'connections-page',
    title: 'Connections: six targets, the wiki and Odysseus on, Hermes needs its key',
    page: 'lib/pages/connections/connections_page.dart (ConnectionsPage)',
    state: 'Wiki on and tested; Odysseus on from the server defaults; Hermes on without a key; the developer '
        'webhook points at the same wiki',
    run: (a) async {
      a.server.jsonRoutes['GET /v1/integrations/outbound'] = _connections;
      await a.pump(const ConnectionsPage(), scaffold: false);
      await a.shot('Open Settings > Connections', step: 'top');
      await a.tap(find.byKey(const Key('connections_tile_wiki')));
      await a.shot('Open the family wiki card: switches, kept-clip choice, address and token', step: 'wiki');
      await a.tester.drag(find.byType(ListView).first, const Offset(0, -700));
      await a.settle();
      await a.shot('Scroll: transcript choice, wiki person, Save and Test connection', step: 'wiki-bottom');
    },
  ),
  AuditScenario(
    id: 'send-to-agent-sheet',
    title: 'Send to agent: pick the agent, edit the prompt, see exactly what leaves',
    page: 'lib/pages/connections/send_to_agent_sheet.dart (SendToAgentSheet)',
    state: 'A conversation about garage shelves; the preview for Hermes suggests Hermes fits; Send enabled',
    run: (a) async {
      const prompt = 'Make a cut list for three shelves and draft the hardware store order.';
      a.server.jsonRoutes['POST /v1/agent-jobs/preview'] = {
        'target': 'odysseus',
        'destination': 'http://192.168.1.9:7000',
        'content': '$prompt\n\n---\nContext from Omi:\n\n## Conversation: Garage shelves ($_date 08:10)\n\n'
            'Planned shelves for the garage; need a cut list.\n\nAction items:\n- Buy 3 boards',
        'suggested_target': 'hermes',
        'title': 'Garage shelves',
      };
      await a.pumpHost((context) =>
          showSendToAgentSheet(context, jobContext: const AgentJobContext(conversationIds: ['conv-garage'])));
      await a.shot('Open Send to agent… from a conversation', step: 'open');
      await a.enterText(find.byKey(const Key('send_to_agent_prompt')), prompt);
      await a.tap(find.byKey(const Key('send_to_agent_show_preview')));
      await a.shot('Show what will be sent: the exact text, and a hint that Hermes fits a job', step: 'preview');
    },
  ),
  AuditScenario(
    id: 'send-to-wiki-sheet',
    title: 'Send to wiki: exactly what is filed and where; already sent once',
    page: 'lib/pages/connections/send_to_wiki_sheet.dart (SendToWikiSheet)',
    state: 'The conversation was sent yesterday; Send files a revised copy',
    run: (a) async {
      a.server.jsonRoutes['GET /v1/conversations/conv-garage/send/preview'] = {
        'target': 'wiki',
        'title': 'Conversation - Garage shelves (conv-gar) (revised 1)',
        'content': '**From:** Omi conversation, $_date 08:10 to 08:22 (omi://conversation/conv-garage)\n\n'
            '## Overview\n\nPlanned shelves for the garage; need a cut list.\n\n## Action items\n\n- [ ] Buy 3 boards',
        'destination':
            'http://192.168.1.5:27123 : raw/sources/$_date - Conversation - Garage shelves (conv-gar) (revised 1).md',
        'receipts': [
          {'target': 'wiki', 'sent_at': _utc(9, 0), 'title': 'Conversation - Garage shelves (conv-gar)', 'revision': 0}
        ],
      };
      await a.pumpHost((context) => showSendToWikiSheet(context, 'conv-garage'));
      await a.shot('Send to wiki from the conversation menu');
    },
  ),
  AuditScenario(
    id: 'send-to-coinpile-sheet',
    title: 'Send to Coinpile: a transaction with an account; Coinpile asks again',
    page: 'lib/pages/connections/send_to_coinpile_sheet.dart (SendToCoinpileSheet)',
    state: 'Drafted from the plumber conversation; transaction chosen; one Coinpile account',
    run: (a) async {
      a.server.jsonRoutes['GET /v1/integrations/coinpile/draft'] = {
        'kind': 'note',
        'title': 'Plumber visit',
        'body': 'The plumber fixes the sink on Thursday for 120 in cash.',
      };
      a.server.jsonRoutes['GET /v1/integrations/coinpile/accounts'] = {
        'accounts': [
          {'id': 'ff:1', 'name': 'Cash', 'type': 'cash'}
        ],
        'error': ''
      };
      await a.pumpHost((context) => showSendToCoinpileSheet(context, conversationId: 'c3'));
      await a.shot('Send to Coinpile: a note drafted from the conversation', step: 'note');
      await a.tap(find.text('Transaction'));
      await a.enterText(find.byKey(const Key('send_to_coinpile_amount')), '-120');
      await a.shot('Switch to a transaction: account, amount, date', step: 'transaction');
    },
  ),
  AuditScenario(
    id: 'agent-jobs-page',
    title: 'Agent jobs: Hermes asks before it runs a command, Studio working, Odysseus answered',
    page: 'lib/pages/connections/agent_jobs_page.dart (AgentJobsPage)',
    state: "Hermes waits for Allow once / Deny; Studio's coding agent is working; Odysseus' answer is in",
    run: (a) async {
      a.server.jsonRoutes['GET /v1/agent-jobs'] = {'jobs': _jobs};
      await a.pump(const AgentJobsPage(), scaffold: false);
      await a.shot('Open Agent jobs');
    },
  ),
  AuditScenario(
    id: 'approvals-agents-asking',
    title: "Waiting for your OK: an agent's question next to the tasks",
    page: 'lib/pages/approvals/approvals_page.dart (ApprovalsPage)',
    state: "Nothing else waits; Hermes asks before it runs the cut-list script; Studio's agent asks to merge",
    run: (a) async {
      a.server.jsonRoutes['GET /v1/approvals'] = _waiting;
      a.server.jsonRoutes['GET /v1/agent-jobs'] = {'jobs': _jobs};
      a.server.jsonRoutes['GET /v1/agent-jobs/studio/approvals'] = {
        'approvals': [
          {
            'id': 'aw-1',
            'title': 'Merge the cut-list script',
            'agent': 'claude-code',
            'summary': 'Tests pass; ready to merge into main.',
            'status': 'awaiting_approval',
            'job_title': 'Write a cut-list script',
          }
        ],
        'error': '',
      };
      await a.pump(const ApprovalsPage(), scaffold: false);
      await a.shot('Open Waiting for your OK');
    },
  ),
  AuditScenario(
    id: 'day-review-clips-keep-send',
    title: 'Approved day: sent to the wiki, Orb skipped, clips open and keep one by one',
    page: 'lib/pages/day_review/day_review_page.dart (DayReviewPage)',
    state: "Wiki receipt; Orb not sent (auto-approved); the shelves section's clips unfolded with keep stars; "
        'the kept dinner offers Send to wiki (Offer chosen)',
    run: (a) async {
      a.server.jsonRoutes['GET /v1/users/day-reviews/$_date'] = _day();
      a.server.jsonRoutes['GET /v1/integrations/outbound'] = _connections;
      a.server.jsonRoutes['GET /v1/approvals'] = _waiting;
      await a.pump(DayReviewPage(date: _date), scaffold: false);
      await a.tap(find.byKey(const Key('day_review_section_clips_s1')));
      await a.shot('Open the approved day and unfold the shelves section', step: 'clips');
      await a.tap(find.text('Kept as conversations (1)'));
      await a.shot('The kept clip offers Send to wiki', step: 'kept');
    },
  ),
];
