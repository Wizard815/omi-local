import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import 'package:file_picker/file_picker.dart';
import 'package:font_awesome_flutter/font_awesome_flutter.dart';
import 'package:provider/provider.dart';
import 'package:share_plus/share_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import 'package:omi/backend/preferences.dart';
import 'package:omi/env/env.dart';
import 'package:omi/pages/settings/developer_firmware_flash_page.dart';
import 'package:omi/pages/settings/developer_mcp_section.dart';
import 'package:omi/pages/settings/settings_destinations.dart';
import 'package:omi/pages/settings/settings_search_index.dart';
import 'package:omi/pages/settings/widgets/developer_api_keys_section.dart';
import 'package:omi/providers/developer_mode_provider.dart';
import 'package:omi/providers/device_provider.dart';
import 'package:omi/providers/mcp_provider.dart';
import 'package:omi/services/device_calendar_service.dart';
import 'package:omi/services/device_calendar_sync.dart';
import 'package:omi/ui/ui.dart';
import 'package:omi/utils/debug_log_manager.dart';
import 'package:omi/utils/firmware_update_build_policy.dart';
import 'package:omi/utils/l10n_extensions.dart';
import 'package:omi/utils/logger.dart';
import 'package:omi/utils/other/api_url.dart';
import 'package:omi/utils/other/temp.dart';

/// Developer Settings: developer tools only (D4) — the self-hosting server (URL, models, device
/// calendar), debug logs, API keys, MCP, webhooks, experiments and custom firmware. Everyday
/// settings live in top-level Settings. Creator payouts are left out: no billing when self-hosted.
///
/// Every switch applies when flipped. Webhook URLs and the custom backend URL wait for Save, and
/// leaving with unsaved edits asks first (chat-apps-settings #2, nav #24).
class DeveloperSettingsPage extends StatelessWidget {
  const DeveloperSettingsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider(
      create: (_) => DeveloperModeProvider()..initialize(),
      child: const _DeveloperSettingsPageView(),
    );
  }
}

class _DeveloperSettingsPageView extends StatefulWidget {
  const _DeveloperSettingsPageView();

  @override
  State<_DeveloperSettingsPageView> createState() => _DeveloperSettingsPageState();
}

