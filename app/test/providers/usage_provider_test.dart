import 'package:flutter_test/flutter_test.dart';

import 'package:omi/models/subscription.dart';
import 'package:omi/models/transcription_allowance.dart';
import 'package:omi/providers/usage_provider.dart';

UserSubscriptionResponse _proSubscription() {
  return UserSubscriptionResponse(
    subscription: Subscription(plan: PlanType.unlimited, status: SubscriptionStatus.active),
    transcriptionSecondsUsed: 0,
    transcriptionSecondsLimit: 0,
    wordsTranscribedUsed: 0,
    wordsTranscribedLimit: 0,
    insightsGainedUsed: 0,
    insightsGainedLimit: 0,
  );
}

UserSubscriptionResponse _subscriptionOn(PlanType plan, {required int used, required int limit}) {
  return UserSubscriptionResponse(
    subscription: Subscription(plan: plan, status: SubscriptionStatus.active),
    transcriptionSecondsUsed: used,
    transcriptionSecondsLimit: limit,
    wordsTranscribedUsed: 0,
    wordsTranscribedLimit: 0,
    insightsGainedUsed: 0,
    insightsGainedLimit: 0,
  );
}

void main() {
  // Self-hosted builds have no billing plane (usage_provider.dart): whatever plan
  // or allowance the server reports, capture is never metered and no feature is
  // paywalled. Upstream's tier-gating tests do not apply to this fork.
  group('UsageProvider self-hosted gating', () {
    test('an exhausted ladder budget does not block capture', () {
      final provider = UsageProvider();
      provider.debugSetSubscription(UserSubscriptionResponse(
        subscription: Subscription(plan: PlanType.operator, status: SubscriptionStatus.active),
        transcriptionSecondsUsed: 0,
        transcriptionSecondsLimit: 0,
        wordsTranscribedUsed: 0,
        wordsTranscribedLimit: 0,
        insightsGainedUsed: 0,
        insightsGainedLimit: 0,
        transcriptionAllowance: const TranscriptionAllowanceSnapshot(
            mode: 'on_device', reason: 'usage_budget_exhausted', remainingSeconds: 0),
      ));
      expect(provider.isOutOfCredits, isFalse);
    });

    test('a used-up metered plan does not block capture', () {
      final provider = UsageProvider();
      provider.debugSetSubscription(_subscriptionOn(PlanType.plus, used: 90000, limit: 90000));
      expect(provider.isOutOfCredits, isFalse);
    });

    test('every plan, including an unknown one, unlocks phone calls', () {
      final provider = UsageProvider();
      for (final plan in [PlanType.plus, PlanType.unlimitedV2, PlanType.unknown('future_plan_123')]) {
        provider.debugSetSubscription(_subscriptionOn(plan, used: 0, limit: 0));
        expect(provider.canAccessPhoneCalls, isTrue, reason: plan.name);
      }
      provider.debugSetSubscription(null);
      expect(provider.canAccessPhoneCalls, isTrue);
    });
  });

  group('UsageProvider.clearUserData', () {
    test('resets subscription state so the next login cannot inherit the previous account plan', () {
      final provider = UsageProvider();
      provider.debugSetSubscription(_proSubscription());
      expect(provider.subscription, isNotNull);

      var notified = false;
      provider.addListener(() => notified = true);
      provider.clearUserData();

      expect(provider.subscription, isNull);
      expect(provider.isOutOfCredits, isFalse);
      expect(provider.error, isNull);
      expect(notified, isTrue);
    });
  });
}
