/// Join the API base URL and a path with exactly one "/" between them.
///
/// The configured base URL ends with "/" (`'${Env.apiBaseUrl}v1/...'` is the
/// house style), so `'$base/v1/...'` produced "//v1/..." — which the backend
/// answers with 404 (device-calendar sync, dashboard links).
String joinApiUrl(String base, String path) {
  final trimmedBase = base.replaceFirst(RegExp(r'/+$'), '');
  final trimmedPath = path.replaceFirst(RegExp(r'^/+'), '');
  return trimmedPath.isEmpty ? '$trimmedBase/' : '$trimmedBase/$trimmedPath';
}
