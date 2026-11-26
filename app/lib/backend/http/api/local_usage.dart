import 'dart:convert';

import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/schema/gen/self_hosted_wire.g.dart' as wire;
import 'package:omi/backend/schema/local_usage.dart';
import 'package:omi/env/env.dart';

/// Your own usage counts, kept on this server. Read-only; [days] is clamped by the server.
Future<ApiResult<LocalUsageReport>> getLocalUsage({int days = 30, ApiSend? send}) => executeApi(
      request: ApiRequest(url: '${Env.apiBaseUrl}v1/users/me/local-usage?days=$days', method: 'GET'),
      decode: (body) => LocalUsageReport.fromGenerated(
          wire.GeneratedLocalUsageResponse.fromJson(jsonDecode(body) as Map<String, dynamic>)),
      send: send,
    );