class _DeveloperSettingsPageState extends State<_DeveloperSettingsPageView> {
  // Self-hosting: the server this app talks to. Saved to customApiBaseUrl, which
  // Env.apiBaseUrl prefers over the build-time define, so unlike the debug-only
  // custom backend URL below it also works in release builds.
  final TextEditingController _serverUrlController = TextEditingController(text: Env.apiBaseUrl ?? '');

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) context.read<McpProvider>().fetchKeys();
    });
  }

  @override
  void dispose() {
    _serverUrlController.dispose();
    super.dispose();
  }

  // iPad requires a non-zero sharePositionOrigin (popover anchor) for the share sheet.
  Rect _shareOrigin() {
    if (!mounted) return const Rect.fromLTWH(0, 0, 100, 100);
    final box = context.findRenderObject() as RenderBox?;
    if (box != null && box.hasSize && box.size.width > 0 && box.size.height > 0) {
      return box.localToGlobal(Offset.zero) & box.size;
    }
    return const Rect.fromLTWH(0, 0, 100, 100);
  }

  /// Asks before throwing away unsaved webhook or backend URL edits. Resolves whether the page may close.
  Future<bool> _confirmDiscard(DeveloperModeProvider provider) async {
    final l10n = context.l10n;
    final discard = await showOmiConfirm(
      context,
      title: l10n.discardChangesTitle,
      message: l10n.discardChangesMessage,
      confirmLabel: l10n.discard,
      cancelLabel: l10n.keepEditing,
      destructive: true,
    );
    if (discard) provider.discardWebhookChanges();
    return discard;
  }

  Future<void> _shareLogs() async {
    final l10n = context.l10n;
    final files = await DebugLogManager.listLogFiles();
    if (!mounted) return;
    if (files.isEmpty) {
      OmiFeedback.info(context, l10n.noLogFilesFound);
      return;
    }
    File? selected = files.length == 1 ? files.first : null;
    selected ??= await showOmiSheet<File>(
      context: context,
      title: l10n.selectLogFile,
      builder: (sheetContext) => SingleChildScrollView(
        child: OmiSettingsGroup(
          children: [
            for (final f in files)
              OmiSettingsRow(title: f.uri.pathSegments.last, onTap: () => Navigator.of(sheetContext).pop(f)),
          ],
        ),
      ),
    );
    if (selected == null || !mounted) return;
    final result = await SharePlus.instance.share(
      ShareParams(files: [XFile(selected.path)], text: l10n.omiDebugLog, sharePositionOrigin: _shareOrigin()),
    );
    if (result.status == ShareResultStatus.success) Logger.debug('Log shared');
  }

  Future<void> _pickFirmware(DeviceProvider provider) async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['zip'],
      dialogTitle: context.l10n.selectFirmwareZip,
    );
    if (result == null || result.files.isEmpty) return;
    final file = result.files.first;
    if (file.path == null || !mounted) return;
    await routeToPage(
      context,
      DeveloperFirmwareFlashPage(zipFilePath: file.path!, fileName: file.name, device: provider.pairedDevice!),
    );
  }

  Widget _buildDebugLogs() {
    final l10n = context.l10n;
    final enabled = SharedPreferencesUtil().devLogsToFileEnabled;
    return OmiSettingsGroup(
      header: l10n.debugAndDiagnostics,
      children: [
        OmiSettingsRow.toggle(
          leading: const FaIcon(FontAwesomeIcons.bug),
          title: l10n.debugLogs,
          subtitle: enabled ? l10n.autoDeletesAfterThreeDays : l10n.helpsDiagnoseIssues,
          value: enabled,
          onChanged: (v) async {
            await DebugLogManager.setEnabled(v);
            if (mounted) setState(() {});
          },
        ),
        if (enabled)
          Padding(
            padding: const EdgeInsets.all(OmiSpacing.md),
            child: Row(
              children: [
                Expanded(
                  child: OmiButton.secondary(
                    label: l10n.shareLogs,
                    leading: const FaIcon(FontAwesomeIcons.fileArrowUp),
                    size: OmiButtonSize.compact,
                    onPressed: _shareLogs,
                  ),
                ),
                const SizedBox(width: OmiSpacing.sm),
                OmiButton.destructive(
                  label: l10n.clear,
                  leading: const FaIcon(FontAwesomeIcons.trash),
                  size: OmiButtonSize.compact,
                  onPressed: () async {
                    final message = l10n.debugLogCleared;
                    await DebugLogManager.clear();
                    if (mounted) OmiFeedback.confirm(context, message);
                  },
                ),
              ],
            ),
          ),
      ],
    );
  }

  Widget _buildWebhookItem({
    required String title,
    required String description,
    required FaIconData icon,
    required bool isEnabled,
    required ValueChanged<bool> onToggle,
    required TextEditingController controller,
    Widget? extraField,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        OmiSettingsRow.toggle(
          leading: FaIcon(icon),
          title: title,
          subtitle: description,
          value: isEnabled,
          onChanged: onToggle,
        ),
        if (isEnabled)
          Padding(
            padding: const EdgeInsets.fromLTRB(OmiSpacing.md, 0, OmiSpacing.md, OmiSpacing.md),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _DeveloperTextField(controller: controller, label: context.l10n.endpointUrl),
                if (extraField != null) ...[const SizedBox(height: OmiSpacing.xs), extraField],
              ],
            ),
          ),
      ],
    );
  }

  Widget _buildCustomBackendUrl(DeveloperModeProvider provider) {
    return OmiSettingsGroup(
      header: context.l10n.customBackendUrlTitle,
      children: [
        Padding(
          padding: const EdgeInsets.all(OmiSpacing.md),
          child: _DeveloperTextField(
            controller: provider.customBackendUrl,
            label: context.l10n.backendUrlLabel,
            hint: 'https://omi.example.com/',
          ),
        ),
      ],
    );
  }

  void _saveServerUrl() {
    final url = _serverUrlController.text.trim();
    if (url.isEmpty) return;
    final normalized = url.endsWith('/') ? url : '$url/';
    Env.overrideApiBaseUrl(normalized);
    SharedPreferencesUtil().customApiBaseUrl = normalized;
    _serverUrlController.text = normalized;
    setState(() {});
    OmiFeedback.confirm(context, 'Server URL set to $normalized\nRestart the app for it to take full effect.');
  }

  Widget _buildSelfHostServerUrl() {
    return OmiSettingsGroup(
      header: 'Server URL',
      headerSubtitle: 'Change the backend server address for self-hosting. Current: ${Env.apiBaseUrl ?? ''}',
      children: [
        Padding(
          padding: const EdgeInsets.all(OmiSpacing.md),
          child: Row(
            children: [
              Expanded(
                child: _DeveloperTextField(
                  controller: _serverUrlController,
                  label: 'Server URL',
                  hint: 'http://192.168.20.5:8000/',
                ),
              ),
              const SizedBox(width: OmiSpacing.sm),
              OmiButton.secondary(label: 'Save', size: OmiButtonSize.compact, onPressed: _saveServerUrl),
            ],
          ),
        ),
      ],
    );
  }

  Future<void> _openServerPage(String path) async {
    final url = Uri.parse(joinApiUrl(Env.apiBaseUrl ?? '', path));
    if (await canLaunchUrl(url)) {
      await launchUrl(url, mode: LaunchMode.externalApplication);
    }
  }

  Widget _buildSelfHostModels() {
    return OmiSettingsGroup(
      header: 'LLM Models',
      headerSubtitle: 'Server models are configured server-side. Visit the dashboard to view and change them.',
      children: [
        OmiSettingsRow(
          leading: const FaIcon(FontAwesomeIcons.chartLine),
          title: 'Open Server Dashboard',
          onTap: () => _openServerPage(''),
        ),
        OmiSettingsRow(
          leading: const FaIcon(FontAwesomeIcons.gear),
          title: 'View Model Config (JSON)',
          onTap: () => _openServerPage('dashboard/models'),
        ),
        Padding(
          padding: const EdgeInsets.all(OmiSpacing.md),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text('Start server with:', style: OmiType.caption.copyWith(color: OmiColors.textTertiary)),
              const SizedBox(height: OmiSpacing.xs),
              Container(
                padding: const EdgeInsets.all(OmiSpacing.sm),
                decoration: BoxDecoration(color: OmiColors.surface2, borderRadius: OmiRadius.mdAll),
                child: Text(
                  'OPENAI_BASE_URL=http://host:8081/v1\nOMI_LOCAL_MODEL=gemma-4-12b\nLOCAL_LLM_MODEL=gemma-4-12b\nbash run-omi-local-host.sh',
                  style: OmiType.caption.copyWith(
                    color: OmiColors.textSecondary,
                    fontFamily: 'Ubuntu Mono',
                    height: 1.5,
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Future<void> _syncDeviceCalendar() async {
    final result = await DeviceCalendarSync.syncNow();
    if (!mounted) return;
    OmiFeedback.confirm(context, result['message']?.toString() ?? 'Sync complete');
  }

  Future<void> _showDeviceCalendars() async {
    final calendars = await DeviceCalendarService.getCalendars();
    if (!mounted) return;
    await showOmiSheet<void>(
      context: context,
      title: 'Calendars on Device',
      builder: (sheetContext) => SingleChildScrollView(
        child: OmiSettingsGroup(
          children: [
            if (calendars.isEmpty) const OmiSettingsRow(title: 'No calendars found'),
            for (final c in calendars) OmiSettingsRow(title: '${c['name']}', subtitle: '${c['accountType']}'),
          ],
        ),
      ),
    );
  }

  Widget _buildDeviceCalendar() {
    return OmiSettingsGroup(
      header: 'Calendar Provider',
      headerSubtitle: 'Use device calendar instead of Google Cloud. Syncs any calendar Android knows.',
      footer: 'Reads Android CalendarContract directly: works with DAVx5 (Nextcloud), Google, Exchange and '
          'local calendars. No cloud API needed.',
      children: [
        OmiSettingsRow(
          leading: const FaIcon(FontAwesomeIcons.calendarCheck),
          title: 'Sync Device Calendar Now',
          onTap: _syncDeviceCalendar,
        ),
        OmiSettingsRow(
          leading: const FaIcon(FontAwesomeIcons.list),
          title: 'List Device Calendars',
          onTap: _showDeviceCalendars,
        ),
      ],
    );
  }

  Widget _buildWebhooks(DeveloperModeProvider provider) {
    final l10n = context.l10n;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        OmiSectionHeader(
          l10n.webhooks,
          trailing: const DeveloperDocsButton(
            url: 'https://docs.omi.me/doc/developer/apps/Introduction',
            analyticsLabel: 'Webhooks',
          ),
        ),
        OmiSettingsGroup(
          children: [
            _buildWebhookItem(
              title: l10n.conversationEvents,
              description: l10n.newConversationCreated,
              icon: FontAwesomeIcons.message,
              isEnabled: provider.conversationEventsToggled,
              onToggle: provider.onConversationEventsToggled,
              controller: provider.webhookOnConversationCreated,
            ),
            _buildWebhookItem(
              title: l10n.realTimeTranscript,
              description: l10n.transcriptReceived,
              icon: FontAwesomeIcons.closedCaptioning,
              isEnabled: provider.transcriptsToggled,
              onToggle: provider.onTranscriptsToggled,
              controller: provider.webhookOnTranscriptReceived,
            ),
            _buildWebhookItem(
              title: l10n.audioBytes,
              description: l10n.audioDataReceived,
              icon: FontAwesomeIcons.waveSquare,
              isEnabled: provider.audioBytesToggled,
              onToggle: provider.onAudioBytesToggled,
              controller: provider.webhookAudioBytes,
              extraField: _DeveloperTextField(
                controller: provider.webhookAudioBytesDelay,
                label: l10n.intervalSeconds,
                keyboardType: TextInputType.number,
              ),
            ),
            _buildWebhookItem(
              title: l10n.daySummary,
              description: l10n.summaryGenerated,
              icon: FontAwesomeIcons.calendarDay,
              isEnabled: provider.daySummaryToggled,
              onToggle: provider.onDaySummaryToggled,
              controller: provider.webhookDaySummary,
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildExperimental(DeveloperModeProvider provider) {
    final l10n = context.l10n;
    return OmiSettingsGroup(
      header: l10n.experimental,
      children: [
        OmiSettingsRow.toggle(
          leading: const FaIcon(FontAwesomeIcons.code),
          title: l10n.conversationDeveloperTools,
          subtitle: l10n.conversationDeveloperToolsDescription,
          value: SharedPreferencesUtil().devModeEnabled,
          onChanged: (v) => setState(() => SharedPreferencesUtil().devModeEnabled = v),
        ),
        OmiSettingsRow.toggle(
          leading: const FaIcon(FontAwesomeIcons.stethoscope),
          title: l10n.transcriptionDiagnostics,
          subtitle: l10n.detailedDiagnosticMessages,
          value: provider.transcriptionDiagnosticEnabled,
          onChanged: provider.onTranscriptionDiagnosticChanged,
        ),
        OmiSettingsRow.toggle(
          leading: const FaIcon(FontAwesomeIcons.microphoneSlash),
          title: l10n.vadGate,
          subtitle: l10n.vadGateDescription,
          value: provider.vadGateEnabled,
          onChanged: provider.onVadGateChanged,
        ),
      ],
    );
  }

  Widget _buildFirmware() {
    final deviceProvider = context.watch<DeviceProvider>();
    if (!FirmwareUpdateBuildPolicy.current.allowsOmiFirmwareUpdate ||
        !deviceProvider.isConnected ||
        deviceProvider.pairedDevice == null) {
      return const SizedBox.shrink();
    }
    final l10n = context.l10n;
    return Padding(
      padding: const EdgeInsets.only(top: OmiSpacing.xxl),
      child: OmiSettingsGroup(
        header: l10n.firmware,
        headerSubtitle: l10n.flashCustomFirmwareDescription,
        children: [
          OmiSettingsRow(
            leading: const FaIcon(FontAwesomeIcons.microchip),
            title: l10n.flashCustomFirmware,
            onTap: () => _pickFirmware(deviceProvider),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final l10n = context.l10n;
    return Consumer<DeveloperModeProvider>(
      builder: (context, provider, child) {
        final dirty = provider.hasUnsavedWebhookChanges;
        return PopScope(
          canPop: !dirty,
          onPopInvokedWithResult: (didPop, _) async {
            if (didPop) return;
            if (await _confirmDiscard(provider) && context.mounted) Navigator.of(context).pop();
          },
          child: GestureDetector(
            onTap: () => FocusScope.of(context).unfocus(),
            child: Scaffold(
              appBar: AppBar(
                leading: const OmiBackButton(),
                title: Text(l10n.developerSettings),
                actions: [
                  if (dirty || provider.savingSettingsLoading)
                    Padding(
                      padding: const EdgeInsets.only(right: OmiSpacing.xs),
                      child: Center(
                        child: OmiButton.tertiary(
                          label: l10n.save,
                          size: OmiButtonSize.compact,
                          isLoading: provider.savingSettingsLoading,
                          onPressed: provider.saveSettings,
                        ),
                      ),
                    ),
                ],
              ),
              body: ListView(
                padding: const EdgeInsets.fromLTRB(OmiSpacing.lg, OmiSpacing.xs, OmiSpacing.lg, OmiSpacing.xxl),
                children: [
                  // Agent setup leads: most people open this page to connect Claude, ChatGPT or
                  // Codex, and the web developer home is where prompts and keys live.
                  OmiSettingsGroup(
                    children: [
                      OmiSettingsRow(
                        key: const ValueKey('developer_connect_agent'),
                        leading: const FaIcon(FontAwesomeIcons.terminal),
                        title: l10n.connectAnAgent,
                        subtitle: l10n.connectAnAgentSubtitle,
                        showChevron: true,
                        onTap: () => openSettingsDestination(context, SettingsDestination.connectAgent),
                      ),
                    ],
                  ),
                  const SizedBox(height: OmiSpacing.xxl),
                  // Self-hosting: server address, server-side models and device calendar sync.
                  _buildSelfHostServerUrl(),
                  const SizedBox(height: OmiSpacing.xxl),
                  _buildSelfHostModels(),
                  const SizedBox(height: OmiSpacing.xxl),
                  _buildDeviceCalendar(),
                  const SizedBox(height: OmiSpacing.xxl),
                  _buildDebugLogs(),
                  const SizedBox(height: OmiSpacing.xxl),
                  const DeveloperApiKeysSection(),
                  const SizedBox(height: OmiSpacing.xxl),
                  const DeveloperMcpSection(),
                  const SizedBox(height: OmiSpacing.xxl),
                  if (!kReleaseMode) ...[_buildCustomBackendUrl(provider), const SizedBox(height: OmiSpacing.xxl)],
                  _buildWebhooks(provider),
                  const SizedBox(height: OmiSpacing.xxl),
                  _buildExperimental(provider),
                  _buildFirmware(),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

class _DeveloperTextField extends StatelessWidget {
  const _DeveloperTextField({required this.controller, required this.label, this.hint, this.keyboardType});

  final TextEditingController controller;
  final String label;
  final String? hint;
  final TextInputType? keyboardType;

  @override
  Widget build(BuildContext context) {
    const border = OutlineInputBorder(borderRadius: OmiRadius.mdAll, borderSide: BorderSide.none);
    return TextField(
      controller: controller,
      keyboardType: keyboardType ?? TextInputType.url,
      autocorrect: false,
      style: OmiType.subhead,
      decoration: InputDecoration(
        labelText: label,
        hintText: hint,
        labelStyle: OmiType.subhead.copyWith(color: OmiColors.textTertiary),
        filled: true,
        fillColor: OmiColors.surface2,
        contentPadding: const EdgeInsets.symmetric(horizontal: OmiSpacing.md, vertical: OmiSpacing.sm),
        border: border,
        enabledBorder: border,
        focusedBorder: OutlineInputBorder(
          borderRadius: OmiRadius.mdAll,
          borderSide: BorderSide(color: OmiColors.textTertiary),
        ),
      ),
    );
  }
}
