// omi-local: a task Omi extracted that waits for the user's OK (or was declined) is not a task yet:
// it stays out of the Tasks list (and so out of Home and reminders) until it is approved.
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:omi/backend/schema/schema.dart';
import 'package:omi/providers/action_items_provider.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  test('waiting and declined tasks are left out of the list; approved and older ones stay', () async {
    final provider = ActionItemsProvider(
      getActionItems: ({
        int limit = 50,
        int offset = 0,
        bool? completed,
        String? conversationId,
        DateTime? startDate,
        DateTime? endDate,
        DateTime? dueStartDate,
        DateTime? dueEndDate,
      }) async =>
          ActionItemsResponse(actionItems: [
        ActionItemWithMetadata(id: 'old', description: 'From before approvals', completed: false),
        ActionItemWithMetadata(id: 'ok', description: 'Approved', completed: false, approval: 'approved'),
        ActionItemWithMetadata(id: 'wait', description: 'Heard, waiting', completed: false, approval: 'pending'),
        ActionItemWithMetadata(id: 'no', description: 'Declined', completed: false, approval: 'declined'),
      ], hasMore: false),
    );
    addTearDown(provider.dispose);
    await provider.fetchActionItems();
    expect(provider.actionItems.map((t) => t.id).toList()..sort(), ['ok', 'old']);
  });
}
