import 'package:flutter/material.dart';

import 'package:intl/intl.dart';

import 'package:omi/backend/http/api/day_reviews.dart';
import 'package:omi/backend/schema/day_review.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/pages/day_review/day_review_page.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/other/temp.dart';

/// Days waiting to be sorted, and past day reviews.
class DayReviewsListPage extends StatefulWidget {
  const DayReviewsListPage({super.key, this.loadPending = getPendingDayReviews, this.loadReviews = listDayReviews});

  /// The days waiting to be sorted; null when the read failed.
  final Future<({String today, List<String> pendingDates, List<String> draftDates})?> Function({int days}) loadPending;
  final Future<List<DayReview>> Function({int limit}) loadReviews;

  @override
  State<DayReviewsListPage> createState() => _DayReviewsListPageState();
}

class _DayReviewsListPageState extends State<DayReviewsListPage> {
  List<String> _pending = const [];
  List<DayReview> _reviews = const [];
  bool _loading = true;

  /// The pending-days read failed (the list read cannot tell a failure from no reviews).
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final results = await Future.wait([widget.loadPending(days: 14), widget.loadReviews()]);
    if (!mounted) return;
    final pending = results[0] as ({String today, List<String> pendingDates, List<String> draftDates})?;
    setState(() {
      _pending = (pending?.pendingDates ?? const <String>[]).reversed.toList();
      _reviews = results[1] as List<DayReview>;
      _failed = pending == null;
      _loading = false;
    });
  }

  Future<void> _retry() async {
    setState(() => _loading = true);
    await _load();
  }

  String _pretty(String date) {
    final parsed = DateTime.tryParse(date);
    return parsed == null ? date : DateFormat.yMMMEd(OmiDateFormat.of(context).localeName).format(parsed);
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
    final Widget body;
    if (_loading) {
      body = const OmiLoadingState();
    } else if (_failed) {
      body = OmiErrorState(message: l10n.dayReviewsLoadFailed, onRetry: _retry);
    } else if (_pending.isEmpty && _reviews.isEmpty) {
      body =
          OmiEmptyState(icon: Icons.auto_stories_outlined, title: l10n.dayReviewsTitle, message: l10n.dayReviewsNone);
    } else {
      body = RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(OmiSpacing.md),
          children: [
            if (_pending.isNotEmpty) ...[
              OmiSectionHeader(l10n.dayReviewsToSort),
              OmiSettingsGroup(
                children: [
                  for (final date in _pending)
                    OmiSettingsRow(
                      key: ValueKey('day_reviews_pending_$date'),
                      leading: const Icon(Icons.inbox_outlined),
                      title: _pretty(date),
                      onTap: () => _open(date),
                    ),
                ],
              ),
              const SizedBox(height: OmiSpacing.lg),
            ],
            OmiSectionHeader(l10n.dayReviewsDone),
            if (_reviews.isEmpty)
              Text(l10n.dayReviewsNone, style: OmiType.subhead.copyWith(color: OmiColors.textSecondary))
            else
              OmiSettingsGroup(
                children: [
                  for (final review in _reviews)
                    OmiSettingsRow(
                      key: ValueKey('day_reviews_done_${review.date}'),
                      leading: const Icon(Icons.auto_stories_outlined),
                      // An untitled review is named by its day.
                      title: review.title.trim().isEmpty ? _pretty(review.date) : review.title,
                      subtitle: review.title.trim().isEmpty ? null : _pretty(review.date),
                      onTap: () => _open(review.date),
                    ),
                ],
              ),
          ],
        ),
      );
    }
    return Scaffold(
      backgroundColor: OmiColors.surface0,
      appBar: AppBar(
        backgroundColor: OmiColors.surface0,
        leading: const OmiBackButton(),
        title: Text(l10n.dayReviewsTitle),
        actions: [
          OmiIconButton(icon: const Icon(Icons.calendar_month), label: l10n.dayReviewPickDate, onPressed: _pickDate),
        ],
      ),
      body: body,
    );
  }
}
