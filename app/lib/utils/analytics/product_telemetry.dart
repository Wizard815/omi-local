import 'package:omi/utils/analytics/registry/event_context.dart';

export 'package:omi/utils/analytics/registry/event_context.dart';

enum ProductJourney {
  search,
  chatText,
  chatVoice,
  subscriptionCancel,
  summaryFeedback,
  recordingFeedback,
  notificationOpen,
  integrationConnect,
  integrationSync,
  onboarding,
  permission,
  taskMutation,
  conversationLoad,
  capture,
}

enum ProductSurface {
  home,
  conversations,
  conversationDetail,
  chat,
  tasks,
  onboarding,
  settings,
  notification,
  integration,
  background,
  unknown
}

enum ProductOutcome { success, empty, failure, cancelled, superseded, unobserved }

enum ProductFailure {
  none,
  network,
  server,
  unauthorized,
  permissionDenied,
  quota,
  timeout,
  invalidResponse,
  incomplete,
  unknown
}

enum ProductValue {
  resultViewed,
  copied,
  shared,
  taskCompleted,
  memoryKept,
  feedbackHelpful,
  searchResultOpened,
  recordingRecovered
}

/// Upstream's product-journey boundary. The self-hosted build has no analytics: an attempt keeps
/// only the state its caller reads (done or not, and its correlation id for the caller's own
/// request) and nothing is recorded or sent.
class ProductTelemetry {
  static ProductTelemetry instance = ProductTelemetry();

  ProductAttempt start(ProductJourney journey,
          {ProductSurface surface = ProductSurface.unknown, RecordReference? objectId}) =>
      ProductAttempt._(journey, surface);

  void value(ProductValue kind, {ProductSurface surface = ProductSurface.unknown, RecordReference? objectId}) {}
}

class ProductAttempt {
  ProductAttempt._(this.journey, this.surface);
  final ProductJourney journey;
  final ProductSurface surface;
  final EventCorrelation _correlation = EventCorrelation.mint();
  bool _terminal = false;

  String get correlationId => _correlation.value;
  bool get isComplete => _terminal;

  void bindObject(RecordReference? objectId) {}

  void firstResult() {}

  void complete(ProductOutcome outcome, {ProductFailure failure = ProductFailure.none, int? resultCount}) {
    _terminal = true;
  }
}
