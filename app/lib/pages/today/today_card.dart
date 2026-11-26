import 'dart:async';

import 'package:flutter/material.dart';

import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// What a card's loader came to. Loading is the pending future itself.
sealed class TodayCardState<T> {
  const TodayCardState();
}

class TodayCardData<T> extends TodayCardState<T> {
  const TodayCardData(this.data);
  final T data;
}

/// Nothing to show: an [OmiEmptyState] with [title], [message] and an optional [action].
class TodayCardEmpty<T> extends TodayCardState<T> {
  const TodayCardEmpty({required this.title, this.message, this.actionLabel, this.onAction});
  final String title;
  final String? message;
  final String? actionLabel;
  final VoidCallback? onAction;
}

class TodayCardError<T> extends TodayCardState<T> {
  const TodayCardError(this.message);
  final String message;
}

/// One card of Omi Today: a header, then exactly one of loading, empty, error or the content.
class TodayCardShell extends StatelessWidget {
  const TodayCardShell({super.key, required this.title, required this.icon, required this.child, this.trailing});

  final String title;
  final IconData icon;
  final Widget child;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.xs, OmiSpacing.md, OmiSpacing.sm),
      child: DecoratedBox(
        decoration: BoxDecoration(color: OmiColors.surface1, borderRadius: OmiRadius.lgAll),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.sm, OmiSpacing.md, OmiSpacing.md),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              Semantics(
                header: true,
                child: Row(
                  children: [
                    Icon(icon, size: 18, color: OmiColors.textSecondary),
                    const SizedBox(width: OmiSpacing.xs),
                    Expanded(
                      child: Text(title, style: OmiType.headline, maxLines: 1, overflow: TextOverflow.ellipsis),
                    ),
                    if (trailing != null) trailing!,
                  ],
                ),
              ),
              const SizedBox(height: OmiSpacing.xs),
              child,
            ],
          ),
        ),
      ),
    );
  }
}

/// A card whose content comes from [load]. [refreshToken] changing (pull to refresh) loads again.
class TodayAsyncCard<T> extends StatefulWidget {
  const TodayAsyncCard({
    super.key,
    required this.title,
    required this.icon,
    required this.load,
    required this.builder,
    this.refreshToken = 0,
  });

  final String title;
  final IconData icon;
  final Future<TodayCardState<T>> Function() load;
  final Widget Function(BuildContext context, T data) builder;
  final int refreshToken;

  @override
  State<TodayAsyncCard<T>> createState() => _TodayAsyncCardState<T>();
}

class _TodayAsyncCardState<T> extends State<TodayAsyncCard<T>> {
  TodayCardState<T>? _state;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant TodayAsyncCard<T> oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.refreshToken != widget.refreshToken) unawaited(_load());
  }

  Future<void> _load() async {
    final generation = ++_generation;
    if (_state != null) setState(() => _state = null);
    TodayCardState<T> next;
    try {
      next = await widget.load();
    } catch (_) {
      next = TodayCardError<T>(mounted ? context.l10n.somethingWentWrong : '');
    }
    if (!mounted || generation != _generation) return;
    setState(() => _state = next);
  }

  @override
  Widget build(BuildContext context) {
    return TodayCardShell(
      title: widget.title,
      icon: widget.icon,
      child: TodayStateView<T>(state: _state, builder: widget.builder, onRetry: _load),
    );
  }
}

/// Draws a [TodayCardState] with the shared page-state primitives; null is loading.
class TodayStateView<T> extends StatelessWidget {
  const TodayStateView({super.key, required this.state, required this.builder, this.onRetry});

  final TodayCardState<T>? state;
  final Widget Function(BuildContext context, T data) builder;
  final FutureOr<void> Function()? onRetry;

  @override
  Widget build(BuildContext context) {
    return switch (state) {
      null => const OmiLoadingState(),
      TodayCardData<T>(:final data) => builder(context, data),
      TodayCardEmpty<T>(:final title, :final message, :final actionLabel, :final onAction) => OmiEmptyState(
          icon: Icons.inbox_outlined,
          title: title,
          message: message,
          action: actionLabel == null || onAction == null
              ? null
              : OmiButton.secondary(label: actionLabel, onPressed: onAction, size: OmiButtonSize.compact),
        ),
      TodayCardError<T>(:final message) => OmiErrorState(message: message, onRetry: onRetry),
    };
  }
}

/// A row inside a card: title, optional detail, optional leading text (a time), tappable.
class TodayRow extends StatelessWidget {
  const TodayRow({super.key, required this.title, this.detail, this.leading, this.value, this.onTap});

  final String title;
  final String? detail;
  final String? leading;

  /// Trailing emphasized text (an amount next to its label).
  final String? value;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final content = ConstrainedBox(
      constraints: const BoxConstraints(minHeight: kOmiMinTapTarget),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: OmiSpacing.xs),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            if (leading != null) ...[
              SizedBox(
                width: 72,
                child: Text(leading!, style: OmiType.footnote.copyWith(color: OmiColors.textSecondary), maxLines: 1),
              ),
            ],
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(title, style: OmiType.subhead, maxLines: 2, overflow: TextOverflow.ellipsis),
                  if (detail != null && detail!.isNotEmpty)
                    Text(detail!,
                        style: OmiType.footnote.copyWith(color: OmiColors.textTertiary),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis),
                ],
              ),
            ),
            if (value != null) ...[
              const SizedBox(width: OmiSpacing.xs),
              Text(value!, style: OmiType.subhead.copyWith(fontWeight: FontWeight.w600)),
            ],
          ],
        ),
      ),
    );
    if (onTap == null) return content;
    return InkWell(borderRadius: OmiRadius.smAll, onTap: onTap, child: content);
  }
}

/// A small caption between groups of rows ("Events", "Tasks due").
class TodaySubheader extends StatelessWidget {
  const TodaySubheader(this.text, {super.key});
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: OmiSpacing.xs),
        child:
            Text(text, style: OmiType.footnote.copyWith(color: OmiColors.textSecondary, fontWeight: FontWeight.w600)),
      );
}
