import 'package:envied/envied.dart';

import 'env.dart';

part 'prod_env.g.dart';

@Envied(allowOptionalFields: true, path: '.env')
final class ProdEnv implements EnvFields {
  ProdEnv();

  @override
  @EnviedField(varName: 'API_BASE_URL', obfuscate: true)
  final String? apiBaseUrl = _ProdEnv.apiBaseUrl;

  @override
  @EnviedField(varName: 'GOOGLE_CLIENT_ID', obfuscate: true)
  final String? googleClientId = _ProdEnv.googleClientId;

  @override
  @EnviedField(varName: 'GOOGLE_CLIENT_SECRET', obfuscate: true)
  final String? googleClientSecret = _ProdEnv.googleClientSecret;

  @override
  @EnviedField(varName: 'USE_WEB_AUTH', obfuscate: false, defaultValue: false)
  final bool? useWebAuth = _ProdEnv.useWebAuth;

  @override
  @EnviedField(varName: 'USE_AUTH_CUSTOM_TOKEN', obfuscate: false, defaultValue: false)
  final bool? useAuthCustomToken = _ProdEnv.useAuthCustomToken;
}
