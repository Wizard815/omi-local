import 'dart:async';
import 'dart:convert';
import 'package:omi/backend/http/shared.dart';
import 'package:omi/env/env.dart';
import 'package:omi/services/device_calendar_service.dart';
import 'package:omi/utils/other/api_url.dart';

/// Background periodic sync of device calendar events to the Omi backend.
class DeviceCalendarSync {
  static Timer? _timer;
  static bool _running = false;

  /// Start periodic sync (every 15 minutes). Safe to call multiple times.
  static void startPeriodic() {
    if (_running) return;
    _running = true;
    _syncNow();
    _timer = Timer.periodic(const Duration(minutes: 15), (_) => _syncNow());
  }

  /// Stop periodic sync.
  static void stop() {
    _timer?.cancel();
    _timer = null;
    _running = false;
  }

  /// Force a sync now.
  static Future<Map<String, dynamic>> syncNow() async {
    return await _syncNow();
  }

  static Future<Map<String, dynamic>> _syncNow() async {
    try {
      // Load calendars from device
      final calendars = await DeviceCalendarService.getCalendars();
      final visibleCalendarIds =
          calendars.where((c) => c['visible'] == true).map<int>((c) => (c['id'] as int)).toList();

      if (visibleCalendarIds.isEmpty) {
        return {'synced': 0, 'message': 'No visible calendars'};
      }

      // Get events for next 30 days
      final now = DateTime.now();
      final startMs = now.subtract(const Duration(days: 1)).millisecondsSinceEpoch;
      final endMs = now.add(const Duration(days: 30)).millisecondsSinceEpoch;

      final events = await DeviceCalendarService.getEvents(
        startMs: startMs,
        endMs: endMs,
        calendarIds: visibleCalendarIds,
      );

      // Add calendar names to events
      final calMap = <int, String>{};
      for (final c in calendars) {
        calMap[c['id'] as int] = (c['name'] as String?) ?? (c['accountName'] as String?) ?? '';
      }

      final enrichedEvents = events.map((e) {
        final calId = e['calendarId'] as int;
        final cal = calendars.firstWhere(
          (c) => c['id'] == calId,
          orElse: () => <String, dynamic>{},
        );
        return {
          ...e,
          'calendarName': cal['name'] ?? '',
          'accountName': cal['accountName'] ?? '',
          'accountType': cal['accountType'] ?? '',
        };
      }).toList();

      // Push to backend. The base URL ends with "/"; "$base/v1/..." posted to
      // "//v1/..." (404) and the ignored response reported success anyway.
      final baseUrl = Env.apiBaseUrl;
      if (baseUrl == null || baseUrl.isEmpty) {
        return {'synced': 0, 'message': 'Sync failed: no server URL configured'};
      }
      final response = await makeApiCall(
        url: joinApiUrl(baseUrl, 'v1/device-calendar/sync'),
        method: 'POST',
        body: jsonEncode({'events': enrichedEvents, 'calendars': calendars}),
        headers: {'Content-Type': 'application/json'},
      );
      if (response == null || response.statusCode != 200) {
        return {'synced': 0, 'message': 'Sync failed: server returned ${response?.statusCode ?? 'no response'}'};
      }

      return {
        'synced': enrichedEvents.length,
        'message': 'Synced ${enrichedEvents.length} events from ${visibleCalendarIds.length} calendars'
      };
    } catch (e) {
      return {'synced': 0, 'message': 'Sync failed: $e'};
    }
  }
}
