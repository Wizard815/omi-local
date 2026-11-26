import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import 'package:omi/backend/http/api/family_calendar.dart' as family_calendar_api;
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/family_calendar.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/action_items/widgets/action_item_form_sheet.dart';
import 'package:omi/pages/conversation_detail/page.dart';
import 'package:omi/pages/settings/person_detail_page.dart';
import 'package:omi/pages/today/today_card.dart';
import 'package:omi/pages/today/today_settings_page.dart';
import 'package:omi/providers/action_items_provider.dart';
import 'package:omi/providers/conversation_provider.dart';
import 'package:omi/providers/people_provider.dart';
import 'package:omi/services/android_home_widgets.dart';
import 'package:omi/services/device_calendar_service.dart';
import 'package:omi/services/today/companion_clients.dart';
import 'package:omi/services/today/news_feed_service.dart';
import 'package:omi/services/today/today_feed_settings.dart';
import 'package:omi/services/today/today_models.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/other/temp.dart';

/// Omi Today (`omi://app/today`): the day on one scrollable page, in place of a launcher's
/// news feed. Cards: agenda (phone calendar, family calendar, tasks due), the day in
/// conversations, people, news from the user's own feeds, Coinpile and the Pondering Orb. Each can
/// be hidden or moved in [TodaySettingsPage].
class OmiTodayPage extends StatefulWidget {
  const OmiTodayPage({super.key, TodayFeedSettingsStore? settings, this.news, this.companions}) : _settings = settings;

  final TodayFeedSettingsStore? _settings;
  final NewsFeedService? news;
  final CompanionClients? companions;

  @override
  State<OmiTodayPage> createState() => _OmiTodayPageState();
}

class _OmiTodayPageState extends State<OmiTodayPage> {
  late final TodayFeedSettingsStore _settings = widget._settings ?? TodayFeedSettingsStore.instance;
  late final NewsFeedService _news = widget.news ?? NewsFeedService.instance;
  late final CompanionClients _companions = widget.companions ?? CompanionClients();
  int _refresh = 0;
  bool _forceNews = false;

  @override
  void initState() {
    super.initState();
    _settings.addListener(_changed);
    unawaited(_settings.ensureLoaded());
  }

  @override
  void dispose() {
    _settings.removeListener(_changed);
    super.dispose();
  }

  void _changed() {
    if (mounted) setState(() => _refresh++);
  }

  Future<void> _pullToRefresh() async {
    setState(() {
      _forceNews = true;
      _refresh++;
    });
    unawaited(context.read<PeopleProvider>().refresh());
    unawaited(context.read<ActionItemsProvider>().refreshActionItems());
  }

  Future<void> _openSettings() => routeToPage(context, TodaySettingsPage(store: _settings));

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return Scaffold(
      appBar: AppBar(
        leading: const OmiBackButton(),
        title: Text(l10n.omiTodayTitle),
        actions: [
          OmiIconButton(
            key: const Key('omi_today_settings'),
            icon: const Icon(Icons.tune),
            label: l10n.omiTodayCustomize,
            onPressed: _openSettings,
          ),
        ],
      ),
      body: !_settings.loaded
          ? const OmiLoadingState()
          : RefreshIndicator(
              onRefresh: _pullToRefresh,
              color: OmiColors.onAccent,
              backgroundColor: OmiColors.accent,
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.only(top: OmiSpacing.xs, bottom: OmiSpacing.xxl),
                children: [for (final id in _settings.value.visible) _card(id)],
              ),
            ),
    );
  }

  Widget _card(TodayCardId id) {
    final settings = _settings.value;
    return switch (id) {
      TodayCardId.agenda => TodayAgendaCard(key: const Key('today_card_agenda'), refreshToken: _refresh),
      TodayCardId.summary => const TodaySummaryCard(key: Key('today_card_summary')),
      TodayCardId.people => const TodayPeopleCard(key: Key('today_card_people')),
      TodayCardId.news => TodayNewsCard(
          key: const Key('today_card_news'),
          feeds: settings.feeds,
          refreshToken: _refresh,
          load: (feeds) {
            final force = _forceNews;
            _forceNews = false;
            return _news.load(feeds, force: force);
          },
          onSetUp: _openSettings,
        ),
      TodayCardId.coinpile => TodayCoinpileCard(
          key: const Key('today_card_coinpile'),
          endpoint: settings.coinpile,
          refreshToken: _refresh,
          load: () async => _companions.coinpile(settings.coinpile.url, await _settings.token(TodayCardId.coinpile)),
          onSetUp: _openSettings,
        ),
      TodayCardId.pondering => TodayPonderingCard(
          key: const Key('today_card_pondering'),
          endpoint: settings.pondering,
          refreshToken: _refresh,
          load: () async => _companions.pondering(
            settings.pondering.url,
            await _settings.token(TodayCardId.pondering),
            day: AndroidWidgetDocuments.dayKey(DateTime.now()),
          ),
          onSetUp: _openSettings,
        ),
    };
  }
}

