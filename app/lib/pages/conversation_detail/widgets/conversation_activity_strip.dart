import 'dart:async';

import 'package:flutter/material.dart';

import 'package:provider/provider.dart';

import 'package:omi/backend/schema/conversation.dart';
import 'package:omi/pages/conversation_detail/conversation_detail_provider.dart';
import 'package:omi/pages/conversation_detail/widgets/summary_generation.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// A server-side processing pass on the open conversation, under the tab row: a hairline progress
/// bar and one line saying so.
///
/// While the reader's own reprocess runs, the Summary tab's "Summarizing with …" state is the one
/// progress surface, so this draws only when another tab is on screen — a request started from the
/// Transcript tab (the ⋯ menu, the speaker refresh) still shows progress.
///
/// While the server reports the conversation as processing, the strip re-reads it every
/// [pollInterval] (at most [maxPolls] times), then every [slowPollInterval] (at most [maxSlowPolls]
/// times), so the result replaces the strip without the reader leaving and coming back — also when
/// a slow self-hosted model takes many minutes to write the notes in the background.
class ConversationActivityStrip extends StatefulWidget {
  const ConversationActivityStrip({
    super.key,
    this.pollInterval = const Duration(seconds: 5),
    this.maxPolls = 36,
    this.slowPollInterval = const Duration(seconds: 30),
    this.maxSlowPolls = 60,
    this.summaryTabVisible = true,
  });

  final Duration pollInterval;
  final int maxPolls;
  final Duration slowPollInterval;
  final int maxSlowPolls;

  /// Whether the Summary tab — the reprocess's primary progress surface — is on screen.
  final bool summaryTabVisible;

  @override
  State<ConversationActivityStrip> createState() => _ConversationActivityStripState();
}

class _ConversationActivityStripState extends State<ConversationActivityStrip> {
  Timer? _poll;
  String? _pollingId;
  int _polls = 0;
  bool _polling = false;

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  void _syncPolling(ConversationDetailProvider provider, ServerConversation? conversation) {
    final processingId =
        conversation != null && conversation.status == ConversationStatus.processing ? conversation.id : null;
    if (processingId == _pollingId) return;
    _poll?.cancel();
    _poll = null;
    _pollingId = processingId;
    _polls = 0;
    if (processingId == null) return;
    _startPolling(provider, processingId, widget.pollInterval);
  }

  void _startPolling(ConversationDetailProvider provider, String processingId, Duration interval) {
    _poll = Timer.periodic(interval, (timer) async {
      if (!mounted || provider.conversationOrNull?.id != processingId) {
        timer.cancel();
        return;
      }
      if (_polls >= widget.maxPolls + widget.maxSlowPolls) {
        timer.cancel();
        return;
      }
      if (_polls >= widget.maxPolls && interval != widget.slowPollInterval) {
        // The notes are taking long (a slow model): keep checking, less often.
        timer.cancel();
        _startPolling(provider, processingId, widget.slowPollInterval);
        return;
      }
      // One read at a time: a slow read is not overtaken by the next tick.
      if (_polling) return;
      _polling = true;
      _polls++;
      try {
        await provider.refreshConversation();
      } finally {
        _polling = false;
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ConversationDetailProvider>();
    final conversation = provider.conversationOrNull;
    _syncPolling(provider, conversation);

    final l10n = context.l10n;
    final String? label;
    if (provider.isReprocessingOpenConversation && !widget.summaryTabVisible) {
      // Not on the tab that owns the primary progress surface: keep a global indicator so a
      // request started from this tab never runs invisibly.
      label = l10n.summarySummarizingWith(summarySourceName(provider, provider.reprocessingAppId, l10n));
    } else if (!provider.isReprocessingOpenConversation && conversation?.status == ConversationStatus.processing) {
      // The transcript is stored and only the notes are left: the same words as the Home row.
      label =
          conversation!.transcriptSegments.isNotEmpty ? l10n.episodeWritingNotes : l10n.processingConversationProgress;
    } else {
      label = null;
    }

    return AnimatedSize(
      duration: const Duration(milliseconds: 200),
      alignment: Alignment.topCenter,
      child: label == null
          ? const SizedBox(width: double.infinity)
          : Semantics(
              key: const Key('conversation_activity_strip'),
              liveRegion: true,
              label: label,
              child: ExcludeSemantics(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    LinearProgressIndicator(
                      minHeight: 2,
                      color: OmiColors.textSecondary,
                      backgroundColor: Colors.transparent,
                    ),
                    Padding(
                      padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.xs, OmiSpacing.md, 0),
                      child: Text(label, style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
                    ),
                  ],
                ),
              ),
            ),
    );
  }
}
