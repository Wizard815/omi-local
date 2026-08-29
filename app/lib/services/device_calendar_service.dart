import 'package:flutter/services.dart';

/// Talks to DeviceCalendarPlugin.kt on Android.
/// Falls back gracefully on non-Android platforms.
class DeviceCalendarService {
  static const _channel = MethodChannel('com.friend.ios/device_calendar');

  /// List all calendars on the device.
  /// Each entry: {id, name, accountName, accountType, color, visible, ownerAccount}
  static Future<List<Map<String, dynamic>>> getCalendars() async {
    try {
      final result = await _channel.invokeMethod('getCalendars');
      return List<Map<String, dynamic>>.from(result as List);
    } on MissingPluginException {
      return [];
    } catch (e) {
      return [];
    }
  }

  /// Get events in a time range. calendarIds=null means all calendars.
  static Future<List<Map<String, dynamic>>> getEvents({
    required int startMs,
    required int endMs,
    List<int>? calendarIds,
  }) async {
    try {
      final result = await _channel.invokeMethod('getEvents', {
        'startMs': startMs,
        'endMs': endMs,
        'calendarIds': calendarIds,
      });
      return List<Map<String, dynamic>>.from(result as List);
    } on MissingPluginException {
      return [];
    } catch (e) {
      return [];
    }
  }

  /// Create a calendar event. calendarId=0 uses primary/visible default.
  static Future<Map<String, dynamic>> createEvent({
    int calendarId = 0,
    required String title,
    required int startMs,
    required int endMs,
    String description = '',
    String location = '',
    bool allDay = false,
  }) async {
    try {
      final result = await _channel.invokeMethod('createEvent', {
        'calendarId': calendarId,
        'title': title,
        'startMs': startMs,
        'endMs': endMs,
        'description': description,
        'location': location,
        'allDay': allDay,
      });
      return Map<String, dynamic>.from(result as Map);
    } on MissingPluginException {
      return {'eventId': -1, 'error': 'plugin not available'};
    } catch (e) {
      return {'eventId': -1, 'error': e.toString()};
    }
  }

  /// Update an event. Only non-null fields are changed.
  static Future<bool> updateEvent({
    required int eventId,
    String? title,
    int? startMs,
    int? endMs,
    String? description,
  }) async {
    try {
      final result = await _channel.invokeMethod('updateEvent', {
        'eventId': eventId,
        'title': title,
        'startMs': startMs,
        'endMs': endMs,
        'description': description,
      });
      return (result as Map)['updated'] == true;
    } on MissingPluginException {
      return false;
    } catch (e) {
      return false;
    }
  }

  /// Delete an event.
  static Future<bool> deleteEvent({required int eventId}) async {
    try {
      final result = await _channel.invokeMethod('deleteEvent', {'eventId': eventId});
      return (result as Map)['deleted'] == true;
    } on MissingPluginException {
      return false;
    } catch (e) {
      return false;
    }
  }
}