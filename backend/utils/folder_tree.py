"""Nested conversation folders: pure tree rules over a user's flat folder list.

Folders stay a flat Firestore collection; a folder's ``parent_id`` (None = top
level) makes them a tree. Everything here works on the list ``get_folders``
returns, so routers validate a move/create against the same snapshot they
just read and tests need no database.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

# Deep enough for "Work / Clients / Acme / Q4" with room to spare; shallow
# enough that a path still fits on one line in the app's folder picker.
MAX_FOLDER_DEPTH = 5

FolderRecord = Mapping[str, Any]


class FolderTreeError(ValueError):
    """A create/move that would break the tree. The message is user-facing."""


def _by_id(folders: Iterable[FolderRecord]) -> Dict[str, FolderRecord]:
    return {str(f.get('id')): f for f in folders if f.get('id')}


def parent_of(folder: FolderRecord) -> Optional[str]:
    parent = folder.get('parent_id')
    return str(parent) if parent else None


def ancestors(folders: Sequence[FolderRecord], folder_id: str) -> List[str]:
    """Ids from ``folder_id``'s parent up to its root. Stops at a missing parent or a cycle."""
    index = _by_id(folders)
    chain: List[str] = []
    seen = {folder_id}
    current = index.get(folder_id)
    while current is not None:
        parent = parent_of(current)
        if parent is None or parent in seen or parent not in index:
            break
        chain.append(parent)
        seen.add(parent)
        current = index[parent]
    return chain


def depth(folders: Sequence[FolderRecord], folder_id: str) -> int:
    """1 for a top-level folder."""
    return len(ancestors(folders, folder_id)) + 1


def children(folders: Sequence[FolderRecord], folder_id: Optional[str]) -> List[FolderRecord]:
    """Direct children of ``folder_id`` (None = top level). A parent that no longer exists counts as top level."""
    index = _by_id(folders)
    out = []
    for folder in folders:
        parent = parent_of(folder)
        if parent is not None and parent not in index:
            parent = None
        if parent == folder_id:
            out.append(folder)
    return out


def descendant_ids(folders: Sequence[FolderRecord], folder_id: str) -> List[str]:
    """Every folder below ``folder_id``, breadth first, excluding it."""
    found: List[str] = []
    frontier = [folder_id]
    seen = {folder_id}
    while frontier:
        next_frontier: List[str] = []
        for fid in frontier:
            for child in children(folders, fid):
                cid = str(child.get('id'))
                if cid in seen:
                    continue
                seen.add(cid)
                found.append(cid)
                next_frontier.append(cid)
        frontier = next_frontier
    return found


def subtree_height(folders: Sequence[FolderRecord], folder_id: str) -> int:
    """Levels in ``folder_id``'s subtree including itself (a leaf is 1)."""
    below = children(folders, folder_id)
    if not below:
        return 1
    return 1 + max(subtree_height(folders, str(c.get('id'))) for c in below)


def validate_parent(
    folders: Sequence[FolderRecord],
    parent_id: Optional[str],
    *,
    moving_folder_id: Optional[str] = None,
) -> None:
    """Raise FolderTreeError unless ``parent_id`` is a valid parent.

    For a create, ``moving_folder_id`` is None. For a move, the folder may not
    become its own ancestor and its whole subtree must still fit under the
    depth limit.
    """
    if parent_id is None:
        return
    index = _by_id(folders)
    if parent_id not in index:
        raise FolderTreeError('Parent folder not found')
    if moving_folder_id is not None:
        if parent_id == moving_folder_id or parent_id in descendant_ids(folders, moving_folder_id):
            raise FolderTreeError('A folder cannot be moved inside itself')
        added_levels = subtree_height(folders, moving_folder_id)
    else:
        added_levels = 1
    if depth(folders, parent_id) + added_levels > MAX_FOLDER_DEPTH:
        raise FolderTreeError(f'Folders can be nested at most {MAX_FOLDER_DEPTH} levels deep')


def path_names(folders: Sequence[FolderRecord]) -> Dict[str, str]:
    """``{folder_id: "Work / Clients / Acme"}`` for every folder."""
    index = _by_id(folders)
    out: Dict[str, str] = {}
    for fid, folder in index.items():
        names = [str(index[a].get('name') or '') for a in reversed(ancestors(folders, fid))]
        names.append(str(folder.get('name') or ''))
        out[fid] = ' / '.join(names)
    return out
