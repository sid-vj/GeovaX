"""Tamper-evident provenance ledger.

Land records are adversarial data. The value of a harmonised cadastre is not only that it
is accurate today but that nobody can quietly change it tomorrow and claim it always said
that. SAMANVAY therefore writes every state transition into an append-only, hash-chained
ledger.

Each entry commits to:

* the entity it concerns,
* the operation and its payload,
* the actor,
* the hash of the previous entry.

Any modification of any historical entry breaks the chain from that point forward, and
``verify()`` reports the exact index at which the break occurs. A daily Merkle root can be
anchored externally (published in the state gazette, or written to a notary service) to
extend the guarantee to the ledger owner themselves.

This is deliberately *not* a blockchain. There is no consensus problem here: DoLR is the
single writer. What is needed is integrity and auditability, and a hash chain plus an
external anchor gives exactly that at a millionth of the cost.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Iterator

GENESIS = "0" * 64


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class LedgerEntry:
    index: int
    timestamp: str
    entity_id: str
    operation: str
    actor: str
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str

    def recompute(self) -> str:
        return hashlib.sha256(
            "|".join(
                [
                    str(self.index),
                    self.timestamp,
                    self.entity_id,
                    self.operation,
                    self.actor,
                    _canonical(self.payload),
                    self.prev_hash,
                ]
            ).encode()
        ).hexdigest()

    def to_json(self) -> str:
        return _canonical(asdict(self))


class ProvenanceLedger:
    """Append-only hash chain, persisted as JSON lines.

    JSON lines rather than a database table on purpose: the ledger must be verifiable by a
    third party with nothing but a text editor and ``sha256sum``.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self._entries: list[LedgerEntry] = []
        self._path = str(path) if path else None
        self._lock = threading.Lock()
        if self._path and os.path.exists(self._path):
            self._load()

    # -- writing ------------------------------------------------------------------

    def append(
        self,
        entity_id: str,
        operation: str,
        payload: dict[str, Any],
        actor: str = "samanvay/auto",
    ) -> LedgerEntry:
        with self._lock:
            index = len(self._entries)
            prev = self._entries[-1].entry_hash if self._entries else GENESIS
            ts = datetime.now(timezone.utc).isoformat()
            stub = LedgerEntry(
                index=index,
                timestamp=ts,
                entity_id=entity_id,
                operation=operation,
                actor=actor,
                payload=payload,
                prev_hash=prev,
                entry_hash="",
            )
            entry = LedgerEntry(**{**asdict(stub), "entry_hash": stub.recompute()})
            self._entries.append(entry)
            if self._path:
                os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(entry.to_json() + "\n")
            return entry

    # -- reading ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[LedgerEntry]:
        return iter(self._entries)

    @property
    def head(self) -> str:
        return self._entries[-1].entry_hash if self._entries else GENESIS

    def history(self, entity_id: str) -> list[LedgerEntry]:
        return [e for e in self._entries if e.entity_id == entity_id]

    # -- integrity ----------------------------------------------------------------

    def verify(self) -> tuple[bool, int | None, str]:
        """Return ``(ok, broken_index, message)``."""
        prev = GENESIS
        for e in self._entries:
            if e.prev_hash != prev:
                return False, e.index, f"chain break at {e.index}: prev_hash mismatch"
            if e.recompute() != e.entry_hash:
                return False, e.index, f"payload tampered at {e.index}"
            prev = e.entry_hash
        return True, None, f"chain intact over {len(self._entries)} entries"

    def merkle_root(self) -> str:
        """Merkle root over all entry hashes, for external anchoring."""
        layer = [e.entry_hash for e in self._entries]
        if not layer:
            return GENESIS
        while len(layer) > 1:
            if len(layer) % 2:
                layer.append(layer[-1])
            layer = [
                hashlib.sha256((layer[i] + layer[i + 1]).encode()).hexdigest()
                for i in range(0, len(layer), 2)
            ]
        return layer[0]

    def inclusion_proof(self, index: int) -> list[tuple[str, str]]:
        """Audit path proving entry ``index`` is in the tree rooted at ``merkle_root()``.

        Returned as ``[(side, hash), ...]`` where side is "L" or "R". A citizen given a
        record, its entry hash, this path and the gazette-published root can verify their
        land record independently of the department.
        """
        layer = [e.entry_hash for e in self._entries]
        if not layer or index >= len(layer):
            return []
        path: list[tuple[str, str]] = []
        idx = index
        while len(layer) > 1:
            if len(layer) % 2:
                layer.append(layer[-1])
            sibling = idx ^ 1
            path.append(("L" if sibling < idx else "R", layer[sibling]))
            layer = [
                hashlib.sha256((layer[i] + layer[i + 1]).encode()).hexdigest()
                for i in range(0, len(layer), 2)
            ]
            idx //= 2
        return path

    @staticmethod
    def verify_inclusion(leaf: str, path: list[tuple[str, str]], root: str) -> bool:
        cur = leaf
        for side, h in path:
            cur = hashlib.sha256(((h + cur) if side == "L" else (cur + h)).encode()).hexdigest()
        return cur == root

    # -- persistence --------------------------------------------------------------

    def _load(self) -> None:
        assert self._path
        with open(self._path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    self._entries.append(LedgerEntry(**json.loads(line)))
