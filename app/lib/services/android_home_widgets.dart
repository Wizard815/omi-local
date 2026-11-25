import 'package:omi/backend/http/api/approvals.dart';
import 'package:omi/backend/schema/family_calendar.dart';
import 'package:omi/backend/schema/schema.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/ui/format/omi_date_format.dart';
import 'package:omi/utils/enums.dart';
import 'package:omi/widgets/extensions/string.dart';

/// What the Android home-screen widgets show (android/app/src/main/kotlin/com/friend/ios/widgets),
/// as the JSON documents they read. Every label is already in the app's language; the widgets only
/// lay them out. Pure, so each document is checked without a phone.
abstract final class AndroidWidgetDocuments {
  static const todayKey = 'widget_today';
  static const summaryKey = 'widget_summary';
  static const captureKey = 'widget_capture';
  static const listeningKey = 'widget_listening';
  static const translationKey = 'widget_translation';
  static const approvalsKey = 'widget_approvals';
  static const familyDayKey = 'widget_family_day';
  static const nextEventKey = 'widget_next_event';
  static const memoryKey = 'widget_memory';

  /// The Android-only documents (the task list shares the iOS Up next document).
  static const keys = [
    todayKey,
    summaryKey,
    captureKey,
    listeningKey,
    translationKey,
    approvalsKey,
    familyDayKey,
    nextEventKey,
    memoryKey,
  ];

  /// Memories the Memory widget rotates through, one a day, without the app running.
  static const maxMemories = 31;

  /// Longest memory text the Memory widget is given (it shows a few lines).
  static const maxMemoryChars = 280;

  /// Rows a widget lists at most.
  static const maxRows = 3;

  static String dayKey(DateTime day) =>
      '${day.year.toString().padLeft(4, '0')}-${day.month.toString().padLeft(2, '0')}-${day.day.toString().padLeft(2, '0')}';

  static bool _sameDay(DateTime a, DateTime b) => a.year == b.year && a.month == b.month && a.day == b.day;

  static bool _kept(ServerConversation c) => !c.discarded && !c.deleted;

  /// Today: how many kept conversations started on [now]'s local day. The widget formats the date
  /// itself and drops a count from an earlier day.
  static Map<String, Object?> today(List<ServerConversation> conversations, AppLocalizations l10n,
      {required DateTime now}) {
    final count = conversations.where((c) => _kept(c) && _sameDay((c.startedAt ?? c.createdAt).toLocal(), now)).length;
    return {
      'title': l10n.today,
      'day': dayKey(now),
      'conversations': count,
      'count_label': l10n.widgetConversationsToday(count),
      'zero_label': l10n.widgetConversationsToday(0),
      'next_label': l10n.nextButton,
    };
  }

  /// Summary: the newest finished, kept conversation that has an overview. Null when none has one.
  static Map<String, Object?>? summary(List<ServerConversation> conversations, AppLocalizations l10n) {
    for (final c in conversations) {
      if (!_kept(c) || c.status != ConversationStatus.completed) continue;
      final overview = c.structured.overview.trim();
      if (overview.isEmpty) continue;
      final title = c.structured.title.trim();
      return {
        'title': l10n.summary,
        'id': c.id,
        'heading': title.isEmpty ? l10n.summary : title,
        'text': overview,
      };
    }
    return null;
  }

  static Map<String, Object?> capture(AppLocalizations l10n) => {
        'title': 'Omi',
        'note': l10n.widgetNewNote,
        'task': l10n.addTask,
      };

  /// Mute/unmute: listening, muted, or not capturing at all (no button then).
  static Map<String, Object?> listening(RecordingState state, AppLocalizations l10n) {
    final muted = state == RecordingState.pause;
    final live = state == RecordingState.record ||
        state == RecordingState.deviceRecord ||
        state == RecordingState.systemAudioRecord;
    return {
      'title': 'Omi',
      'muted': muted,
      'available': muted || live,
      'state': muted ? l10n.muted : (live ? l10n.listening : l10n.off),
      'action': muted ? l10n.unmute : l10n.mute,
    };
  }

  /// Live translation: on when the app translates other languages into [language] (the primary
  /// language's display name, or empty when none is set).
  static Map<String, Object?> translation(
      {required bool on, required String language, required AppLocalizations l10n}) {
    return {
      'title': l10n.automaticTranslation,
      'on': on,
      'state': on ? l10n.on : l10n.off,
      'target': language.isEmpty ? '' : l10n.widgetTranslationInto(language),
    };
  }

  /// Task list: the labels the Android widget adds to the shared Up next document.
  static Map<String, Object?> taskLabels(Map<String, Object?> upNext, AppLocalizations l10n, OmiDateFormat dates) {
    final open = upNext['open'] as int? ?? 0;
    final tasks = (upNext['tasks'] as List?) ?? const [];
    return {
      ...upNext,
      'title': l10n.tasks,
      'count_label': '$open',
      'empty': l10n.allCaughtUp,
      'tasks': [
        for (final task in tasks.cast<Map<String, Object?>>())
          {
            ...task,
            'due_label': switch (task['due']) {
              final num due => dates.dateTime(DateTime.fromMillisecondsSinceEpoch((due * 1000).round()).toLocal()),
              _ => null,
            },
          },
      ],
    };
  }

