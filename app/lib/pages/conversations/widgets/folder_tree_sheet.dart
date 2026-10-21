import 'package:flutter/material.dart';

import 'package:font_awesome_flutter/font_awesome_flutter.dart';
import 'package:provider/provider.dart';

import 'package:omi/backend/schema/folder.dart';
import 'package:omi/pages/conversations/widgets/create_folder_sheet.dart';
import 'package:omi/providers/folder_provider.dart';
import 'package:omi/utils/alerts/app_snackbar.dart';
import 'package:omi/utils/folders/folder_icon_mapper.dart';
import 'package:omi/utils/folders/folder_tree.dart';
import 'package:omi/utils/l10n_extensions.dart';

/// All folders as a collapsible tree: open one, add subfolders, move or edit.
class FolderTreeSheet extends StatefulWidget {
  final String? selectedFolderId;
  final Function(String?) onFolderSelected;

  const FolderTreeSheet({super.key, required this.selectedFolderId, required this.onFolderSelected});

  static Future<void> show(
    BuildContext context, {
    required String? selectedFolderId,
    required Function(String?) onFolderSelected,
  }) {
    return showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: const Color(0xFF1F1F25),
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => FolderTreeSheet(selectedFolderId: selectedFolderId, onFolderSelected: onFolderSelected),
    );
  }

  @override
  State<FolderTreeSheet> createState() => _FolderTreeSheetState();
}

class _FolderTreeSheetState extends State<FolderTreeSheet> {
  final Set<String> _collapsed = {};

  @override
  void initState() {
    super.initState();
    // Start with everything collapsed except the path to the open folder.
    final folders = context.read<FolderProvider>().folders;
    final open = widget.selectedFolderId == null
        ? <String>{}
        : folderAncestors(folders, widget.selectedFolderId!).map((f) => f.id).toSet();
    _collapsed.addAll(folders.where((f) => !open.contains(f.id)).map((f) => f.id));
  }

  bool _visible(List<Folder> folders, Folder folder) =>
      folderAncestors(folders, folder.id).every((a) => !_collapsed.contains(a.id));

  Future<void> _move(Folder folder) async {
    final provider = context.read<FolderProvider>();
    final folders = provider.folders;
    final targets = folderTreeOrder(folders).where((e) => canPlaceFolder(folders, folder.id, e.folder.id)).toList();
    final choice = await showModalBottomSheet<({String? id})>(
      context: context,
      backgroundColor: const Color(0xFF1F1F25),
      builder: (ctx) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            ListTile(
              title: Text(context.l10n.moveFolderTo(folder.name),
                  style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
            ),
            ListTile(
              leading: const Icon(Icons.vertical_align_top, color: Colors.white70),
              title: Text(context.l10n.folderTopLevel, style: const TextStyle(color: Colors.white)),
              onTap: () => Navigator.pop(ctx, (id: null)),
            ),
            for (final entry in targets)
              ListTile(
                contentPadding: EdgeInsets.only(left: 16.0 + 18.0 * entry.depth, right: 16),
                leading: FaIcon(folderIconToFa(entry.folder.icon), size: 14, color: entry.folder.colorValue),
                title: Text(entry.folder.name, style: const TextStyle(color: Colors.white)),
                onTap: () => Navigator.pop(ctx, (id: entry.folder.id)),
              ),
          ],
        ),
      ),
    );
    if (choice == null || choice.id == folder.parentId) return;
    final ok = await provider.moveFolder(folder.id, choice.id);
    if (!ok && mounted) AppSnackbar.showSnackbarError(context.l10n.failedToUpdateFolder);
  }

  @override
  Widget build(BuildContext context) {
    final folders = context.watch<FolderProvider>().folders;
    final rows = folderTreeOrder(folders).where((e) => _visible(folders, e.folder)).toList();
    return SafeArea(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.8),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 16, 8, 8),
              child: Row(
                children: [
                  Expanded(
                    child: Text(context.l10n.folderTreeTitle,
                        style: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600)),
                  ),
                  IconButton(
                    tooltip: context.l10n.newFolder,
                    icon: const Icon(Icons.create_new_folder_outlined, color: Colors.white),
                    onPressed: () => showCreateFolderBottomSheet(context),
                  ),
                ],
              ),
            ),
            Flexible(
              child: ListView.builder(
                shrinkWrap: true,
                itemCount: rows.length,
                itemBuilder: (context, index) {
                  final entry = rows[index];
                  final folder = entry.folder;
                  final hasChildren = childFolders(folders, folder.id).isNotEmpty;
                  final selected = folder.id == widget.selectedFolderId;
                  return InkWell(
                    onTap: () {
                      widget.onFolderSelected(folder.id);
                      Navigator.pop(context);
                    },
                    child: Container(
                      color: selected ? Colors.white.withValues(alpha: 0.06) : null,
                      padding: EdgeInsets.only(left: 8.0 + 20.0 * entry.depth, right: 4),
                      child: Row(
                        children: [
                          SizedBox(
                            width: 32,
                            child: hasChildren
                                ? IconButton(
                                    padding: EdgeInsets.zero,
                                    icon: Icon(
                                      _collapsed.contains(folder.id) ? Icons.chevron_right : Icons.expand_more,
                                      color: Colors.white70,
                                    ),
                                    onPressed: () => setState(() => _collapsed.contains(folder.id)
                                        ? _collapsed.remove(folder.id)
                                        : _collapsed.add(folder.id)),
                                  )
                                : null,
                          ),
                          FaIcon(folderIconToFa(folder.icon), size: 14, color: folder.colorValue),
                          const SizedBox(width: 10),
                          Expanded(
                            child: Text(folder.name,
                                style: TextStyle(
                                  color: Colors.white,
                                  fontWeight: selected ? FontWeight.w600 : FontWeight.normal,
                                )),
                          ),
                          if (folder.conversationCount > 0)
                            Text('${folder.conversationCount}', style: TextStyle(color: Colors.grey.shade500)),
                          PopupMenuButton<String>(
                            icon: const Icon(Icons.more_vert, color: Colors.white54, size: 20),
                            onSelected: (action) {
                              switch (action) {
                                case 'sub':
                                  showCreateFolderBottomSheet(context, parentId: folder.id);
                                  break;
                                case 'move':
                                  _move(folder);
                                  break;
                                case 'edit':
                                  showCreateFolderBottomSheet(context, folderToEdit: folder);
                                  break;
                              }
                            },
                            itemBuilder: (_) => [
                              if (canPlaceFolder(folders, null, folder.id))
                                PopupMenuItem(value: 'sub', child: Text(context.l10n.newSubfolder)),
                              if (!folder.isSystem) PopupMenuItem(value: 'move', child: Text(context.l10n.moveFolder)),
                              PopupMenuItem(value: 'edit', child: Text(context.l10n.editFolder)),
                            ],
                          ),
                        ],
                      ),
                    ),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}
