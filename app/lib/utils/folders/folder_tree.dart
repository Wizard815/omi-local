/// Nested folders: a folder's `parentId` makes the flat folder list a tree.
///
/// Mirrors backend/utils/folder_tree.py so the UI only offers moves the server
/// accepts. A folder whose parent no longer exists counts as top level.
library;

import 'package:omi/backend/schema/folder.dart';

/// Same limit the backend enforces.
const int maxFolderDepth = 5;

Map<String, Folder> _index(List<Folder> folders) => {for (final f in folders) f.id: f};

String? _parentOf(Folder folder, Map<String, Folder> index) {
  final parent = folder.parentId;
  return parent != null && index.containsKey(parent) ? parent : null;
}

List<Folder> rootFolders(List<Folder> folders) {
  final index = _index(folders);
  return folders.where((f) => _parentOf(f, index) == null).toList();
}

List<Folder> childFolders(List<Folder> folders, String parentId) {
  final index = _index(folders);
  return folders.where((f) => _parentOf(f, index) == parentId).toList();
}

/// Ancestors of [folderId], top level first (excluding the folder itself).
List<Folder> folderAncestors(List<Folder> folders, String folderId) {
  final index = _index(folders);
  final chain = <Folder>[];
  final seen = {folderId};
  var current = index[folderId];
  while (current != null) {
    final parent = _parentOf(current, index);
    if (parent == null || !seen.add(parent)) break;
    current = index[parent];
    chain.insert(0, current!);
  }
  return chain;
}

String? rootFolderId(List<Folder> folders, String? folderId) {
  if (folderId == null) return null;
  final ancestors = folderAncestors(folders, folderId);
  return ancestors.isEmpty ? folderId : ancestors.first.id;
}

int folderDepth(List<Folder> folders, String folderId) => folderAncestors(folders, folderId).length + 1;

Set<String> folderDescendantIds(List<Folder> folders, String folderId) {
  final found = <String>{};
  final frontier = [folderId];
  while (frontier.isNotEmpty) {
    final id = frontier.removeLast();
    for (final child in childFolders(folders, id)) {
      if (child.id != folderId && found.add(child.id)) frontier.add(child.id);
    }
  }
  return found;
}

int _subtreeHeight(List<Folder> folders, String folderId) {
  final children = childFolders(folders, folderId);
  if (children.isEmpty) return 1;
  return 1 + children.map((c) => _subtreeHeight(folders, c.id)).reduce((a, b) => a > b ? a : b);
}

/// Whether [folderId] may move under [newParentId] (null = top level), or a new
/// folder may be created there when [folderId] is null.
bool canPlaceFolder(List<Folder> folders, String? folderId, String? newParentId) {
  if (newParentId == null) return true;
  if (!folders.any((f) => f.id == newParentId)) return false;
  if (folderId != null) {
    if (newParentId == folderId || folderDescendantIds(folders, folderId).contains(newParentId)) return false;
    return folderDepth(folders, newParentId) + _subtreeHeight(folders, folderId) <= maxFolderDepth;
  }
  return folderDepth(folders, newParentId) + 1 <= maxFolderDepth;
}

/// Depth-first order (children right after their parent), for pickers and the tree view.
List<({Folder folder, int depth})> folderTreeOrder(List<Folder> folders) {
  final out = <({Folder folder, int depth})>[];
  void visit(Folder folder, int depth, Set<String> seen) {
    if (!seen.add(folder.id)) return;
    out.add((folder: folder, depth: depth));
    for (final child in childFolders(folders, folder.id)) {
      visit(child, depth + 1, seen);
    }
  }

  final seen = <String>{};
  for (final root in rootFolders(folders)) {
    visit(root, 0, seen);
  }
  return out;
}

/// "Work / Clients / Acme".
String folderPath(List<Folder> folders, String folderId) {
  final folder = folders.where((f) => f.id == folderId).firstOrNull;
  if (folder == null) return '';
  return [...folderAncestors(folders, folderId).map((f) => f.name), folder.name].join(' / ');
}
