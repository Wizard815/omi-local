import 'package:flutter/material.dart';

import 'package:intl/intl.dart';

import 'package:omi/backend/http/api/caldav.dart';
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/caldav.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/alerts/app_snackbar.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// omi-local: where the tasks and calendar sync goes (CalDAV) and what moves.
/// The stack's bundled server is used until the user enters their own.
class CalDavSettingsPage extends StatefulWidget {
  const CalDavSettingsPage({super.key});

  @override
  State<CalDavSettingsPage> createState() => _CalDavSettingsPageState();
}

class _CalDavSettingsPageState extends State<CalDavSettingsPage> {
  CalDavSettings? _settings;
  bool _loading = true;
  bool _busy = false;
  final _url = TextEditingController();
  final _username = TextEditingController();
  final _password = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _url.dispose();
    _username.dispose();
    _password.dispose();
    super.dispose();
  }

  /// The settings behind a result, or null after showing the failure (the page keeps what it had).
  CalDavSettings? _unwrap(ApiResult<CalDavSettings> result) {
    switch (result) {
      case ApiSuccess(:final data):
        return data;
      case ApiFailure(:final problem):
        AppSnackbar.showSnackbarError(context.l10n.caldavFailed(problem.kind.name));
        return null;
    }
  }

  Future<void> _load() async {
    final result = await getCalDavSettings();
    if (!mounted) return;
    final settings = _unwrap(result);
    setState(() {
      if (settings != null) _settings = settings;
      _loading = false;
      if (settings != null && !settings.bundled) {
        _url.text = settings.url;
        _username.text = settings.username;
      }
    });
  }

  Future<void> _run(Future<void> Function() work) async {
    setState(() => _busy = true);
    try {
      await work();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _save() => _run(() async {
        final password = _password.text;
        final result = await updateCalDavSettings(
          url: _url.text.trim(),
          username: _username.text.trim(),
          password: password.isEmpty ? null : password,
        );
        if (!mounted) return;
        final saved = _unwrap(result);
        if (saved == null) return;
        _password.clear();
        setState(() => _settings = saved);
        AppSnackbar.showSnackbar(context.l10n.caldavSaved);
      });

  Future<void> _toggle(String field, bool value) => _run(() async {
        final result = await updateCalDavSettings(
          enabled: field == 'enabled' ? value : null,
          pushTasks: field == 'push_tasks' ? value : null,
          pullTasks: field == 'pull_tasks' ? value : null,
          pushDaySummaries: field == 'push_day_summaries' ? value : null,
          pushConversationEvents: field == 'push_conversation_events' ? value : null,
          pushConversations: field == 'push_conversations' ? value : null,
          pullEvents: field == 'pull_events' ? value : null,
        );
        if (!mounted) return;
        final saved = _unwrap(result);
        if (saved != null) setState(() => _settings = saved);
      });

  Future<void> _test() => _run(() async {
        final result = await testCalDavConnection();
        if (!mounted) return;
        switch (result) {
          case ApiSuccess(:final data) when data.ok:
            AppSnackbar.showSnackbar(context.l10n.caldavCalendarsFound(data.calendars));
          case ApiSuccess(:final data):
            AppSnackbar.showSnackbarError(context.l10n.caldavFailed(data.error));
          case ApiFailure(:final problem):
            AppSnackbar.showSnackbarError(context.l10n.caldavFailed(problem.kind.name));
        }
      });

  Future<void> _sync() => _run(() async {
        final result = await syncCalDavNow();
        if (!mounted) return;
        switch (result) {
          case ApiSuccess(:final data) when data.ok || data.error.isEmpty:
            AppSnackbar.showSnackbar(context.l10n.caldavSyncResult(
                data.tasksPushed, data.eventsPushed, data.tasksPulled + data.tasksImported + data.eventsPulled));
          case ApiSuccess(:final data):
            AppSnackbar.showSnackbarError(context.l10n.caldavFailed(data.error));
          case ApiFailure(:final problem):
            AppSnackbar.showSnackbarError(context.l10n.caldavFailed(problem.kind.name));
        }
        await _load();
      });

  Future<void> _forget() => _run(() async {
        final result = await deleteCalDavSettings();
        if (!mounted) return;
        final settings = _unwrap(result);
        if (settings == null) return;
        _url.clear();
        _username.clear();
        _password.clear();
        setState(() => _settings = settings);
      });

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    final settings = _settings;
    return Scaffold(
      backgroundColor: Theme.of(context).colorScheme.primary,
      appBar: AppBar(
        backgroundColor: Theme.of(context).colorScheme.primary,
        title: Text(l10n.caldavSettingsTitle),
      ),
      body: _loading
          ? const Center(child: OmiSpinner())
          : ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
              children: [
                Text(l10n.caldavSettingsIntro, style: TextStyle(color: Colors.grey.shade400, height: 1.4)),
                const SizedBox(height: 12),
                _status(settings),
                const SizedBox(height: 20),
                _field(_url, l10n.caldavServerUrl, hint: 'https://cloud.example.org/remote.php/dav/'),
                _field(_username, l10n.caldavUsername),
                _field(_password, l10n.caldavPassword,
                    obscure: true,
                    helper:
                        settings != null && settings.hasPassword && !settings.bundled ? l10n.caldavPasswordKept : null),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Expanded(
                      child: ElevatedButton(
                        key: const Key('caldav_save'),
                        style: _primary,
                        onPressed: _busy ? null : _save,
                        child: Text(l10n.caldavSave),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: OutlinedButton(
                        key: const Key('caldav_test'),
                        style: _secondary,
                        onPressed: _busy || settings == null || !settings.configured ? null : _test,
                        child: Text(l10n.caldavTestConnection),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                SizedBox(
                  width: double.infinity,
                  child: OutlinedButton.icon(
                    key: const Key('caldav_sync_now'),
                    style: _secondary,
                    onPressed: _busy || settings == null || !settings.configured ? null : _sync,
                    icon: const Icon(Icons.sync_rounded, size: 18),
                    label: Text(l10n.caldavSyncNow),
                  ),
                ),
                if (settings != null && settings.configured && !settings.bundled)
                  TextButton(
                    onPressed: _busy ? null : _forget,
                    child: Text(l10n.caldavForget, style: TextStyle(color: Colors.grey.shade500)),
                  ),
                if (settings != null && settings.configured) ...[
                  const SizedBox(height: 16),
                  _switch('enabled', l10n.caldavEnabled, settings.enabled),
                  _switch('push_tasks', l10n.caldavPushTasks, settings.pushTasks),
                  _switch('pull_tasks', l10n.caldavPullTasks, settings.pullTasks),
                  _switch('push_day_summaries', l10n.caldavPushDaySummaries, settings.pushDaySummaries),
                  _switch(
                      'push_conversation_events', l10n.caldavPushConversationEvents, settings.pushConversationEvents),
                  _switch('push_conversations', l10n.caldavPushConversations, settings.pushConversations),
                  _switch('pull_events', l10n.caldavPullEvents, settings.pullEvents),
                ],
              ],
            ),
    );
  }

  ButtonStyle get _primary => ElevatedButton.styleFrom(
        backgroundColor: Colors.white,
        foregroundColor: Colors.black,
        padding: const EdgeInsets.symmetric(vertical: 14),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(OmiRadius.md)),
      );

  ButtonStyle get _secondary => OutlinedButton.styleFrom(
        foregroundColor: Colors.white,
        side: const BorderSide(color: Colors.white54),
        padding: const EdgeInsets.symmetric(vertical: 14),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(OmiRadius.md)),
      );

  Widget _status(CalDavSettings? settings) {
    final l10n = context.l10n;
    final lines = <String>[];
    if (settings == null || !settings.configured) {
      lines.add(l10n.caldavNotConfigured);
    } else if (settings.bundled) {
      lines.add(l10n.caldavUsingBundled(settings.url));
    }
    if (settings?.lastSyncAt != null) {
      lines.add(l10n.caldavLastSync(DateFormat.yMMMd().add_jm().format(settings!.lastSyncAt!)));
    }
    if (settings != null && settings.lastError.isNotEmpty) lines.add(l10n.caldavLastError(settings.lastError));
    if (lines.isEmpty) return const SizedBox.shrink();
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(color: OmiColors.surface1, borderRadius: BorderRadius.circular(OmiRadius.md)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final line in lines)
            Padding(
              padding: const EdgeInsets.only(bottom: 2),
              child: Text(line,
                  style: OmiType.footnote.copyWith(
                      color: line.startsWith(l10n.caldavLastError('').trimRight())
                          ? Colors.red.shade200
                          : Colors.grey.shade300)),
            ),
        ],
      ),
    );
  }

  Widget _field(TextEditingController controller, String label, {String? hint, bool obscure = false, String? helper}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: TextField(
        controller: controller,
        obscureText: obscure,
        autocorrect: false,
        style: const TextStyle(color: Colors.white),
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          helperText: helper,
          labelStyle: TextStyle(color: Colors.grey.shade400),
          hintStyle: TextStyle(color: Colors.grey.shade600),
          helperStyle: TextStyle(color: Colors.grey.shade500),
          filled: true,
          fillColor: OmiColors.surface1,
          border: OutlineInputBorder(borderRadius: BorderRadius.circular(OmiRadius.md), borderSide: BorderSide.none),
        ),
      ),
    );
  }

  Widget _switch(String field, String label, bool value) => SwitchListTile(
        key: Key('caldav_switch_$field'),
        contentPadding: EdgeInsets.zero,
        title: Text(label, style: const TextStyle(color: Colors.white)),
        value: value,
        activeThumbColor: Colors.white,
        onChanged: _busy ? null : (v) => _toggle(field, v),
      );
}
