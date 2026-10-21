import 'dart:convert';

import 'package:omi/backend/http/shared.dart';
import 'package:omi/backend/schema/gen/misc_wire.g.dart' as wire;
import 'package:omi/env/env.dart';
import 'package:omi/utils/logger.dart';

/// Tell the backend the device's IANA time zone without registering a push token.
///
/// Self-hosted builds never register FCM, which used to be the only way the
/// backend learned the zone — so recaps, day reviews and "today" ran in UTC.
Future<bool> saveTimeZoneServer(String timeZone) async {
  final response = await makeApiCall(
    url: '${Env.apiBaseUrl}v1/users/time-zone',
    headers: {'Content-Type': 'application/json'},
    method: 'PUT',
    body: jsonEncode({'time_zone': timeZone}),
  );
  return response?.statusCode == 200;
}

Future<void> saveFcmTokenServer({required String token, required String timeZone}) async {
  var response = await makeApiCall(
    url: '${Env.apiBaseUrl}v1/users/fcm-token',
    headers: {'Content-Type': 'application/json'},
    method: 'POST',
    body: jsonEncode({'fcm_token': token, 'time_zone': timeZone}),
  );

  Logger.debug('saveToken: ${response?.body}');
  if (response?.statusCode == 200) {
    final data = wire.GeneratedFcmTokenResponse.fromJson(jsonDecode(response!.body) as Map<String, dynamic>);
    Logger.debug(data.status == 'Ok' ? "Token saved successfully" : "Token save returned ${data.status}");
  } else {
    Logger.debug("Failed to save token");
  }
}
