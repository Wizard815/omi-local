import 'dart:convert';

import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/caldav.dart';
import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;
import 'package:omi/env/env.dart';

CalDavSettings _settings(String body) => CalDavSettings.fromGenerated(
    wire.GeneratedCalDavSettingsResponse.fromJson(jsonDecode(body) as Map<String, dynamic>));

Future<ApiResult<CalDavSettings>> getCalDavSettings() =>
    executeApi(request: ApiRequest(url: '${Env.apiBaseUrl}v1/caldav/settings', method: 'GET'), decode: _settings);

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
  String? taskMode,
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
    if (taskMode != null) 'task_mode': taskMode,
  };
  return executeApi(
    request: ApiRequest(
      url: '${Env.apiBaseUrl}v1/caldav/settings',
      method: 'PUT',
      headers: const {'Content-Type': 'application/json'},
      body: jsonEncode(payload),
    ),
    decode: _settings,
  );
}

Future<ApiResult<CalDavSettings>> deleteCalDavSettings() =>
    executeApi(request: ApiRequest(url: '${Env.apiBaseUrl}v1/caldav/settings', method: 'DELETE'), decode: _settings);

/// A connection test: the calendars found, or (ok false) the server's error text.
Future<ApiResult<({bool ok, int calendars, String error})>> testCalDavConnection() => executeApi(
      request: ApiRequest(url: '${Env.apiBaseUrl}v1/caldav/test', method: 'POST'),
      decode: (body) {
        final g = wire.GeneratedCalDavTestResponse.fromJson(jsonDecode(body) as Map<String, dynamic>);
        return (ok: g.ok, calendars: g.calendars?.length ?? 0, error: g.error);
      },
    );

Future<ApiResult<CalDavSyncOutcome>> syncCalDavNow() => executeApi(
      request: ApiRequest(url: '${Env.apiBaseUrl}v1/caldav/sync', method: 'POST'),
      decode: (body) => CalDavSyncOutcome.fromGenerated(
          wire.GeneratedCalDavSyncResponse.fromJson(jsonDecode(body) as Map<String, dynamic>)),
    );

/// The server's calendars and task lists for the picker (who else on this server uses each).
Future<ApiResult<({bool ok, List<CalDavCollection> collections, String error})>> listCalDavCollections() => executeApi(
      request: ApiRequest(url: '${Env.apiBaseUrl}v1/caldav/collections', method: 'GET'),
      decode: (body) {
        final g = wire.GeneratedCalDavCollectionsResponse.fromJson(jsonDecode(body) as Map<String, dynamic>);
        return (
          ok: g.ok,
          collections: (g.collections ?? const []).map(CalDavCollection.fromGenerated).toList(),
          error: g.error,
        );
      },
    );

/// Makes a new calendar (`kind` 'calendar') or task list ('tasks') on the server and picks it.
Future<ApiResult<({bool ok, CalDavSettings? settings, String error})>> createCalDavCollection({
  required String name,
  required String kind,
}) =>
    executeApi(
      request: ApiRequest(
        url: '${Env.apiBaseUrl}v1/caldav/collections',
        method: 'POST',
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({'name': name, 'kind': kind, 'select': true}),
      ),
      decode: (body) {
        final g = wire.GeneratedCalDavCollectionResponse.fromJson(jsonDecode(body) as Map<String, dynamic>);
        final settings = g.settings;
        return (ok: g.ok, settings: settings == null ? null : CalDavSettings.fromGenerated(settings), error: g.error);
      },
    );
