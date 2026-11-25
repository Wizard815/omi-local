import 'dart:convert';

import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/family_calendar.dart';
import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;
import 'package:omi/env/env.dart';

String _base() => '${Env.apiBaseUrl}v1/family-calendar';

Map<String, dynamic> _json(String body) => jsonDecode(body) as Map<String, dynamic>;

/// Whether a family calendar is set, and the events waiting for the user's check. With
/// [conversationId], every event heard in that one conversation (the conversation page).
Future<ApiResult<FamilyCalendar>> getFamilyCalendar({String? conversationId}) => executeApi(
      request: ApiRequest(
        url:
            conversationId == null ? _base() : '${_base()}?conversation_id=${Uri.encodeQueryComponent(conversationId)}',
        method: 'GET',
      ),
      decode: (body) => FamilyCalendar.fromGenerated(wire.GeneratedFamilyCalendarResponse.fromJson(_json(body))),
    );

/// Today on the family calendar: what is still ahead, tasks due and overdue chores (the Android
/// Family day and Next event widgets).
Future<ApiResult<FamilyDay>> getFamilyDay() => executeApi(
      request: ApiRequest(url: '${_base()}/today', method: 'GET'),
      decode: (body) => FamilyDay.fromGenerated(wire.GeneratedFamilyDayResponse.fromJson(_json(body))),
    );

/// Put one reviewed event on the family calendar, with the user's edits (null = keep Omi's value).
Future<ApiResult<FamilyCalendarOutcome>> approveFamilyCalendarEvent(
  FamilyCalendarEvent event, {
  String? title,
  String? description,
  DateTime? start,
  int? duration,
}) =>
    executeApi(
      request: ApiRequest(
        url: '${_base()}/events',
        method: 'POST',
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({
          'conversation_id': event.conversationId,
          'event_idx': event.eventIdx,
          if (title != null) 'title': title,
          if (description != null) 'description': description,
          if (start != null) 'start': start.toUtc().toIso8601String(),
          if (duration != null) 'duration': duration,
        }),
      ),
      decode: (body) =>
          FamilyCalendarOutcome.fromGenerated(wire.GeneratedFamilyCalendarEventResponse.fromJson(_json(body))),
    );

/// Omi got it wrong or it is not wanted: remember that and stop asking. [declined] false takes a
/// decline back (Undo, or "Bring Back" on a reviewed row) so the event waits for review again.
Future<ApiResult<FamilyCalendarOutcome>> declineFamilyCalendarEvent(FamilyCalendarEvent event,
        {bool declined = true}) =>
    executeApi(
      request: ApiRequest(
        url: '${_base()}/events/decline',
        method: 'POST',
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({'conversation_id': event.conversationId, 'event_idx': event.eventIdx, 'declined': declined}),
      ),
      decode: (body) =>
          FamilyCalendarOutcome.fromGenerated(wire.GeneratedFamilyCalendarEventResponse.fromJson(_json(body))),
    );

/// Which household member Omi adds events as (null = the whole family). Answers the refreshed page.
Future<ApiResult<FamilyCalendar>> setFamilyCalendarOwner(String? ownerId) => executeApi(
      request: ApiRequest(
        url: '${_base()}/owner',
        method: 'PUT',
        headers: const {'Content-Type': 'application/json'},
        body: jsonEncode({'owner_id': ownerId}),
      ),
      decode: (body) => FamilyCalendar.fromGenerated(wire.GeneratedFamilyCalendarResponse.fromJson(_json(body))),
    );
