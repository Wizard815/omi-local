import 'dart:async';

import 'package:flutter/material.dart';

import 'package:omi/backend/http/api/local_usage.dart';
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/local_usage.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// "Your usage on this server": the signed-in person's own action counts, which their self-hosted
/// server keeps (`GET /v1/users/me/local-usage`). Read-only; nothing here goes anywhere else.
class ServerUsagePage extends StatefulWidget {
  const ServerUsagePage({super.key, this.load});

  /// Replaces the request in tests.
  final Future<ApiResult<LocalUsageReport>> Function()? load;

  @override
  State<ServerUsagePage> createState() => _ServerUsagePageState();
}

class _ServerUsagePageState extends State<ServerUsagePage> {
  /// How many recent actions the page lists.
  static const _recentShown = 10;

  LocalUsageReport? _report;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    if (!_loading) setState(() => _loading = true);
    final result = await (widget.load ?? getLocalUsage)();
    if (!mounted) return;
    setState(() {
      _loading = false;
      // A failed refresh keeps what was shown; a failed first load shows the error state.
      if (result case ApiSuccess(:final data)) _report = data;
    });
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return Scaffold(
      appBar: AppBar(leading: const OmiBackButton(), title: Text(l10n.serverUsageTitle)),
      body: _body(context),
    );
  }

  Widget _body(BuildContext context) {
    final l10n = context.l10n;
    final report = _report;
    if (report == null) {
      if (_loading) return const OmiLoadingState();
      return OmiErrorState(message: l10n.serverUsageLoadFailed, onRetry: _load);
    }
    final dates = OmiDateFormat.of(context);
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        key: const Key('server_usage_list'),
        padding: const EdgeInsets.all(OmiSpacing.md),
        children: [
          Text(l10n.serverUsageIntro, style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
          const SizedBox(height: OmiSpacing.xl),
          if (!report.enabled)
            OmiEmptyState(icon: Icons.bar_chart_rounded, title: l10n.serverUsageOff)
          else if (report.totals.isEmpty)
            OmiEmptyState(icon: Icons.bar_chart_rounded, title: l10n.serverUsageEmpty)
          else ...[
            Text(
              l10n.serverUsageTotal(report.total, report.windowDays),
              key: const Key('server_usage_total'),
              style: OmiType.headline,
            ),
            const SizedBox(height: OmiSpacing.lg),
            OmiSettingsGroup(
              header: l10n.serverUsageMostUsed,
              children: [
                for (final row in report.totals) OmiSettingsRow(title: row.event, value: '${row.count}'),
              ],
            ),
            if (report.recent.isNotEmpty) ...[
              const SizedBox(height: OmiSpacing.xl),
              OmiSettingsGroup(
                header: l10n.serverUsageRecent,
                children: [
                  for (final event in report.recent.take(_recentShown))
                    OmiSettingsRow(title: event.event, value: dates.timestamp(event.at.toLocal())),
                ],
              ),
            ],
          ],
        ],
      ),
    );
  }
}
