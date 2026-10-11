import 'package:flutter/material.dart';

import 'package:intl/intl.dart';

import 'package:omi/backend/http/api/day_reviews.dart';
import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/pages/day_review/day_review_page.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/other/temp.dart';

/// Days waiting to be sorted, and past day reviews.
class DayReviewsListPage extends StatefulWidget {
  const DayReviewsListPage({super.key});

  @override
  State<DayReviewsListPage> createState() => _DayReviewsListPageState();
}

class _DayReviewsListPageState extends State<DayReviewsListPage> {
  List<String> _pending = const [];
  List<DayReview> _reviews = const [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final results = await Future.wait([getPendingDayReviews(days: 14), listDayReviews()]);
    if (!mounted) return;
    final pending = results[0] as ({String today, List<String> pendingDates})?;
    setState(() {
      _pending = (pending?.pendingDates ?? const <String>[]).reversed.toList();
      _reviews = results[1] as List<DayReview>;
      _loading = false;
    });
  }

  String _pretty(String date) {
    final parsed = DateTime.tryParse(date);
    return parsed == null ? date : DateFormat.yMMMEd().format(parsed);
  }

  Future<void> _open(String date) async {
    await routeToPage(context, DayReviewPage(date: date));
    _load();
  }

  Future<void> _pickDate() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: now,
      firstDate: now.subtract(const Duration(days: 365)),
      lastDate: now,
    );
    if (picked != null) _open(DayReviewPage.todayKey(picked));
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return Scaffold(
      backgroundColor: Theme.of(context).colorScheme.primary,
      appBar: AppBar(
        backgroundColor: Theme.of(context).colorScheme.primary,
        title: Text(l10n.dayReviewsTitle),
        actions: [
          IconButton(tooltip: l10n.dayReviewPickDate, icon: const Icon(Icons.calendar_month), onPressed: _pickDate),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  if (_pending.isNotEmpty) ...[
                    Text(l10n.dayReviewsToSort,
                        style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 8),
                    for (final date in _pending)
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.inbox_outlined, color: Colors.white70),
                        title: Text(_pretty(date), style: const TextStyle(color: Colors.white)),
                        trailing: const Icon(Icons.chevron_right, color: Colors.white38),
                        onTap: () => _open(date),
                      ),
                    const SizedBox(height: 16),
                  ],
                  Text(l10n.dayReviewsDone, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
                  const SizedBox(height: 8),
                  if (_reviews.isEmpty)
                    Text(l10n.dayReviewsNone, style: TextStyle(color: Colors.grey.shade500))
                  else
                    for (final review in _reviews)
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.auto_stories_outlined, color: Colors.white70),
                        title: Text(review.title, style: const TextStyle(color: Colors.white)),
                        subtitle: Text(_pretty(review.date), style: TextStyle(color: Colors.grey.shade500)),
                        trailing: const Icon(Icons.chevron_right, color: Colors.white38),
                        onTap: () => _open(review.date),
                      ),
                ],
              ),
            ),
    );
  }
}
