import '../analytics_manager.dart';
import 'events.g.dart';
export 'event_context.dart';

/// Upstream's single seam for registered events. It forwards to [AnalyticsManager.track], which
/// drops every event in the self-hosted app (no adapter is ever installed).
class TypedEvents {
  const TypedEvents();

  void emit(RegisteredEvent event) =>
      AnalyticsManager().track(event.wireName, properties: Map<String, dynamic>.from(event.properties));
}

enum EventPhase { point, attempt, progress, outcome }
