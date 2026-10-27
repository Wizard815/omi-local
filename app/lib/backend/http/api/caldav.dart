import 'dart:convert';

import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/caldav.dart';
import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;
import 'package:omi/env/env.dart';

String _base() => '${Env.apiBaseUrl}v1/caldav';

CalDavSettings _settings(String body) => CalDavSettings.fromGenerated(
    wire.GeneratedCalDavSettingsResponse.fromJson(jsonDecode(body) as Map<String, dynamic>));

Future<ApiResult<CalDavSettings>> getCalDavSettings() =>
    executeApi(request: ApiRequest(url: '${_base()}/settings', method: 'GET'), decode: _settings);

/// Fields left null are kept as stored; an empty [password] clears it, null keeps it.
Future<ApiResult<CalDavSettings>> updateCalDavSettings({
  String? url,
  String? username,
  String? password,
  String? calendarHref,
  String? tasksHref,
  bool? enabled,
  bool? pushTasks,
  bool? pullTasks,
  bool? pushDaySummaries,
  bool? pushConversationEvents,
  bool? pushConversations,
  bool? pullEvents,
}) {
  final payload = {
    if (url != null) 'url': url,
    if (username != null) 'username': username,
    if (password != null) 'password': password,
    if (calendarHref != null) 'calendar_href': calendarHref,
    if (tasksHref != null) 'tasks_href': tasksHref,
    if (enabled != null) 'enabled': enabled,
    if (pushTasks != null) 'push_tasks': pushTasks,
    if (pullTasks != null) 'pull_tasks': pullTasks,
    if (pushDaySummaries != null) 'push_day_summaries': pushDaySummaries,
    if (pushConversationEvents != null) 'push_conversation_events': pushConversationEvents,
    if (pushConversations != null) 'push_conversations': pushConversations,
    if (pullEvents != null) 'pull_events': pullEvents,
  };
  return executeApi(
    request: ApiRequest(
      url: '${_base()}/settings',
      method: 'PUT',
      headers: const {'Content-Type': 'application/json'},
      body: jsonEncode(payload),
    ),
    decode: _settings,
  );
}

Future<ApiResult<CalDavSettings>> deleteCalDavSettings() =>
    executeApi(request: ApiRequest(url: '${_base()}/settings', method: 'DELETE'), decode: _settings);

/// A connection test: the calendars found, or (ok false) the server's error text.
Future<ApiResult<({bool ok, int calendars, String error})>> testCalDavConnection() => executeApi(
      request: ApiRequest(url: '${_base()}/test', method: 'POST'),
      decode: (body) {
        final g = wire.GeneratedCalDavTestResponse.fromJson(jsonDecode(body) as Map<String, dynamic>);
        return (ok: g.ok, calendars: g.calendars?.length ?? 0, error: g.error);
      },
    );

Future<ApiResult<CalDavSyncOutcome>> syncCalDavNow() => executeApi(
      request: ApiRequest(url: '${_base()}/sync', method: 'POST'),
      decode: (body) => CalDavSyncOutcome.fromGenerated(
          wire.GeneratedCalDavSyncResponse.fromJson(jsonDecode(body) as Map<String, dynamic>)),
    );
