"""
ChromaDB vector database replacement for Pinecone.
Activated when LOCAL_VECTOR_DB=chroma and no Pinecone key is set.
Implements the same interface as the Pinecone paths in vector_db.py.
"""

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)

# Chroma client & collection (lazy init)
_chroma_client: Optional[chromadb.PersistentClient] = None
_chroma_collection: Optional[Any] = None
_chroma_enabled = False

CHROMA_PATH = os.environ.get("CHROMA_DATA_PATH", "/data/chroma/vector_db")


def _init_chroma() -> None:
    """Initialize ChromaDB persistent client and collection."""
    global _chroma_client, _chroma_collection, _chroma_enabled

    if _chroma_client is not None:
        return

    try:
        os.makedirs(CHROMA_PATH, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(
            path=CHROMA_PATH,
            settings=Settings(anonymized_telemetry=False),
        )
        _chroma_collection = _chroma_client.get_or_create_collection(
            name="omi_vectors",
            metadata={"hnsw:space": "cosine"},
        )
        _chroma_enabled = True
        logger.info(f"ChromaDB vector store ready at {CHROMA_PATH} (collection: omi_vectors)")
    except Exception as e:
        logger.error(f"ChromaDB init failed: {e}. Vector search disabled.")
        _chroma_enabled = False


def _ready() -> bool:
    """Open the store on first use; True once the collection is usable.

    Every function used to return on `not _chroma_enabled` before calling
    _init_chroma(), the only thing that sets it, so the store never opened:
    with LOCAL_VECTOR_DB=chroma nothing was stored and every search was empty.
    """
    _init_chroma()
    return _chroma_enabled


def is_chroma_enabled() -> bool:
    return os.environ.get("LOCAL_VECTOR_DB", "").lower() == "chroma"


def chroma_upsert(
    doc_id: str,
    vector: List[float],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Upsert a single vector into Chroma."""
    if not _ready():
        return
    try:
        _chroma_collection.upsert(
            ids=[doc_id],
            embeddings=[vector],
            metadatas=[metadata or {}],
        )
    except Exception as e:
        logger.error(f"Chroma upsert failed for {doc_id}: {e}")


def chroma_upsert_batch(
    ids: List[str],
    vectors: List[List[float]],
    metadatas: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Upsert multiple vectors into Chroma."""
    if not ids or not _ready():
        return
    try:
        _chroma_collection.upsert(
            ids=ids,
            embeddings=vectors,
            metadatas=metadatas or [{}] * len(ids),
        )
    except Exception as e:
        logger.error(f"Chroma batch upsert failed: {e}")


def chroma_query(
    query_vector: Optional[List[float]] = None,
    query_text: Optional[str] = None,
    uid: Optional[str] = None,
    top_k: int = 5,
    filter_created_after: Optional[int] = None,
    filter_created_before: Optional[int] = None,
) -> List[str]:
    """Query Chroma and return matching document IDs."""
    if not _ready():
        return []

    try:
        # Chroma takes one condition, or several under a single "$and": mixing
        # "uid" with a sibling "$and" is rejected, which used to empty every
        # uid + date-range search.
        conditions: List[Dict[str, Any]] = []
        if uid:
            conditions.append({"uid": uid})
        if filter_created_after is not None:
            conditions.append({"created_at": {"$gte": filter_created_after}})
        if filter_created_before is not None:
            conditions.append({"created_at": {"$lte": filter_created_before}})

        kwargs: Dict[str, Any] = {"n_results": top_k}
        if len(conditions) == 1:
            kwargs["where"] = conditions[0]
        elif conditions:
            kwargs["where"] = {"$and": conditions}

        if query_vector is not None:
            results = _chroma_collection.query(query_embeddings=[query_vector], **kwargs)
        elif query_text is not None:
            results = _chroma_collection.query(query_texts=[query_text], **kwargs)
        else:
            return []

        ids_list = results.get("ids", [[]])
        return ids_list[0] if ids_list else []

    except Exception as e:
        logger.error(f"Chroma query failed: {e}")
        return []


def chroma_delete(ids: List[str]) -> None:
    """Delete vectors by ID."""
    if not ids or not _ready():
        return
    try:
        _chroma_collection.delete(ids=ids)
    except Exception as e:
        logger.error(f"Chroma delete failed: {e}")


def chroma_delete_by_uid(uid: str) -> None:
    """Delete all vectors for a user."""
    if not _ready():
        return
    try:
        _chroma_collection.delete(where={"uid": uid})
    except Exception as e:
        logger.error(f"Chroma delete by uid failed: {e}")


def chroma_count() -> int:
    """Return number of vectors stored."""
    if not _ready():
        return 0
    try:
        return _chroma_collection.count()
    except Exception:
        return 0
