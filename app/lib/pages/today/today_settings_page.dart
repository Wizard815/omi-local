import 'dart:async';

import 'package:flutter/material.dart';

import 'package:omi/services/today/today_feed_settings.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// Omi Today's settings: which cards show and in what order, the news feeds, and where the
/// Coinpile and Pondering Orb cards read from. Everything stays on this phone (tokens in secure
/// storage).
class TodaySettingsPage extends StatefulWidget {
  const TodaySettingsPage({super.key, required this.store});

  final TodayFeedSettingsStore store;

  @override
  State<TodaySettingsPage> createState() => _TodaySettingsPageState();
}

class _TodaySettingsPageState extends State<TodaySettingsPage> {
  TodayFeedSettingsStore get _store => widget.store;

  @override
  void initState() {
    super.initState();
    _store.addListener(_changed);
    unawaited(_store.ensureLoaded());
  }

  @override
  void dispose() {
    _store.removeListener(_changed);
    super.dispose();
  }

  void _changed() {
    if (mounted) setState(() {});
  }

  String cardName(TodayCardId id) {
    final l10n = context.l10n;
    return switch (id) {
      TodayCardId.agenda => l10n.todayAgendaTitle,
      TodayCardId.summary => l10n.todaySummaryTitle,
      TodayCardId.people => l10n.people,
      TodayCardId.news => l10n.todayNewsTitle,
      TodayCardId.coinpile => l10n.todayCoinpileTitle,
      TodayCardId.pondering => l10n.todayPonderingTitle,
    };
  }

  Future<void> _addFeed() async {
    final values = await showOmiEditSheet<List<String>>(
      context: context,
      builder: (_) => _TextFieldsSheet(
        title: context.l10n.todayAddFeed,
        fields: [_FieldSpec(label: context.l10n.todayFeedUrl, keyboard: TextInputType.url)],
        validate: (values) =>
            TodayFeedSettings.cleanFeedUrl(values[0]) == null ? context.l10n.todayAddressInvalid : null,
      ),
    );
    if (values == null) return;
    await _store.addFeed(values.first);
  }

  Future<void> _removeFeed(String url) async {
    final index = _store.value.feeds.indexOf(url);
    await _store.removeFeed(url);
    if (!mounted) return;
    await OmiFeedback.undo(context, context.l10n.todayFeedRemoved,
        onUndo: () => unawaited(_store.insertFeed(index, url)));
  }

  Future<void> _editCompanion(TodayCardId card) async {
    final l10n = context.l10n;
    final endpoint = card == TodayCardId.coinpile ? _store.value.coinpile : _store.value.pondering;
    final values = await showOmiEditSheet<List<String>>(
      context: context,
      builder: (_) => _TextFieldsSheet(
        title: cardName(card),
        fields: [
          _FieldSpec(label: l10n.todayCompanionAddress, initial: endpoint.url, keyboard: TextInputType.url),
          _FieldSpec(label: l10n.todayCompanionToken, obscure: true),
        ],
        validate: (values) => TodayFeedSettings.cleanBaseUrl(values[0]) == null ? l10n.todayAddressInvalid : null,
      ),
    );
    if (values == null) return;
    // An empty token field keeps the saved token; the address alone can change.
    final token = values[1].trim();
    await _store.setCompanion(card, url: values[0], token: token.isEmpty ? null : token);
    if (mounted) OmiFeedback.confirm(context, l10n.saved);
  }

  Future<void> _clearToken(TodayCardId card) async {
    final endpoint = card == TodayCardId.coinpile ? _store.value.coinpile : _store.value.pondering;
    final confirmed = await showOmiConfirm(
      context,
      title: context.l10n.connectionsClearToken,
      message: context.l10n.todayClearTokenMessage(cardName(card)),
      confirmLabel: context.l10n.connectionsClearToken,
      destructive: true,
    );
    if (confirmed) await _store.setCompanion(card, url: endpoint.url, token: '');
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final settings = _store.value;
    return Scaffold(
      appBar: AppBar(leading: const OmiBackButton(), title: Text(l10n.omiTodayCustomize)),
      body: !_store.loaded
          ? const OmiLoadingState()
          : CustomScrollView(
              slivers: [
                SliverPadding(
                  padding: const EdgeInsets.fromLTRB(OmiSpacing.md, OmiSpacing.md, OmiSpacing.md, 0),
                  sliver: SliverToBoxAdapter(child: OmiSectionHeader(l10n.todayCards)),
                ),
                SliverPadding(
                  padding: const EdgeInsets.symmetric(horizontal: OmiSpacing.md),
                  sliver: SliverReorderableList(
                    itemCount: settings.order.length,
                    onReorderItem: (from, to) => unawaited(_store.reorder(from, to)),
                    itemBuilder: (context, index) {
                      final id = settings.order[index];
                      return Material(
                        key: ValueKey('today_settings_card_${id.name}'),
                        color: OmiColors.surface1,
                        borderRadius: index == 0
                            ? const BorderRadius.vertical(top: Radius.circular(OmiRadius.lg))
                            : index == settings.order.length - 1
                                ? const BorderRadius.vertical(bottom: Radius.circular(OmiRadius.lg))
                                : null,
                        child: OmiSettingsRow.toggle(
                          leading: ReorderableDragStartListener(
                            index: index,
                            child: Semantics(
                              label: l10n.todayReorderCard(cardName(id)),
                              child: const Icon(Icons.drag_indicator),
                            ),
                          ),
                          title: cardName(id),
                          value: !settings.hidden.contains(id),
                          onChanged: (visible) => unawaited(_store.setVisible(id, visible)),
                        ),
                      );
                    },
                  ),
                ),
                SliverPadding(
                  padding: const EdgeInsets.all(OmiSpacing.md),
                  sliver: SliverList.list(
                    children: [
                      const SizedBox(height: OmiSpacing.md),
                      OmiSettingsGroup(
                        header: l10n.todayNewsFeeds,
                        headerSubtitle: l10n.todayNewsEmptyMessage,
                        children: [
                          for (final url in settings.feeds)
                            OmiSettingsRow(
                              key: ValueKey('today_feed_$url'),
                              leading: const Icon(Icons.rss_feed),
                              title: Uri.tryParse(url)?.host ?? url,
                              subtitle: url,
                              trailing: OmiIconButton(
                                icon: const Icon(Icons.remove_circle_outline),
                                label: l10n.todayRemoveFeed,
                                onPressed: () => unawaited(_removeFeed(url)),
                              ),
                            ),
                          if (settings.feeds.length < TodayFeedSettings.maxFeeds)
                            OmiSettingsRow(
                              key: const Key('today_add_feed'),
                              leading: const Icon(Icons.add),
                              title: l10n.todayAddFeed,
                              onTap: () => unawaited(_addFeed()),
                            ),
                        ],
                      ),
                      const SizedBox(height: OmiSpacing.xl),
                      for (final card in const [TodayCardId.coinpile, TodayCardId.pondering]) ...[
                        _companionGroup(card),
                        const SizedBox(height: OmiSpacing.xl),
                      ],
                    ],
                  ),
                ),
              ],
            ),
    );
  }

