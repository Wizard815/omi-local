// Self-hosted build: no product analytics. There is no analytics SDK, and the app never installs
// an adapter, so nothing is recorded, queued, stored or sent: [track] returns at once.
//
// The class keeps the method names upstream call sites use so they still compile; almost all of
// them do nothing. Feature flags have no remote source either: each read answers its caller's
// local default.
//
// Two things are kept:
// - the sign-in identity fence ([identityEpoch]), which owner-scoped work uses to drop results
//   that belong to a previous account. It is not analytics and never leaves the device;
// - the adapter seam ([configure], [init], [flushPending]) that upstream's protected C7 contract
//   (app/test/spine/c7_*) drives with an in-memory test adapter. Only a test installs one.
export 'registry/typed_events.dart';

import 'package:flutter/foundation.dart';
import 'package:package_info_plus/package_info_plus.dart';

import 'package:omi/backend/schema/bt_device/bt_device.dart';
import 'package:omi/backend/schema/conversation.dart';
import 'package:omi/backend/schema/memory.dart';
import 'package:omi/services/capture/empty_recording_notice.dart';
import 'package:omi/utils/analytics/analytics_adapter.dart';
import 'package:omi/utils/analytics/registry/events.g.dart';
import 'package:omi/utils/analytics/registry/typed_events.dart';
import 'package:omi/utils/build_provenance.dart';
import 'package:omi/utils/platform/platform_service.dart';

enum ProductErrorKind { flutterFramework, uncaughtDart, startup }

class AnalyticsManager {
  static final AnalyticsManager _instance = AnalyticsManager._internal();

  factory AnalyticsManager() => _instance;

  AnalyticsManager._internal();

  static int _identityEpoch = 0;
  static String? _boundIdentity;
  static bool _identityKnown = false;
  static final ValueNotifier<int> _identityChanges = ValueNotifier(0);

  /// Called with the signed-in identity after each change. `enabled` is always false: there is
  /// no analytics identity to settle.
  static void Function(String? identity, bool enabled)? identityChanged;

  /// Bumps on every sign-in change so work started for one account can drop its result.
  static int get identityEpoch => _identityEpoch;
  static ValueListenable<int> get identityChanges => _identityChanges;
  static bool get identityKnown => _identityKnown;
  static String? get currentIdentity => _boundIdentity;

  /// Product analytics are off in this build and cannot be switched on.
  static bool get trackingEnabled => false;
  static String get appBuild => _globals['app_build']?.toString() ?? 'unknown';
  static String get mobilePlatform {
    if (PlatformService.isIOS) return 'ios';
    if (PlatformService.isAndroid) return 'android';
    return 'unknown';
  }

  // The test seam. Production never calls [configure], so [_adapter] stays null.
  static AnalyticsAdapter? _adapter;
  static bool _ready = false;
  static const int _maxQueued = 200;
  static final List<(String, Map<String, Object>)> _queue = [];
  static int _dropped = 0;
  static Map<String, Object> _globals = {'app_platform': mobilePlatform};

  /// Installs an adapter. Only tests do; the app ships without one.
  static void configure(AnalyticsAdapter adapter) => _adapter = adapter;

  static Future<void> init() async {
    final adapter = _adapter;
    if (adapter == null || _ready) return;
    await adapter.init();
    try {
      adapter.registerSuperProperties(BuildProvenance.fromEnvironment().asProperties);
    } catch (_) {}
    try {
      final info = await PackageInfo.fromPlatform();
      _globals = {'app_platform': mobilePlatform, 'app_version': info.version, 'app_build': info.buildNumber};
    } catch (_) {}
    _ready = true;
  }

  /// Hands queued events to the installed adapter, if any.
  static Future<void> flushPending({bool force = false}) async {
    final adapter = _adapter;
    if (adapter == null || !_ready) return;
    while (_queue.isNotEmpty) {
      final (name, properties) = _queue.removeAt(0);
      adapter.track(eventName: name, properties: {..._globals, ...properties});
    }
  }

  @visibleForTesting
  static int get queuedEventCountForTesting => _queue.length;

  @visibleForTesting
  static int get droppedEventCountForTesting => _dropped;

  @visibleForTesting
  static void resetForTesting() {
    _identityEpoch++;
    _boundIdentity = null;
    _identityKnown = false;
    identityChanged = null;
    _adapter = null;
    _ready = false;
    _queue.clear();
    _dropped = 0;
    _globals = {'app_platform': mobilePlatform};
  }

