import 'package:flutter/material.dart';

import 'package:omi/backend/http/api/family_calendar.dart';
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/family_calendar.dart';
import 'package:omi/pages/family_calendar/family_calendar_review_page.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/logger.dart';
import 'package:omi/utils/other/temp.dart';

/// "N events to check" card at the top of the conversation list: Omi heard dates it wants
/// confirmed before they go to the family calendar. Shown only with a family calendar set
/// and something waiting; never writes anything itself.
class FamilyCalendarBanner extends StatefulWidget {
  const FamilyCalendarBanner({super.key});

  @override
  State<FamilyCalendarBanner> createState() => _FamilyCalendarBannerState();
}

class _FamilyCalendarBannerState extends State<FamilyCalendarBanner> {
  int _pending = 0;
  bool _dismissed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    // An optional nudge: a failure shows nothing rather than reaching the list.
    final ApiResult<FamilyCalendar> result;
    try {
      result = await getFamilyCalendar();
    } catch (e) {
      Logger.debug('Family calendar banner lookup failed: ${e.runtimeType}');
      return;
    }
    if (!mounted) return;
    if (result case ApiSuccess(:final data) when data.configured) {
      setState(() => _pending = data.pending.length);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_dismissed || _pending == 0) return const SizedBox.shrink();
    final l10n = context.l10n;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
      child: Material(
        key: const Key('family_calendar_banner'),
        color: OmiColors.surface1,
        borderRadius: OmiRadius.lgAll,
        child: InkWell(
          borderRadius: OmiRadius.lgAll,
          onTap: () async {
            await routeToPage(context, const FamilyCalendarReviewPage());
            _load();
          },
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 4, 12),
            child: Row(
              children: [
                Icon(Icons.event_note_outlined, color: OmiColors.textPrimary),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(l10n.familyCalendarBannerTitle(_pending),
                          style: OmiType.subhead.copyWith(fontWeight: FontWeight.w600)),
                      const SizedBox(height: 2),
                      Text(l10n.familyCalendarBannerBody,
                          style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
                    ],
                  ),
                ),
                IconButton(
                  icon: Icon(Icons.close, color: OmiColors.textTertiary, size: 18),
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
