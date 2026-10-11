import 'package:flutter/material.dart';

import 'package:intl/intl.dart';

import 'package:omi/backend/http/api/day_reviews.dart';
import 'package:omi/pages/day_review/day_review_page.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/other/temp.dart';

/// Which unsorted day (if any) to nudge the user about: the latest earlier day,
/// or today once it's evening ([eveningHour] local time and later).
String? dayToNudge(
    {required String today, required List<String> pendingDates, required DateTime now, int eveningHour = 18}) {
  final earlier = pendingDates.where((d) => d.compareTo(today) < 0).toList()..sort();
  if (pendingDates.contains(today) && now.hour >= eveningHour) return today;
  return earlier.isEmpty ? null : earlier.last;
}

/// "Review your day" card at the top of the conversation list.
class DayReviewBanner extends StatefulWidget {
  const DayReviewBanner({super.key});

  @override
  State<DayReviewBanner> createState() => _DayReviewBannerState();
}

class _DayReviewBannerState extends State<DayReviewBanner> {
  String? _date;
  String? _today;
  bool _dismissed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final pending = await getPendingDayReviews();
    if (!mounted || pending == null) return;
    setState(() {
      _today = pending.today;
      _date = dayToNudge(today: pending.today, pendingDates: pending.pendingDates, now: DateTime.now());
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_dismissed || _date == null) return const SizedBox.shrink();
    final l10n = context.l10n;
    final parsed = DateTime.tryParse(_date!);
    final label = _date == _today
        ? l10n.dayReviewBannerToday
        : l10n.dayReviewBannerDay(parsed == null ? _date! : DateFormat.MMMEd().format(parsed));
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
      child: Material(
        color: const Color(0xFF1F1F25),
        borderRadius: BorderRadius.circular(16),
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: () async {
            await routeToPage(context, DayReviewPage(date: _date!));
            _load();
          },
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 4, 12),
            child: Row(
              children: [
                const Icon(Icons.auto_stories_outlined, color: Colors.white),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(label, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
                      const SizedBox(height: 2),
                      Text(l10n.dayReviewBannerBody, style: TextStyle(color: Colors.grey.shade400, fontSize: 12.5)),
                    ],
                  ),
                ),
                IconButton(
                  icon: const Icon(Icons.close, color: Colors.white38, size: 18),
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