  /// Without an adapter (always, outside tests) this does nothing at all.
  void track(String eventName, {Map<String, dynamic>? properties}) {
    if (_adapter == null) return;
    if (_queue.length >= _maxQueued) {
      _dropped++;
      return;
    }
    _queue.add((
      eventName,
      {
        for (final entry in (properties ?? const <String, dynamic>{}).entries)
          if (entry.value != null) entry.key: entry.value as Object,
      },
    ));
  }

  void bindIdentity(String? identity) {
    if (_identityKnown && _boundIdentity == identity) return;
    _identityKnown = true;
    _identityEpoch++;
    _boundIdentity = identity;
    _identityChanges.value = _identityEpoch;
    identityChanged?.call(identity, false);
  }

  /// No remote flags: a flag is on only when its caller's [fallback] says so.
  Future<bool> isFeatureEnabled(String key, {bool fallback = false}) async => fallback;
  Future<bool> isBooleanFeatureEnabled(String key) async => false;
  bool isBooleanFeatureEnabledCached(String key) => false;
  Future<bool> refreshBooleanFeatureFlag(String key) async => false;
  Future<Object?> getFeatureFlagPayload(String key) async => null;
  bool recordFeatureFlagExposure(String key, String variant) => false;
  void setUserProperty(String key, dynamic value) {}

  // The four events the protected C7 contract drives keep upstream's typed route; with no adapter
  // installed they end in [track] and are dropped.
  void onboardingCompleted() => const TypedEvents().emit(const OnboardingCompleted());
  void phoneMicRecordingStarted() => const TypedEvents().emit(const PhoneMicRecordingStarted());
  void phoneMicRecordingStopped() => const TypedEvents().emit(const PhoneMicRecordingStopped());
  void transcribeLaterToggled({required bool enabled}) =>
      const TypedEvents().emit(TranscribeLaterToggled(enabled: enabled));

