import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

class AppReviewService {
  static final AppReviewService _instance = AppReviewService._internal();
  factory AppReviewService() => _instance;
  AppReviewService._internal();

  static const String _hasCompletedFirstActionItemKey = 'has_completed_first_action_item';
  static const String _hasShownReviewPromptKey = 'has_shown_review_prompt';
  static const String _hasFirstConversationKey = 'has_first_conversation';
  static const String _hasShownReviewForConversationKey = 'has_shown_review_for_conversation';
  static const String _hasShownReviewForActionItemKey = 'has_shown_review_for_action_item';

  // Checks if the user has completed their first action item
  Future<bool> hasCompletedFirstActionItem() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_hasCompletedFirstActionItemKey) ?? false;
  }

  // Marks that the user has completed their first action item
  Future<void> markFirstActionItemCompleted() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_hasCompletedFirstActionItemKey, true);
  }

  // Checks if the review prompt has already been shown
  Future<bool> hasShownReviewPrompt() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_hasShownReviewPromptKey) ?? false;
  }

  // Marks that the review prompt has been shown
  Future<void> markReviewPromptShown() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_hasShownReviewPromptKey, true);
  }

  // Checks if this is the user's first conversation
  Future<bool> isFirstConversation() async {
    final prefs = await SharedPreferences.getInstance();
    return !(prefs.getBool(_hasFirstConversationKey) ?? false);
  }

  // Marks that the user has had their first conversation
  Future<void> markFirstConversation() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_hasFirstConversationKey, true);
  }

  // Checks if review prompt has been shown for conversation
  Future<bool> hasShownReviewForConversation() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_hasShownReviewForConversationKey) ?? false;
  }

  // Marks that review prompt has been shown for conversation
  Future<void> markReviewShownForConversation() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_hasShownReviewForConversationKey, true);
  }

  // Checks if review prompt has been shown for action item
  Future<bool> hasShownReviewForActionItem() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_hasShownReviewForActionItemKey) ?? false;
  }

  // Marks that review prompt has been shown for action item
  Future<void> markReviewShownForActionItem() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_hasShownReviewForActionItemKey, true);
  }

  // Shows the review prompt if conditions are met. Neutered for this self-hosted
  // deployment — an App Store/Play Store rating nag makes no sense for a
  // single self-hosted user (same shape as the Crashlytics/FCM neutering).
  // Call sites still gate other first-conversation/first-action-item bookkeeping
  // on this method's siblings above, so those stay intact.
  Future<bool> showReviewPromptIfNeeded(BuildContext context, {bool isProcessingFirstConversation = false}) async {
    return false;
  }
}
