import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/backend/http/api/conversations.dart';
import 'package:omi/backend/http/api/search.dart';
import 'package:omi/backend/http/api/users.dart';
import 'package:omi/backend/http/api_result.dart';
import 'package:omi/backend/preferences.dart';
import 'package:omi/backend/schema/action_item.dart';
import 'package:omi/backend/schema/conversation.dart';
import 'package:omi/backend/schema/daily_summary.dart';
import 'package:omi/backend/schema/folder.dart';
import 'package:omi/l10n/app_localizations.dart';
import 'package:omi/pages/search/global_search.dart';
import 'package:omi/providers/folder_provider.dart';
import 'package:omi/providers/people_provider.dart';

/// omi-local: Search → Tags lists the user's tag catalog; a tag opens the conversations carrying it.
class _Source extends GlobalSearchSource {
  _Source();

  final List<String> requestedTags = [];

  @override
  Future<ApiResult<SearchOverview>> overview() async =>
      const ApiFailure(ApiProblem(ApiProblemKind.notFound, statusCode: 404));

  @override
  Future<ConversationSearchResult> conversations(String query,
          {String? speakerId, DateTime? startDate, DateTime? endDate}) async =>
      const ConversationSearchResult(
          items: [], currentPage: 1, totalPages: 1, outcome: ConversationSearchResultOutcome.success);

  @override
  Future<List<ServerConversation>> conversationsIn({String? folderId, bool starred = false}) async => const [];

  @override
  Future<List<({String tag, int count})>> tagCatalog() async => [(tag: 'work', count: 12), (tag: 'family', count: 3)];

  @override
  Future<List<ServerConversation>> conversationsByTag(String tag) async {
    requestedTags.add(tag);
    return [_conversation('c1', 'Planning with Dana', DateTime(2026, 10, 27, 9))];
  }

  @override
  Future<ApiResult<List<DailySummary>>> recaps(String query) async => const ApiSuccess([]);

  @override
  Future<ApiResult<List<ActionItemWithMetadata>>> tasks(String query) async => const ApiSuccess([]);

  @override
  Future<ApiResult<List<MemorySearchHit>>> memories(String query) async => const ApiSuccess([]);
}

ServerConversation _conversation(String id, String title, DateTime started) => ServerConversation.fromJson({
      'id': id,
      'created_at': started.toUtc().toIso8601String(),
      'started_at': started.toUtc().toIso8601String(),
      'finished_at': started.add(const Duration(minutes: 10)).toUtc().toIso8601String(),
      'structured': {'title': title, 'overview': '', 'emoji': '', 'category': 'work'},
      'status': 'completed',
      'transcript_segments': [],
      'tags': ['work'],
    });

void main() {
  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await SharedPreferencesUtil.init();
  });

  testWidgets('the Tags tile lists the catalog with counts and a tag opens its conversations', (tester) async {
    final source = _Source();
    await tester.pumpWidget(MultiProvider(
      providers: [
        ChangeNotifierProvider(
            create: (_) => PeopleProvider(loadPeople: () async => const PeopleListResponse(people: []))),
        ChangeNotifierProvider(create: (_) => FolderProvider(foldersFetcher: () async => <Folder>[])),
      ],
      child: MaterialApp(
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: Scaffold(body: GlobalSearchPage(source: source)),
      ),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('search_tile_tags')));
    await tester.pumpAndSettle();
    expect(find.text('#work'), findsOneWidget);
    expect(find.text('12'), findsOneWidget);
    expect(find.text('#family'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('search_tag_work')));
    await tester.pumpAndSettle();
    expect(source.requestedTags, ['work']);
    expect(find.text('Planning with Dana'), findsOneWidget);
    expect(find.text('#work'), findsOneWidget); // the scope title
  });

  testWidgets('a conversation row parses its tags', (tester) async {
    expect(_conversation('c', 'T', DateTime(2026)).tags, ['work']);
    expect(_conversation('c', 'T', DateTime(2026)).toJson()['tags'], ['work']);
  });
}