/// Today's events (phone calendar when Omi may read it, the family calendar) and the tasks due.
class TodayAgendaCard extends StatelessWidget {
  const TodayAgendaCard({super.key, this.refreshToken = 0, this.loadFamilyDay, this.loadDeviceEvents});

  final int refreshToken;
  final Future<FamilyDay?> Function()? loadFamilyDay;
  final Future<List<Map<String, dynamic>>> Function(DateTime start, DateTime end)? loadDeviceEvents;

  static Future<FamilyDay?> _familyDay() async {
    try {
      return switch (await family_calendar_api.getFamilyDay()) {
        ApiSuccess<FamilyDay>(:final data) => data,
        _ => null,
      };
    } catch (_) {
      return null;
    }
  }

  static Future<List<Map<String, dynamic>>> _deviceEvents(DateTime start, DateTime end) async {
    if (!Platform.isAndroid) return const [];
    return DeviceCalendarService.getEvents(startMs: start.millisecondsSinceEpoch, endMs: end.millisecondsSinceEpoch);
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final tasks = context.read<ActionItemsProvider>();
    return TodayAsyncCard<TodayAgenda>(
      title: l10n.todayAgendaTitle,
      icon: Icons.event_outlined,
      refreshToken: refreshToken,
      load: () async {
        final now = DateTime.now();
        final start = DateTime(now.year, now.month, now.day);
        final results = await Future.wait<Object?>([
          (loadDeviceEvents ?? _deviceEvents)(start, start.add(const Duration(days: 1))),
          (loadFamilyDay ?? _familyDay)(),
          tasks.ensureHomeTodayTasksLoaded(now: now),
        ]);
        final agenda = TodayAgenda.build(
          deviceEvents: results[0] as List<Map<String, dynamic>>,
          family: results[1] as FamilyDay?,
          tasks: tasks.reminderTasks,
          now: now,
        );
        if (agenda.isEmpty && !agenda.familyUnreachable) {
          return TodayCardEmpty(title: l10n.todayAgendaEmpty, message: l10n.todayAgendaEmptyMessage);
        }
        return TodayCardData(agenda);
      },
      builder: (context, agenda) {
        final dates = OmiDateFormat.of(context);
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (agenda.events.isNotEmpty) TodaySubheader(l10n.todayEvents),
            for (final event in agenda.events)
              TodayRow(
                leading: event.start != null
                    ? dates.time(event.start!)
                    : (event.timeLabel.isNotEmpty ? event.timeLabel : l10n.todayAllDay),
                title: event.title,
                detail: event.family ? l10n.widgetFamilyCalendarLabel : null,
              ),
            if (agenda.tasks.isNotEmpty) TodaySubheader(l10n.todayTasksDue),
            for (final task in agenda.tasks)
              TodayRow(
                leading:
                    task.dueAt!.toLocal().isBefore(DateTime.now()) ? l10n.overdue : dates.time(task.dueAt!.toLocal()),
                title: task.description.trim(),
                onTap: () => unawaited(showActionItemFormSheet(context, actionItem: task)),
              ),
            if (agenda.familyUnreachable)
              Padding(
                padding: const EdgeInsets.only(top: OmiSpacing.xs),
                child:
                    Text(l10n.todayFamilyUnreachable, style: OmiType.footnote.copyWith(color: OmiColors.textTertiary)),
              ),
          ],
        );
      },
    );
  }
}

