import 'dart:async';

import 'package:flutter/material.dart';

import 'package:omi/pages/today/today_page.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/other/temp.dart';

/// The way into Omi Today from Home: one row under the capture row.
class OmiTodayEntryCard extends StatelessWidget {
  const OmiTodayEntryCard({super.key});

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return Padding(
      padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.xs, OmiSpacing.md, OmiSpacing.xxs),
      child: Material(
        key: const Key('omi_today_entry'),
        color: OmiColors.surface1,
        borderRadius: OmiRadius.lgAll,
        child: InkWell(
          borderRadius: OmiRadius.lgAll,
          onTap: () {
            OmiHaptics.selection();
            unawaited(routeToPage(context, const OmiTodayPage()));
          },
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: kOmiMinTapTarget),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: OmiSpacing.md, vertical: OmiSpacing.sm),
              child: Row(
                children: [
                  Icon(Icons.wb_sunny_outlined, color: OmiColors.textPrimary),
                  const SizedBox(width: OmiSpacing.sm),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(l10n.omiTodayTitle, style: OmiType.subhead.copyWith(fontWeight: FontWeight.w600)),
                        Text(l10n.omiTodaySubtitle, style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
                      ],
                    ),
                  ),
                  Icon(Icons.chevron_right, color: OmiColors.textTertiary),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
