from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import firebase_admin
from firebase_admin import credentials, firestore, storage

from .config import Settings


_FIREBASE_APP: Optional[firebase_admin.App] = None


@dataclass
class FirestoreFilter:
    field: str
    op: str
    value: Any


def init_firebase(settings: Settings) -> None:
    """
    Initialize the Firebase Admin SDK once per process.

    Uses either:
    - Explicit service account JSON via `firebase_credentials_file`, or
    - Application Default Credentials (ADC) if not provided.
    """
    global _FIREBASE_APP

    if _FIREBASE_APP is not None:
        return

    if settings.firebase_credentials_file:
        cred = credentials.Certificate(settings.firebase_credentials_file)
    else:
        cred = credentials.ApplicationDefault()

    _FIREBASE_APP = firebase_admin.initialize_app(
        cred,
        {"projectId": settings.firebase_project_id},
    )


def get_firestore_client() -> firestore.Client:
    if _FIREBASE_APP is None:
        raise RuntimeError("Firebase app not initialized. Call init_firebase() first.")
    return firestore.client(app=_FIREBASE_APP)


def get_default_bucket() -> storage.bucket.Bucket:
    if _FIREBASE_APP is None:
        raise RuntimeError("Firebase app not initialized. Call init_firebase() first.")
    return storage.bucket(app=_FIREBASE_APP)


def store_file(
    path: str,
    data: bytes,
    content_type: str,
    metadata: Optional[Dict[str, str]] = None,
) -> str:
    """
    Store a file in the default Firebase Storage bucket.

    Returns the public (or signed) URL of the stored object, depending on bucket config.
    """
    bucket = get_default_bucket()
    blob = bucket.blob(path)
    blob.upload_from_string(data, content_type=content_type)
    if metadata:
        blob.metadata = metadata
        blob.patch()
    # The actual URL exposure pattern (public vs signed) can be configured later.
    return blob.public_url


def write_doc(
    collection: str,
    doc_id: Optional[str],
    data: Dict[str, Any],
) -> str:
    """
    Write a document to Firestore.

    If `doc_id` is None, an auto ID is generated.
    """
    db = get_firestore_client()
    col_ref = db.collection(collection)
    if doc_id:
        doc_ref = col_ref.document(doc_id)
        doc_ref.set(data)
        return doc_ref.id
    doc_ref = col_ref.document()
    doc_ref.set(data)
    return doc_ref.id


def update_doc(
    collection: str,
    doc_id: str,
    data: Dict[str, Any],
) -> None:
    db = get_firestore_client()
    doc_ref = db.collection(collection).document(doc_id)
    doc_ref.update(data)


def read_doc(
    collection: str,
    doc_id: str,
) -> Optional[Dict[str, Any]]:
    db = get_firestore_client()
    doc_ref = db.collection(collection).document(doc_id)
    snap = doc_ref.get()
    if not snap.exists:
        return None
    return snap.to_dict() or {}


def query_collection(
    collection: str,
    filters: Optional[Iterable[FirestoreFilter]] = None,
    limit: Optional[int] = None,
    order_by: Optional[Tuple[str, firestore.Query.DIRECTION]] = None,
) -> List[Dict[str, Any]]:
    db = get_firestore_client()
    query: firestore.Query = db.collection(collection)

    if filters:
        for f in filters:
            query = query.where(f.field, f.op, f.value)

    if order_by:
        field, direction = order_by
        query = query.order_by(field, direction=direction)

    if limit is not None:
        query = query.limit(limit)

    docs = query.stream()
    return [d.to_dict() or {} for d in docs]