/// The day in conversations: how many today, the newest overview, the latest titles.
class TodaySummaryCard extends StatelessWidget {
  const TodaySummaryCard({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final provider = context.watch<ConversationProvider>();
    final summary = TodayDaySummary.from(provider.conversations, now: DateTime.now());
    final loading = summary.isEmpty && provider.isLoadingConversations && provider.conversations.isEmpty;
    final TodayCardState<TodayDaySummary> state =
        summary.isEmpty ? TodayCardEmpty(title: l10n.widgetConversationsToday(0)) : TodayCardData(summary);
    return TodayCardShell(
      title: l10n.todaySummaryTitle,
      icon: Icons.notes_outlined,
      child: loading
          ? const OmiLoadingState()
          : TodayStateView<TodayDaySummary>(
              state: state,
              builder: (context, summary) => Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(l10n.widgetConversationsToday(summary.count),
                      style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
                  if (summary.overview != null) ...[
                    const SizedBox(height: OmiSpacing.xs),
                    if (summary.headline != null)
                      Text(summary.headline!, style: OmiType.subhead.copyWith(fontWeight: FontWeight.w600)),
                    Text(summary.overview!, style: OmiType.subhead, maxLines: 5, overflow: TextOverflow.ellipsis),
                  ],
                  for (final conversation in summary.recent)
                    TodayRow(
                      leading:
                          OmiDateFormat.of(context).time((conversation.startedAt ?? conversation.createdAt).toLocal()),
                      title: conversation.structured.title.trim(),
                      onTap: () => unawaited(routeToPage(context, ConversationDetailPage(conversation: conversation))),
                    ),
                ],
              ),
            ),
    );
  }
}

/// People: pinned first, then whoever Omi heard most recently.
class TodayPeopleCard extends StatelessWidget {
  const TodayPeopleCard({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final provider = context.watch<PeopleProvider>();
    final people = pickTodayPeople(provider.people);
    final Widget child;
    if (people.isEmpty && provider.loading) {
      child = const OmiLoadingState();
    } else if (people.isEmpty && provider.loadFailed) {
      child = OmiErrorState(message: l10n.somethingWentWrong, onRetry: provider.refresh);
    } else if (people.isEmpty) {
      child = OmiEmptyState(icon: Icons.people_outline, title: l10n.noPeopleYet);
    } else {
      final dates = OmiDateFormat.of(context);
      child = Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final person in people)
            TodayRow(
              title: person.name.trim(),
              detail: person.lastHeardAt == null ? null : dates.dayHeader(person.lastHeardAt!.toLocal()),
              onTap: () => unawaited(routeToPage(context, PersonDetailPage(personId: person.id))),
            ),
        ],
      );
    }
    return TodayCardShell(title: l10n.people, icon: Icons.people_outline, child: child);
  }
}

/// Headlines from the user's RSS/Atom feeds, read straight from the phone.
class TodayNewsCard extends StatelessWidget {
  const TodayNewsCard({
    super.key,
    required this.feeds,
    required this.load,
    required this.onSetUp,
    this.refreshToken = 0,
    this.maxShown = 6,
  });

  final List<String> feeds;
  final Future<NewsDigest> Function(List<String> feeds) load;
  final VoidCallback onSetUp;
  final int refreshToken;
  final int maxShown;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return TodayAsyncCard<NewsDigest>(
      title: l10n.todayNewsTitle,
      icon: Icons.newspaper_outlined,
      refreshToken: refreshToken,
      load: () async {
        if (feeds.isEmpty) {
          return TodayCardEmpty(
            title: l10n.todayNewsEmpty,
            message: l10n.todayNewsEmptyMessage,
            actionLabel: l10n.todayNewsAddFeeds,
            onAction: onSetUp,
          );
        }
        final digest = await load(feeds);
        if (digest.items.isEmpty) {
          return digest.failedFeeds.isNotEmpty
              ? TodayCardError(l10n.todayNewsError)
              : TodayCardEmpty(title: l10n.todayNothingYet);
        }
        return TodayCardData(digest);
      },
      builder: (context, digest) {
        final dates = OmiDateFormat.of(context);
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (final item in digest.items.take(maxShown))
              TodayRow(
                title: item.title,
                detail: [
                  if (item.source.isNotEmpty) item.source,
                  if (item.published != null) dates.dayHeader(item.published!.toLocal()),
                ].join(' · '),
                onTap: item.link == null
                    ? null
                    : () => unawaited(launchUrl(Uri.parse(item.link!), mode: LaunchMode.externalApplication)),
              ),
            if (digest.failedFeeds.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: OmiSpacing.xs),
                child: Text(l10n.todayNewsSomeFailed(digest.failedFeeds.length),
                    style: OmiType.footnote.copyWith(color: OmiColors.textTertiary)),
              ),
          ],
        );
      },
    );
  }
}

TodayCardState<T> _companionState<T>(
  AppLocalizations l10n,
  CompanionResult<T> result, {
  required String name,
  required String nothing,
}) {
  return switch (result) {
    CompanionData<T>(:final data) => TodayCardData(data),
    CompanionNothing<T>() => TodayCardEmpty(title: nothing),
    CompanionFailure<T>(:final kind) when kind == 'unauthorized' => TodayCardError(l10n.todayCompanionUnauthorized),
    CompanionFailure<T>() => TodayCardError(l10n.todayCompanionUnreachable(name)),
  };
}