  Widget _companionGroup(TodayCardId card) {
    final l10n = context.l10n;
    final endpoint = card == TodayCardId.coinpile ? _store.value.coinpile : _store.value.pondering;
    return OmiSettingsGroup(
      header: cardName(card),
      headerSubtitle: card == TodayCardId.coinpile ? l10n.todayCoinpileHint : l10n.todayPonderingHint,
      children: [
        OmiSettingsRow(
          key: ValueKey('today_companion_${card.name}'),
          leading: const Icon(Icons.link),
          title: l10n.todayCompanionAddress,
          value: endpoint.configured ? Uri.tryParse(endpoint.url)?.host ?? endpoint.url : l10n.notSet,
          onTap: () => unawaited(_editCompanion(card)),
        ),
        OmiSettingsRow(
          leading: const Icon(Icons.key_outlined),
          title: l10n.todayCompanionToken,
          value: endpoint.hasToken ? l10n.saved : l10n.notSet,
          onTap: () => unawaited(endpoint.hasToken ? _clearToken(card) : _editCompanion(card)),
        ),
      ],
    );
  }
}

class _FieldSpec {
  const _FieldSpec({required this.label, this.initial = '', this.obscure = false, this.keyboard});
  final String label;
  final String initial;
  final bool obscure;
  final TextInputType? keyboard;
}

/// An edit sheet of a few text fields with Save; resolves with the values, or null.
class _TextFieldsSheet extends StatefulWidget {
  const _TextFieldsSheet({required this.title, required this.fields, required this.validate});

  final String title;
  final List<_FieldSpec> fields;

  /// An error to show under the first field, or null when the values can be saved.
  final String? Function(List<String> values) validate;

  @override
  State<_TextFieldsSheet> createState() => _TextFieldsSheetState();
}

class _TextFieldsSheetState extends State<_TextFieldsSheet> {
  late final List<TextEditingController> _controllers = [
    for (final field in widget.fields) TextEditingController(text: field.initial),
  ];
  String? _error;

  @override
  void dispose() {
    for (final c in _controllers) {
      c.dispose();
    }
    super.dispose();
  }

  bool get _dirty {
    for (var i = 0; i < _controllers.length; i++) {
      if (_controllers[i].text != widget.fields[i].initial) return true;
    }
    return false;
  }

  void _save() {
    final values = [for (final c in _controllers) c.text.trim()];
    final error = widget.validate(values);
    if (error != null) {
      setState(() => _error = error);
      return;
    }
    Navigator.of(context).pop(values);
  }

  @override
  Widget build(BuildContext context) {
    return OmiEditSheet(
      title: widget.title,
      isDirty: _dirty,
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (var i = 0; i < widget.fields.length; i++) ...[
              Text(widget.fields[i].label, style: OmiType.footnote.copyWith(color: OmiColors.textSecondary)),
              const SizedBox(height: OmiSpacing.xs),
              TextField(
                key: ValueKey('today_field_$i'),
                controller: _controllers[i],
                autofocus: i == 0,
                obscureText: widget.fields[i].obscure,
                autocorrect: false,
                enableSuggestions: false,
                keyboardType: widget.fields[i].keyboard,
                onChanged: (_) => setState(() => _error = null),
                style: OmiType.callout,
                cursorColor: OmiColors.accent,
                decoration: InputDecoration(
                  errorText: i == 0 ? _error : null,
                  filled: true,
                  fillColor: OmiColors.surface2,
                  contentPadding: const EdgeInsets.symmetric(horizontal: OmiSpacing.md, vertical: 14),
                  border: const OutlineInputBorder(borderRadius: OmiRadius.mdAll, borderSide: BorderSide.none),
                ),
              ),
              const SizedBox(height: OmiSpacing.md),
            ],
            OmiButton(key: const Key('today_sheet_save'), label: context.l10n.save, expand: true, onPressed: _save),
          ],
        ),
      ),
    );
  }
}
