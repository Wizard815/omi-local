import 'package:flutter_test/flutter_test.dart';

import 'package:omi/backend/schema/folder.dart';
import 'package:omi/utils/folders/folder_tree.dart';

Folder _f(String id, {String? parent, int order = 0}) => Folder(
      id: id,
      name: id[0].toUpperCase() + id.substring(1),
      color: '#6B7280',
      icon: 'folder',
      createdAt: DateTime(2026),
      updatedAt: DateTime(2026),
      order: order,
      isDefault: false,
      isSystem: false,
      conversationCount: 0,
      parentId: parent,
    );

void main() {
  final folders = [
    _f('work'),
    _f('home'),
    _f('clients', parent: 'work'),
    _f('acme', parent: 'clients'),
    _f('stray', parent: 'deleted-long-ago'),
  ];

  test('roots, children, ancestors and paths', () {
    expect(rootFolders(folders).map((f) => f.id), ['work', 'home', 'stray']);
    expect(childFolders(folders, 'work').map((f) => f.id), ['clients']);
    expect(folderAncestors(folders, 'acme').map((f) => f.id), ['work', 'clients']);
    expect(rootFolderId(folders, 'acme'), 'work');
    expect(rootFolderId(folders, null), isNull);
    expect(folderPath(folders, 'acme'), 'Work / Clients / Acme');
  });

  test('tree order puts children right after their parent', () {
    expect(folderTreeOrder(folders).map((e) => '${e.folder.id}:${e.depth}'),
        ['work:0', 'clients:1', 'acme:2', 'home:0', 'stray:0']);
  });

  test('moves the backend would reject are not offered', () {
    expect(canPlaceFolder(folders, 'work', 'acme'), isFalse); // inside its own descendant
    expect(canPlaceFolder(folders, 'clients', 'clients'), isFalse);
    expect(canPlaceFolder(folders, 'clients', 'home'), isTrue);
    expect(canPlaceFolder(folders, 'acme', null), isTrue);
    final chain = [for (var i = 0; i < 5; i++) _f('f$i', parent: i == 0 ? null : 'f${i - 1}')];
    expect(canPlaceFolder(chain, null, 'f3'), isTrue); // new folder at depth 5
    expect(canPlaceFolder(chain, null, 'f4'), isFalse); // would be depth 6
  });
}
