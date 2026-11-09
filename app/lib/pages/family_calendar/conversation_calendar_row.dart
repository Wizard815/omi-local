import 'package:flutter/material.dart';

import 'package:omi/backend/schema/conversation.dart';
import 'package:omi/pages/family_calendar/family_calendar_review_page.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/other/temp.dart';

/// omi-local: on a conversation's summary, the events Omi heard in it. Tapping opens the
/// Calendar check for just this conversation (edit the title, note, date and time, then add or
/// decline). Nothing is shown when Omi heard no events here.
class ConversationCalendarRow extends StatelessWidget {
  const ConversationCalendarRow({super.key, required this.conversation});

  final ServerConversation conversation;

  @override
  Widget build(BuildContext context) {
    final count = conversation.structured.events.length;
    if (count == 0) return const SizedBox.shrink();
    final l10n = context.l10n;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: OmiSpacing.sm),
      child: OmiSettingsGroup(
        children: [
          OmiSettingsRow(
            key: Key('conversation_calendar_${conversation.id}'),
            leading: const Icon(Icons.event_outlined),
            title: l10n.familyCalendarTitle,
            subtitle: l10n.familyCalendarHeardHere(count),
            showChevron: true,
            onTap: () => routeToPage(
              context,
              FamilyCalendarReviewPage(
                conversationId: conversation.id,
                conversationTitle: conversation.structured.title,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