  /// Approval list: how much waits for the user's OK and the first items (tasks, then heard events,
  /// then day summary drafts).
  static Map<String, Object?> approvals(WaitingForApproval waiting, AppLocalizations l10n, OmiDateFormat dates) {
    final items = <Map<String, Object?>>[
      for (final task in waiting.tasks)
        {
          'title': task.description.trim(),
          'detail': task.dueAt == null ? null : l10n.approvalDue(dates.date(task.dueAt!.toLocal())),
        },
      for (final event in waiting.events) {'title': event.title.trim(), 'detail': dates.dateTime(event.start)},
      for (final date in waiting.draftDates)
        {
          'title': l10n.approvalSummaryItem(switch (DateTime.tryParse(date)) {
            final parsed? => dates.weekdayDate(parsed),
            null => date,
          }),
          'detail': l10n.approvalDraftRowBody,
        },
    ].where((item) => (item['title'] as String).isNotEmpty).take(maxRows).toList();
    final count = waiting.taskCount + waiting.eventCount + waiting.draftDates.length;
    return {
      'title': l10n.approvalWaitingTitle,
      'count': count,
      'count_label': '$count',
      'empty': l10n.approvalNothingWaiting,
      'items': items,
    };
  }

  /// Family day: what is still ahead today on the family calendar (ALLGlance), tasks due and
  /// overdue chores. `events` carries the timed blocks' start times for the Next event widget.
  /// [day] is the server's answer; the widget drops rows from another day.
  static Map<String, Object?> familyDay(FamilyDay day, AppLocalizations l10n, {required DateTime now}) {
    final owner = day.ownerName.trim();
    final rows = [
      for (final block in day.next)
        if (block.title.trim().isNotEmpty) {'title': block.title.trim(), 'detail': block.timeLabel.trim()},
    ].take(maxRows).toList();
    final footer = [
      if (day.tasksDue > 0) l10n.widgetFamilyTasksDue(day.tasksDue),
      if (day.choresOverdue > 0) l10n.widgetFamilyChoresOverdue(day.choresOverdue),
    ].join(' · ');
    final remaining = day.remaining < rows.length ? rows.length : day.remaining;
    return {
      'title': owner.isEmpty ? l10n.widgetFamilyDayTitle : l10n.widgetFamilyDayFor(owner),
      'day': day.date.isNotEmpty ? day.date : dayKey(now),
      'configured': day.configured,
      'count': remaining,
      'count_label': remaining > 0 ? l10n.widgetFamilyBlocksLeft(remaining) : '',
      'empty': !day.configured
          ? l10n.widgetFamilyNotSetUp
          : (day.error.trim().isNotEmpty && !day.reachable ? day.error.trim() : l10n.widgetFamilyNothingLeft),
      'footer': footer,
      'rows': rows,
      'events': [
        for (final block in day.next)
          if (!block.allDay && block.start != null && block.title.trim().isNotEmpty)
            {'title': block.title.trim(), 'start_ms': block.start!.millisecondsSinceEpoch},
      ],
    };
  }

  /// Next event: only labels. The widget reads the phone's calendars itself (when Omi may) and the
  /// family calendar's timed blocks from the Family day document, and shows the soonest.
  static Map<String, Object?> nextEvent(AppLocalizations l10n) => {
        'title': l10n.widgetNextEventTitle,
        'none': l10n.widgetNextEventNone,
        'family_label': l10n.widgetFamilyCalendarLabel,
      };

  /// Memory: up to [maxMemories] of the user's current memories, oldest first so the order stays
  /// put as new ones arrive. The widget shows a different one each day.
  static Map<String, Object?> memory(List<Memory> memories, AppLocalizations l10n) {
    final current =
        memories.where((m) => m.isUsefulNow && !m.isDismissed && !m.isLocked && _memoryText(m).isNotEmpty).toList()
          ..sort((a, b) {
            final byDate = a.createdAt.compareTo(b.createdAt);
            return byDate != 0 ? byDate : a.id.compareTo(b.id);
          });
    final picked = current.length > maxMemories ? current.sublist(current.length - maxMemories) : current;
    return {
      'title': l10n.widgetMemoryTitle,
      'empty': l10n.widgetMemoryEmpty,
      'memories': [
        for (final m in picked)
          {
            'id': m.id,
            'text': switch (_memoryText(m)) {
              final text when text.length > maxMemoryChars => '${text.substring(0, maxMemoryChars - 1).trimRight()}…',
              final text => text,
            },
          },
      ],
    };
  }

  static String _memoryText(Memory memory) => memory.content.decodeString.trim();
}
