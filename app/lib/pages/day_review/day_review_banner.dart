import 'package:flutter/material.dart';

import 'package:omi/ui/ui.dart';

import 'package:omi/backend/http/api/approvals.dart';
import 'package:omi/backend/http/api/day_reviews.dart';
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/pages/approvals/approvals_page.dart';
import 'package:omi/pages/day_review/day_review_page.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/logger.dart';
import 'package:omi/utils/other/temp.dart';

/// Which unsorted day (if any) to nudge the user about: the latest earlier day,
/// or today once it's evening ([eveningHour] local time and later).
String? dayToNudge(
    {required String today, required List<String> pendingDates, required DateTime now, int eveningHour = 18}) {
  final earlier = pendingDates.where((d) => d.compareTo(today) < 0).toList()..sort();
  if (pendingDates.contains(today) && now.hour >= eveningHour) return today;
  return earlier.isEmpty ? null : earlier.last;
}

/// "Review your day" card at the top of the conversation list. When something waits for the
/// user's OK (tasks, heard events, a day summary draft) it says so instead and opens the list.
class DayReviewBanner extends StatefulWidget {
  const DayReviewBanner({super.key, this.approvals = const ApprovalsApi()});

  final ApprovalsApi approvals;

  @override
  State<DayReviewBanner> createState() => _DayReviewBannerState();
}

class _DayReviewBannerState extends State<DayReviewBanner> {
  String? _date;
  String? _today;
  String? _waiting;
  bool _dismissed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    // An optional nudge: if the lookup fails for any reason, show nothing
    // rather than letting the error escape into the conversation list.
    final pending = await getPendingDayReviews().catchError((Object e) {
      Logger.debug('Day review banner lookup failed: ${e.runtimeType}');
      return null;
    });
    ApiResult<WaitingForApproval> waiting;
    try {
      waiting = await widget.approvals.waiting();
    } catch (e) {
      // Optional too: a failed lookup shows the plain day nudge (or nothing).
      Logger.debug('Approval banner lookup failed: ${e.runtimeType}');
      waiting = const ApiFailure(ApiProblem(ApiProblemKind.transport));
    }
    if (!mounted) return;
    setState(() {
      if (pending != null) {
        _today = pending.today;
        _date = dayToNudge(today: pending.today, pendingDates: pending.pendingDates, now: DateTime.now());
      }
      _waiting = switch (waiting) {
        ApiSuccess(:final data) => waitingLine(context.l10n, data, today: data.today),
        ApiFailure() => null,
      };
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_dismissed || (_date == null && _waiting == null)) return const SizedBox.shrink();
    final l10n = context.l10n;
    final waiting = _waiting;
    final parsed = _date == null ? null : DateTime.tryParse(_date!);
    final label = waiting ??
        (_date == _today
            ? l10n.dayReviewBannerToday
            : l10n.dayReviewBannerDay(parsed == null ? _date! : OmiDateFormat.of(context).weekdayDate(parsed)));
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
      child: Material(
        color: OmiColors.surface1,
        borderRadius: OmiRadius.lgAll,
        child: InkWell(
          borderRadius: OmiRadius.lgAll,
          key: const Key('day_review_banner'),
          onTap: () async {
            await routeToPage(context, waiting != null ? const ApprovalsPage() : DayReviewPage(date: _date!));
            _load();
          },
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 4, 12),
            child: Row(
              children: [
                Icon(Icons.auto_stories_outlined, color: OmiColors.textPrimary),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(label,
                          style: OmiType.subhead.copyWith(color: OmiColors.textPrimary, fontWeight: FontWeight.w600)),
                      const SizedBox(height: 2),
                      Text(waiting != null ? l10n.approvalBannerBody : l10n.dayReviewBannerBody,
                          style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
                    ],
                  ),
                ),
                OmiIconButton(
                  icon: Icon(Icons.close, color: OmiColors.textTertiary, size: 18),
                  label: l10n.dismiss,
                  onPressed: () => setState(() => _dismissed = true),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
