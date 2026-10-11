import 'dart:convert';

import 'package:omi/backend/http/shared.dart';
import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;
import 'package:omi/env/env.dart';
import 'package:omi/utils/logger.dart';

/// A self-hosted chat model shortcut: typing `/name …` in Ask Omi answers
/// that one message with [model] instead of the base chat model.
class ChatModelShortcut {
  final String name;
  final String? model;

  const ChatModelShortcut({required this.name, this.model});

  bool get isConfigured => model != null && model!.isNotEmpty;

  factory ChatModelShortcut.fromGenerated(wire.GeneratedChatModelShortcut g) =>
      ChatModelShortcut(name: g.name, model: g.model);
}

class AvailableChatModel {
  final String id;
  final String ownedBy;

  const AvailableChatModel({required this.id, this.ownedBy = ''});

  factory AvailableChatModel.fromGenerated(wire.GeneratedAvailableChatModel g) =>
      AvailableChatModel(id: g.id, ownedBy: g.ownedBy);
}

class ChatModelShortcutsState {
  /// False unless the backend runs a local LLM (shortcuts need the model proxy).
  final bool enabled;
  final String baseModel;
  final List<ChatModelShortcut> shortcuts;
  final List<AvailableChatModel> availableModels;

  const ChatModelShortcutsState({
    required this.enabled,
    this.baseModel = '',
    this.shortcuts = const [],
    this.availableModels = const [],
  });

  static const disabled = ChatModelShortcutsState(enabled: false);

  factory ChatModelShortcutsState.fromGenerated(wire.GeneratedChatModelShortcutsResponse g) => ChatModelShortcutsState(
        enabled: g.enabled,
        baseModel: g.baseModel,
        shortcuts: (g.shortcuts ?? const []).map(ChatModelShortcut.fromGenerated).toList(),
        availableModels: (g.availableModels ?? const []).map(AvailableChatModel.fromGenerated).toList(),
      );

  ChatModelShortcut? byName(String name) {
    for (final s in shortcuts) {
      if (s.name == name) return s;
    }
    return null;
  }
}

ChatModelShortcutsState? _parse(dynamic response, String label) {
  if (response == null) return null;
  if (response.statusCode == 200) {
    return ChatModelShortcutsState.fromGenerated(
      wire.GeneratedChatModelShortcutsResponse.fromJson(
          jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>),
    );
  }
  Logger.debug('$label failed: ${response.statusCode} ${response.body}');
  return null;
}

Future<ChatModelShortcutsState?> getChatModelShortcuts() async {
  final response = await makeApiCall(
    url: '${Env.apiBaseUrl}v1/chat/model-shortcuts',
    headers: {},
    method: 'GET',
    body: '',
  );
  return _parse(response, 'getChatModelShortcuts');
}

Future<ChatModelShortcutsState?> setChatModelShortcut(String name, String model) async {
  final response = await makeApiCall(
    url: '${Env.apiBaseUrl}v1/chat/model-shortcuts/${Uri.encodeComponent(name)}',
    headers: {'Content-Type': 'application/json'},
    method: 'PUT',
    body: jsonEncode({'model': model}),
  );
  return _parse(response, 'setChatModelShortcut');
}

Future<ChatModelShortcutsState?> deleteChatModelShortcut(String name) async {
  final response = await makeApiCall(
    url: '${Env.apiBaseUrl}v1/chat/model-shortcuts/${Uri.encodeComponent(name)}',
    headers: {},
    method: 'DELETE',
    body: '',
  );
  return _parse(response, 'deleteChatModelShortcut');
}