  // Upstream event and person-property calls, kept as no-ops.
  void setSessionContext(String sessionId, {bool foreground = true}) {}
  void setForeground(bool foreground) {}
  void recordProductError(ProductErrorKind kind) {}
  void recordTelemetryHealth() {}
  void setUserAttribute(String key, dynamic value) {}
  void setInteractionContext({String? screenName, required String target}) {}
  void setSubscriptionTier(String tier) {}
  void identify({String? authMethod, DateTime? userCreatedAt, String userRole = 'member'}) {}
  void accountCreated({required String authProvider, String acquisitionSource = 'mobile_oauth'}) {}
  void signInFailed(String provider, {Object? error, bool cancelled = false}) {}
  void onboardingPermissionResult(
      {required OnboardingPermissionResultPermission permission,
      required bool granted,
      required bool permanentlyDenied,
      required bool servicesDisabled}) {}
  void devicePairingBlocked(
      {required DevicePairingBlockedReason reason, required int androidSdk, required int iosVersion}) {}
  void deviceScanEmpty({required int afterSeconds}) {}
  void deviceConnectFailed(DeviceConnectFailedErrorClass errorClass) {}
  void recordingProducedNoConversation(
      {required RecordingSurface source,
      required int durationSeconds,
      required bool transcriptObserved,
      required EmptyRecordingReason reason}) {}
  void onboardingStepCompleted(String step) {}
  void onboardingUserAcquisitionSource(String source) {}
  void deviceOnboardingStarted({String source = 'auto'}) {}
  void deviceOnboardingStepCompleted(String step) {}
  void deviceOnboardingCompleted() {}
  void deviceOnboardingAbandoned(int step) {}
  void deviceOnboardingDoubleTapConfigured(int action) {}
  void settingsSaved({bool hasWebhookConversationCreated = false, bool hasWebhookTranscriptReceived = false}) {}
  void pageOpened(String name) {}
  void appEnabled(String appId) {}
  void appPurchaseStarted(String appId) {}
  void appPurchaseCompleted(String appId) {}
  void privateAppSubmitted(Map<String, dynamic> properties) {}
  void publicAppSubmitted(Map<String, dynamic> properties) {}
  void appDisabled(String appId) {}
  void appRated(String appId, double rating) {}
  void recordingUploadStarted(
      {required String attemptId,
      required int fileCount,
      required int totalBytes,
      required bool claimsLiveCapture,
      String? recordingId}) {}
  void recordingUploadCompleted(
      {required String attemptId,
      required int fileCount,
      required int totalBytes,
      required bool claimsLiveCapture,
      required double durationSeconds,
      required String result,
      String? recordingId}) {}
  void recordingUploadFailed(
      {required String attemptId,
      required int fileCount,
      required int totalBytes,
      required bool claimsLiveCapture,
      required double durationSeconds,
      required String failureClass,
      String? recordingId}) {}
  void phoneCallPageOpened() {}
  void phoneCallVerificationStarted() {}
  void phoneCallVerificationCompleted() {}
  void phoneCallStarted({String? contactName}) {}
  void phoneCallConnected() {}
  void phoneCallEnded({required int durationSeconds}) {}
  void phoneCallTranscriptSession(
      {required bool wsAccepted,
      required int audioFramesSent,
      required int audioBytesSent,
      required int audioChannel1Frames,
      required int audioChannel2Frames,
      required int eventChannelErrors,
      required int eventChannelCoerced,
      required String transcriptionStatusFinal,
      required int durationSeconds,
      String? reason}) {}
  void phoneCallFailed({String? error}) {}
  void phoneCallDialpadOpened() {}
  void phoneCallDialpadDigitPressed(String digit) {}
  void languageChanged(String language) {}
  void bottomNavigationTabClicked(String tab) {}
  void deviceConnected(BtDevice device) {}
  void devicePaired(String firstPairedAt) {}
  void deviceDisconnected({String? reason, int? reasonCode, String? appState}) {}
  void diagnosticsSent({required int bundleBytes, required int disconnectCount, required int schemaVersion}) {}
  void diagnosticsSendFailed(
      {required DiagnosticsSendFailedFailureStage failureStage,
      int bundleBytes = 0,
      int disconnectCount = 0,
      int schemaVersion = 0,
      int statusCode = 0}) {}
  void deviceSessionEnded({required BtDevice device, required Duration duration, String? reason, int? hciReasonCode}) {}
  void memoriesPageDeletedMemory(Memory memory) {}
  void memoriesPageEditedMemory() {}
  void memoriesPageCreateMemoryBtn() {}
  void memoriesPageCreatedMemory(MemoryCategory category) {}
  void memorySearched(String query, int resultsCount) {}
  void memorySearchCleared(int totalFactsCount) {}
  void memoryListItemClicked(Memory memory) {}
  void memoryVisibilityChanged(Memory memory, MemoryVisibility newVisibility) {}
  void memoryReviewCardShown({required int itemCount, required String source}) {}
  void memoryReviewAction(
      {required String source, required String action, required String outcome, required String memoryCategory}) {}
  void followUpChipTapped({required String source}) {}
  void memoriesAllVisibilityChanged(MemoryVisibility newVisibility, int count) {}
  void memoriesAllDeleted(int countBeforeDeletion) {}
  void memoriesManagementSheetOpened() {}
  void conversationCreated(ServerConversation conversation, {BtDevice? recordingDevice}) {}
  void conversationDeleted(ServerConversation conversation) {}
  void chatMessageSent(
      {required String message,
      required bool includesFiles,
      required int numberOfFiles,
      required String chatTargetId,
      required bool isPersonaChat,
      required bool isVoiceInput}) {}
  void chatVoiceInputUsed({required String chatTargetId, required bool isPersonaChat}) {}
  void speechProfileCapturePageClicked() {}
  void speechProfileSkipped() {}
  void speechProfileUploadSucceeded() {}
  void speechProfileUploadFailed({String? reason, int? statusCode}) {}
  void speechProfileEmbeddingStored() {}
  void speechProfileContinued() {}
  void guidedIntroStarted(
      {required String sessionId, required String source, required String variant, required int promptCount}) {}
  void guidedIntroPromptViewed(
      {required String sessionId, required String source, required String variant, required int promptIndex}) {}
  void guidedIntroRecordingStarted(
      {required String sessionId,
      required String source,
      required String variant,
      required int promptIndex,
      required int attempt}) {}
  void guidedIntroReviewShown(
      {required String sessionId,
      required String source,
      required String variant,
      required int answerCount,
      required bool goalPresent,
      required int reviewAttempt}) {}
  void guidedIntroSaveSubmitted(
      {required String sessionId,
      required String source,
      required String variant,
      required int selectedAnswerCount,
      required int selectedMemoryCount,
      required bool goalSelected,
      required bool voiceAttempted,
      required int attempt}) {}
  void guidedIntroVoiceEnrollment(
      {required String sessionId,
      required String source,
      required String variant,
      required String result,
      required int durationMs,
      required int attempt}) {}
  void guidedIntroContentSave(
      {required String sessionId,
      required String source,
      required String variant,
      required int memoryAttempted,
      required int memorySaved,
      required int memoryFailed,
      required String goalResult,
      required int attempt}) {}
  void guidedIntroCompleted(
      {required String sessionId,
      required String source,
      required String variant,
      required String completionMode,
      required String voiceResult,
      required int memorySaved,
      required String goalResult,
      required int elapsedMs}) {}
  void showDiscardedMemoriesToggled(bool showDiscarded) {}
  void conversationDisplaySettingsOpened() {}
  void showShortConversationsToggled(bool showShort) {}
  void showDiscardedConversationsToggled(bool showDiscarded) {}
  void shortConversationThresholdChanged(int thresholdSeconds) {}
  void voiceResponseModeChanged(int mode) {}
  void voiceReplyPlayback(
      {required VoiceReplyPlaybackOutcome outcome,
      required VoiceReplyPlaybackSkipReason skipReason,
      required VoiceReplyPlaybackMode mode,
      required VoiceReplyPlaybackOutputRoute outputRoute,
      required int chunksRequested,
      required int chunksPlayed,
      required int chunksDropped,
      required VoiceReplyPlaybackFallbackReason fallbackReason,
      required int firstAudioLatencyMs,
      required VoiceReplyPlaybackInterruptSource interruptSource}) {}
  void conversationMergeSelectionModeEntered() {}
  void conversationMergeSelectionModeExited() {}
  void conversationSelectedForMerge(String conversationId, int totalSelected) {}
  void conversationMergeInitiated(List<String> conversationIds) {}
  void conversationMergeCompleted(String mergedConversationId, List<String> removedConversationIds) {}
  void conversationMergeFailed(List<String> conversationIds) {}
  void importantConversationNotificationReceived(String conversationId) {}
  void shareToContactsSheetOpened(String conversationId) {}
  void shareToContactsSelected(String conversationId, int contactCount) {}
  void chatMessageConversationClicked(ServerConversation conversation) {}
  void reProcessConversation(ServerConversation conversation) {}
  void exportMemories() {}
  void paywallOpened(String source, {String currentPlan = 'unknown'}) {}
  void upgradePlanSelected({required String plan, required String billingInterval, required String source}) {}
  void upgradeSucceeded({required String previousPlan, required String newPlan, required String billingInterval}) {}
  void upgradeCancelled() {}
  void upgradeModalDismissed() {}
  void subscriptionCancelFlowStarted() {}
  void subscriptionCancelReasonSelected({required String reason}) {}
  void subscriptionCancelConfirmed({required String reason, String? details}) {}
  void subscriptionCancelKeptPlan({required int step, String? reason}) {}
  void subscriptionCancelAbandoned({required int step, String? reason}) {}
  void connectFriendClicked() {}
  void disconnectFriendClicked() {}
  void batteryIndicatorClicked() {}
  void useWithoutDeviceOnboardingFindDevices() {}
  void tagSheetOpened() {}
  void taggedSegment(String assignType) {}
  void editSegmentTextStarted() {}
  void editSegmentTextSaved() {}
  void editSegmentTextCancelled() {}
  void editSummaryStarted() {}
  void editSummarySaved() {}
  void editSummaryCancelled() {}
  void deleteAccountConfirmed() {}
  void deleteAccountFlowStarted() {}
  void deleteAccountReasonSelected({required String reason}) {}
  void deleteAccountFeedbackSubmitted({required String reason, String? details}) {}
  void deleteAccountAbandoned({required int step, String? reason}) {}
  void deleteAccountKeptAccount({required int step, String? reason}) {}
  void deleteUser() {}
  void appsCategoryFilter(String category, bool isSelected) {}
  void appsTypeFilter(String type, bool isSelected) {}
  void appsSortFilter(String sortBy, bool isSelected) {}
  void appsRatingFilter(String rating, bool isSelected) {}
  void appsCapabilityFilter(String capability, bool isSelected) {}
  void appsClearFilters() {}
  void brainMapOpened() {}
  void brainMapNodeClicked(String nodeId, String label, String type) {}
  void brainMapShareClicked() {}
  void summarizedAppSheetViewed({required String conversationId, String? currentSummarizedAppId}) {}
  void summarizedAppSelected({required String conversationId, required String selectedAppId, String? previousAppId}) {}
  void summarizedAppEnableAppsClicked({required String conversationId}) {}
  void summarizedAppCreateTemplateClicked({required String conversationId}) {}
  void quickTemplateCreated({required String conversationId, required String appName, required bool isPublic}) {}
  void actionItemsPageOpened() {}
  void actionItemCompleted({required String fromTab}) {}
  void trainingDataOptInSubmitted() {}
  void recordingMuteToggled({required bool isMuted, required String recordingType}) {}
  void calendarFilterApplied(DateTime startDate, DateTime endDate) {}
  void calendarFilterCleared() {}
  void conversationOpenedFromSearch(
      {required ServerConversation conversation,
      required String searchQuery,
      required int conversationIndexInResults}) {}
  void liveTranscriptCardClicked(
      {required bool hasSegments, required bool hasPhotos, required int segmentCount, required int photoCount}) {}
  void conversationListItemClickedWithTimeDifference(
      {required ServerConversation conversation,
      required int conversationIndex,
      required int hoursSinceConversation}) {}
  void conversationSwipedToDelete(ServerConversation conversation) {}
  void conversationDetailTabChanged(String tabName) {}
  void conversationDetailSearchClicked({required String conversationId}) {}
  void conversationDetailSearchQueryEntered(
      {required String conversationId, required String query, required int resultsCount, required String activeTab}) {}
  void conversationThreeDotsMenuOpened({required String conversationId}) {}
  void conversationThreeDotsMenuActionSelected({required String conversationId, required String action}) {}
  void exportTasksBannerClicked() {}
  void taskIntegrationEnabled({required String appName, required bool success}) {}
  void taskIntegrationAuthFailed({required String appName}) {}
  void taskIntegrationSettingsOpened({required String appName}) {}
  void transcriptionSourceSelected({required String source}) {}
  void transcriptionProviderSelected({required String provider}) {}
  void audioPlaybackFailed({required String conversationId, required String reason}) {}
  void audioPlaybackStarted({required String conversationId, int? durationSeconds}) {}
  void audioPlaybackPaused({required String conversationId, required int positionSeconds, int? durationSeconds}) {}
  void audioPlaybackSeeked({required String conversationId, required int toPositionSeconds}) {}
  void transcriptSegmentTapped(
      {required String conversationId, required double segmentStartSeconds, required double seekPositionSeconds}) {}
  void audioShareStarted({required String conversationId, required int audioFileCount}) {}
  void audioShareCompleted(
      {required String conversationId,
      required int audioFileCount,
      required bool wasCombined,
      required int durationSeconds}) {}
  void audioShareFailed({required String conversationId, String? errorMessage}) {}
  void actionItemExported({required String actionItemId, required String appName, required DateTime timestamp}) {}
  void actionItemManuallyAdded({required String actionItemId, required DateTime timestamp}) {}
  void actionItemEdited({required String actionItemId, required bool titleChanged, required bool dateChanged}) {}
  void appleRemindersSyncCompleted(
      {required int pendingExported,
      required int syncedChecked,
      required int completionsPulled,
      required int completionsPushed,
      required int titleDuePulled,
      required int titleDuePushed,
      required int remindersUnlinked}) {}
  void appleReminderDirectSync({required String actionItemId}) {}
  void appleReminderDeleted({required String actionItemId}) {}
  void settingsPageOpened({required String pageName}) {}
  void appsSearched({required String searchTerm, required int resultCount}) {}
  void appsFilterMyApps({required bool enabled}) {}
  void appsFilterInstalled({required bool enabled}) {}
  void appsFilterRating({required int rating}) {}
  void appsFilterCategory({required String category}) {}
  void appsSortChanged({required String sortOption}) {}
  void appsFilterCapability({required String capability}) {}
  void appsCategoryPageOpened({required String category, required int appCount}) {}
  void appDetailViewed(
      {required String appId,
      required String appName,
      String? category,
      double? rating,
      int? installs,
      bool? isInstalled}) {}
  void appDetailReviewsOpened({required String appId, required int reviewCount}) {}
  void appDetailSubscribeClicked({required String appId, required String appName, double? price}) {}
  void appDetailSubscriptionCancelled({required String appId, required String appName}) {}
  void appDetailPreviewImageViewed({required String appId, required int imageIndex}) {}
  void appDetailChatClicked({required String appId, required String appName}) {}
  void folderCreated(
      {required String folderId, required String folderName, required String icon, required String color}) {}
  void folderUpdated({required String folderId, required String folderName}) {}
  void folderDeleted(
      {required String folderId, required String folderName, required int conversationCount, String? moveToFolderId}) {}
  void folderContextMenuOpened({required String folderId, required String folderName}) {}
  void conversationDetailFolderChipClicked({required String conversationId, String? currentFolderId}) {}
  void conversationMovedToFolder(
      {required String conversationId, String? fromFolderId, String? toFolderId, required String source}) {}
  void conversationVisibilityChanged(
      {required String conversationId, required String fromVisibility, required String toVisibility}) {}
  void conversationStarToggled(
      {required ServerConversation conversation, required bool starred, required String source}) {}
  void omiDoubleTap({required String feature}) {}
  void wrappedPageOpened() {}
  void wrappedGenerationStarted() {}
  void wrappedGenerationCompleted(
      {required int totalConversations, required int totalMinutes, required int daysActive}) {}
  void wrappedGenerationFailed({String? error}) {}
  void wrappedCardViewed({required String cardName, required int cardIndex}) {}
  void wrappedShareButtonClicked({required String cardName, required int cardIndex}) {}
  void wrappedSharedSuccessfully({required String cardName, required int cardIndex, int? fileSizeBytes}) {}
  void wrappedShareFailed({required String cardName, required int cardIndex, String? error}) {}
  void dailySummarySettingsOpened() {}
  void permissionsSettingsOpened() {}
  void permissionChanged({required String permission, required bool granted}) {}
  void permissionsInterstitialShown() {}
  void permissionsInterstitialCompleted() {}
  void dailySummaryToggled({required bool enabled}) {}
  void dailySummaryTimeChanged({required int hour}) {}
  void dailySummaryDetailViewed({required String summaryId, required String date, String? source}) {}
  void recapSummaryCardClicked({required String summaryId, required String date, required int cardIndex}) {}
  void dailySummaryNotificationOpened({required String summaryId, required String date}) {}
  void dailySummaryConversationClicked(
      {required String summaryId, required String conversationId, required String source}) {}
  void dailySummaryDeleted({required String summaryId, required String date, required String source}) {}
  void dailySummaryDeleteFailed({required String summaryId, required String date, required String source}) {}
  void announcementShown({required String announcementId, required String type, String? trigger, int? priority}) {}
  void announcementDismissed({required String announcementId, required String type, required bool ctaClicked}) {}
  void changelogShown({required int changelogCount, required String fromVersion, required String toVersion}) {}
  void changelogDismissed({required int changelogCount}) {}
  void whatsNewOpened() {}
  void goalAddButtonTapped({required String source}) {}
  void goalCreated(
      {required String goalId, required int titleLength, required double targetValue, required String source}) {}
  void goalUpdated({required String goalId, required String source}) {}
  void goalDeleted({required String goalId, required String source, required String method}) {}
  void goalItemTappedForEdit({required String goalId, required String source}) {}
  void goalEmojiSelected({required String emoji}) {}
  void goalProgressChanged(
      {required String goalId, required double oldValue, required double newValue, required double targetValue}) {}
  void integrationsPageOpened() {}
  void integrationConnectAttempted({required String integrationName}) {}
  void integrationConnectSucceeded({required String integrationName}) {}
  void integrationConnectFailed({required String integrationName}) {}
  void integrationDisconnected({required String integrationName}) {}
  void paymentsPageOpened() {}
  void paymentMethodSelected({required String methodName}) {}
  void connectDevicePageOpened() {}
  void getOmiDeviceClicked() {}
  void connectionGuideOpened() {}
  void connectionGuideDeviceTapped(String deviceId) {}
  void connectionGuideDismissed(String deviceId) {}
  void dataPrivacyPageOpened() {}
  void aiAppGeneratorPageOpened() {}
  void aiAppGeneratorPromptSubmitted({required int promptLength}) {}
  void aiAppGeneratorAppGenerated({required bool success}) {}
  void importHistoryPageOpened() {}
  void importStarted({required String source}) {}
  void notificationFrequencyChanged({required int oldFrequency, required int newFrequency}) {}
}
