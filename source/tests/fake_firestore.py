from __future__ import annotations

import copy
import json
import threading
from typing import Any


class FakeSnapshot:
    def __init__(self, reference: FakeDocumentReference, value: dict[str, Any] | None):
        self.reference = reference
        self.id = reference.id
        self.exists = value is not None
        self._value = copy.deepcopy(value)

    def to_dict(self):
        return copy.deepcopy(self._value)


class FakeDocumentReference:
    def __init__(self, client: FakeFirestoreClient, collection: str, doc_id: str):
        self.client = client
        self.collection_name = collection
        self.id = doc_id
        self.path = f"{collection}/{doc_id}"

    def get(self, transaction=None):
        return FakeSnapshot(self, self.client._documents.get(self.path))

    def set(self, value: dict[str, Any], merge: bool = False):
        self.client._set(self, value, merge=merge)

    def update(self, value: dict[str, Any]):
        self.client._set(self, value, merge=True)

    def delete(self):
        self.client._documents.pop(self.path, None)


class FakeQuery:
    def __init__(
        self,
        collection: FakeCollectionReference,
        filters: list[tuple[str, str, Any]] | None = None,
    ):
        self.collection = collection
        self.filters = filters or []

    def where(self, field: str, operator: str, value: Any):
        return FakeQuery(self.collection, [*self.filters, (field, operator, value)])

    def stream(self):
        for snapshot in self.collection.stream():
            row = snapshot.to_dict() or {}
            include = True
            for field, operator, expected in self.filters:
                actual = row.get(field)
                if operator == "==" and actual != expected:
                    include = False
                elif operator == "in" and actual not in expected:
                    include = False
                elif operator not in {"==", "in"}:
                    raise NotImplementedError(operator)
            if include:
                yield snapshot


class FakeCollectionReference:
    def __init__(self, client: FakeFirestoreClient, name: str):
        self.client = client
        self.id = name

    def document(self, doc_id: str):
        return FakeDocumentReference(self.client, self.id, doc_id)

    def stream(self):
        prefix = f"{self.id}/"
        for path in sorted(self.client._documents):
            if path.startswith(prefix) and "/" not in path[len(prefix) :]:
                doc_id = path[len(prefix) :]
                yield FakeSnapshot(
                    self.document(doc_id), self.client._documents[path]
                )

    def where(self, field: str, operator: str, value: Any):
        return FakeQuery(self, [(field, operator, value)])


class FakeBatch:
    def __init__(self, client: FakeFirestoreClient):
        self.client = client
        self.operations: list[tuple[str, FakeDocumentReference, Any, bool]] = []

    def set(self, ref, value, merge: bool = False):
        self.operations.append(("set", ref, copy.deepcopy(value), merge))

    def update(self, ref, value):
        self.operations.append(("set", ref, copy.deepcopy(value), True))

    def delete(self, ref):
        self.operations.append(("delete", ref, None, False))

    def commit(self):
        for action, ref, value, merge in self.operations:
            if action == "delete":
                ref.delete()
            else:
                self.client._set(ref, value, merge=merge)
        count = len(self.operations)
        self.operations = []
        return [None] * count


class FakeTransaction(FakeBatch):
    def get_all(self, refs):
        return [ref.get(transaction=self) for ref in refs]


class FakeFirestoreClient:
    is_fake_firestore = True

    def __init__(self):
        self._documents: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.transaction_write_bytes: list[int] = []
        self.closed = False

    def collection(self, name: str):
        return FakeCollectionReference(self, name)

    def batch(self):
        return FakeBatch(self)

    def get_all(self, refs):
        return [ref.get() for ref in refs]

    def run_transaction(self, callback):
        with self._lock:
            transaction = FakeTransaction(self)
            result = callback(transaction)
            self.transaction_write_bytes.append(
                sum(
                    len(
                        json.dumps(
                            value,
                            default=str,
                            ensure_ascii=False,
                        ).encode("utf-8")
                    )
                    for action, _, value, _ in transaction.operations
                    if action == "set"
                )
            )
            transaction.commit()
            return result

    def _set(self, ref, value: dict[str, Any], *, merge: bool):
        incoming = copy.deepcopy(value)
        if merge and ref.path in self._documents:
            self._documents[ref.path] = {
                **self._documents[ref.path],
                **incoming,
            }
        else:
            self._documents[ref.path] = incoming

    def close(self):
        self.closed = True