TodayCardEmpty<T> _notSetUp<T>(BuildContext context, VoidCallback onSetUp) => TodayCardEmpty(
      title: context.l10n.todayCompanionNotSetUp,
      message: context.l10n.todayCompanionSetUpMessage,
      actionLabel: context.l10n.todaySetUp,
      onAction: onSetUp,
    );

/// Coinpile (Firefly III): net worth, bills ahead, free savings, this month's spending, alerts.
class TodayCoinpileCard extends StatelessWidget {
  const TodayCoinpileCard({
    super.key,
    required this.endpoint,
    required this.load,
    required this.onSetUp,
    this.refreshToken = 0,
  });

  final CompanionEndpoint endpoint;
  final Future<CompanionResult<CoinpileSummary>> Function() load;
  final VoidCallback onSetUp;
  final int refreshToken;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final name = l10n.todayCoinpileTitle;
    return TodayAsyncCard<CoinpileSummary>(
      title: name,
      icon: Icons.savings_outlined,
      refreshToken: refreshToken,
      load: () async {
        if (!endpoint.configured) return _notSetUp(context, onSetUp);
        final state = _companionState(l10n, await load(), name: name, nothing: l10n.todayNothingYet);
        if (state is TodayCardData<CoinpileSummary> && state.data.isEmpty) {
          return TodayCardEmpty(title: l10n.todayNothingYet);
        }
        return state;
      },
      builder: (context, s) {
        final money = NumberFormat.simpleCurrency(locale: Localizations.localeOf(context).toLanguageTag());
        String? amount(double? v) => v == null ? null : money.format(v);
        final rows = <(String, String?)>[
          (l10n.todayNetWorth, amount(s.netWorth)),
          (l10n.todayBills30d, amount(s.bills30d)),
          (l10n.todaySavingsFree, amount(s.savingsFree)),
          (l10n.todaySpentThisMonth, amount(s.spentThisMonth)),
        ];
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (final (label, value) in rows)
              if (value != null) TodayRow(title: label, value: value),
            if (s.nextBillName != null)
              TodayRow(
                title: l10n.todayNextBill(s.nextBillName!),
                detail: [
                  if (DateTime.tryParse(s.nextBillDate ?? '') case final date?)
                    OmiDateFormat.of(context).weekdayDate(date),
                  if (amount(s.nextBillAmount) case final a?) a,
                ].join(' · '),
              ),
            if (s.alertsTotal > 0)
              Text(l10n.todayAlerts(s.alertsTotal),
                  style: OmiType.footnote
                      .copyWith(color: s.alertsCritical > 0 ? OmiColors.danger : OmiColors.textSecondary)),
          ],
        );
      },
    );
  }
}

/// The Pondering Orb's morning brief, written by its Golem.
class TodayPonderingCard extends StatefulWidget {
  const TodayPonderingCard({
    super.key,
    required this.endpoint,
    required this.load,
    required this.onSetUp,
    this.refreshToken = 0,
  });

  final CompanionEndpoint endpoint;
  final Future<CompanionResult<PonderingBrief>> Function() load;
  final VoidCallback onSetUp;
  final int refreshToken;

  static const collapsedLines = 8;

  @override
  State<TodayPonderingCard> createState() => _TodayPonderingCardState();
}

class _TodayPonderingCardState extends State<TodayPonderingCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final name = l10n.todayPonderingTitle;
    return TodayAsyncCard<PonderingBrief>(
      title: name,
      icon: Icons.auto_awesome_outlined,
      refreshToken: widget.refreshToken,
      load: () async {
        if (!widget.endpoint.configured) return _notSetUp(context, widget.onSetUp);
        return _companionState(l10n, await widget.load(), name: name, nothing: l10n.todayNoBriefYet);
      },
      builder: (context, brief) {
        final lines = PonderingBrief.plainLines(brief.text);
        final shown = _expanded ? lines : lines.take(TodayPonderingCard.collapsedLines).toList();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (final line in shown)
              Padding(
                padding: const EdgeInsets.only(bottom: OmiSpacing.xxs),
                child: Text(line, style: OmiType.subhead),
              ),
            if (lines.length > TodayPonderingCard.collapsedLines)
              Align(
                alignment: AlignmentDirectional.centerStart,
                child: OmiButton.tertiary(
                  label: _expanded ? l10n.showLess : l10n.showMore,
                  size: OmiButtonSize.compact,
                  onPressed: () => setState(() => _expanded = !_expanded),
                ),
              ),
          ],
        );
      },
    );
  }
}
