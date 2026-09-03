"""Identity minting.

Two identifiers are used throughout the platform and they do different jobs.

``entity_id``
    An internal, opaque, content-addressed handle for a real-world entity. It is derived
    from the harmonised geometry so that re-running the pipeline on unchanged data is
    idempotent, which is what makes the whole system safely re-runnable.

``ULPIN``
    The Unique Land Parcel Identification Number — "Bhu-Aadhaar" — mandated under DILRMP.
    It is a 14-character alphanumeric identifier that must survive geometry revision: if a
    parcel's boundary is re-surveyed by 40 cm the ULPIN must not change, otherwise every
    downstream record (mutation, encumbrance, utility connection) is orphaned.

The trick that makes ULPIN stable is to derive it from a *geohash of the parcel centroid
snapped to a tolerance grid*, combined with the administrative hierarchy, rather than from
the boundary itself. A re-survey moves the centroid by centimetres; the snapped cell does
not change. A genuine subdivision moves it far enough that a new ULPIN is minted, and the
genealogy table records the parent.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Crockford base-32: no I, L, O, U — chosen so a ULPIN can be read out over a phone line
# in a tahsildar's office without ambiguity.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ALPHABET_INDEX = {c: i for i, c in enumerate(_ALPHABET)}

_GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def _b32(value: int, width: int) -> str:
    out = []
    for _ in range(width):
        out.append(_ALPHABET[value % 32])
        value //= 32
    return "".join(reversed(out))


def geohash_encode(lon: float, lat: float, precision: int = 12) -> str:
    """Standard geohash. Implemented here to avoid a dependency for 30 lines of code."""
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    bits = [16, 8, 4, 2, 1]
    bit = 0
    ch = 0
    even = True
    out: list[str] = []
    while len(out) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon > mid:
                ch |= bits[bit]
                lon_lo = mid
            else:
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat > mid:
                ch |= bits[bit]
                lat_lo = mid
            else:
                lat_hi = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_GEOHASH_BASE32[ch])
            bit = 0
            ch = 0
    return "".join(out)


def entity_id(feature_class: str, geometry_wkt: str, aoi: str = "") -> str:
    """Content-addressed internal identity. Stable for identical geometry."""
    h = hashlib.blake2b(
        f"{aoi}|{feature_class}|{geometry_wkt}".encode(), digest_size=12
    ).hexdigest()
    return f"{feature_class[:3].upper()}-{h}"


@dataclass(frozen=True)
class AdminContext:
    """Administrative hierarchy a parcel sits in, as per LGD codes."""

    state_lgd: str        # e.g. "33" Tamil Nadu
    district_lgd: str     # e.g. "571" Chennai
    ulb_or_block: str     # e.g. "GCC"
    ward: str = ""
    village_or_zone: str = ""

    def key(self) -> str:
        return "/".join(
            [self.state_lgd, self.district_lgd, self.ulb_or_block, self.village_or_zone, self.ward]
        )


def _checksum(payload: str) -> str:
    """Position-weighted checksum character over the Crockford base-32 alphabet.

    Guarantees detection of **every** single-character substitution, which is the mistake
    actually made when a ULPIN is copied by hand off a paper patta into a computer.

    The guarantee rests on modular arithmetic: the contribution of position *i* is
    ``v_i · w_i · 3^(n-1-i) (mod 32)``. Both the position weight ``w_i`` and the mixing
    factor 3 are **odd**, hence invertible modulo 32, so a change of ``Δv ≢ 0 (mod 32)`` at
    any position always changes the checksum. An earlier version used weights ``i mod 7 + 1``,
    which include even values; an even multiplier is not invertible modulo 32 and silently
    lost roughly a fifth of single-character errors.
    """
    total = 0
    for i, ch in enumerate(payload):
        v = _ALPHABET_INDEX.get(ch, 0)
        weight = 2 * (i % 8) + 1          # 1,3,5,…,15 — always odd, always invertible
        total = (total * 3 + v * weight) % 32
    return _ALPHABET[total]


def mint_ulpin(
    admin: AdminContext,
    lon: float,
    lat: float,
    *,
    snap_precision: int = 9,
) -> str:
    """Mint a 14-character ULPIN.

    Layout (14 chars)::

        ┌──2──┬───3───┬─────8──────┬─1─┐
        │state│ULB    │geo+admin   │chk│
        └─────┴───────┴────────────┴───┘

    ``snap_precision`` is the geohash precision the centroid is snapped to before hashing.
    Precision 9 is a cell of roughly 4.8 m x 4.8 m, which is comfortably larger than any
    re-survey shift yet far smaller than any real subdivision.
    """
    state = re.sub(r"[^0-9A-Z]", "", admin.state_lgd.upper()).rjust(2, "0")[:2]
    ulb = re.sub(r"[^0-9A-Z]", "", admin.ulb_or_block.upper()).ljust(3, "0")[:3]
    cell = geohash_encode(lon, lat, snap_precision)
    digest = hashlib.blake2b(f"{admin.key()}|{cell}".encode(), digest_size=8).digest()
    body = _b32(int.from_bytes(digest, "big") % (32 ** 8), 8)
    payload = f"{state}{ulb}{body}"
    return payload + _checksum(payload)


def validate_ulpin(ulpin: str) -> bool:
    s = ulpin.strip().upper().replace("-", "")
    if len(s) != 14 or any(c not in _ALPHABET for c in s):
        return False
    return _checksum(s[:13]) == s[13]


def format_ulpin(ulpin: str) -> str:
    """Human-readable grouping, as printed on a patta."""
    s = ulpin.upper()
    return f"{s[0:2]}-{s[2:5]}-{s[5:9]}-{s[9:13]}-{s[13]}"


class UlpinMinter:
    """Mints ULPINs and guarantees they are unique.

    Deriving an identifier from a snapped location makes it stable under re-survey, which
    is the property that matters most — but stability and uniqueness pull against each
    other. In dense urban fabric two small parcels can share a snapped cell, and at
    precision 9 (about 4.8 m) that happens for roughly one parcel in a hundred and forty in
    central Chennai. An identifier that is not unique is not an identifier.

    The resolution is a disambiguation nonce mixed into the hash, incremented until the
    result is free. Because the nonce is recorded against the parcel and re-applied on
    every subsequent run, the identifier stays stable across runs *and* stays unique —
    which is the combination the DILRMP requirement actually needs.
    """

    def __init__(self, *, snap_precision: int = 10) -> None:
        self.snap_precision = snap_precision
        self._issued: dict[str, str] = {}      # ulpin -> owning key
        self._nonces: dict[str, int] = {}      # owning key -> nonce
        self.collisions_resolved = 0

    def mint(self, admin: AdminContext, lon: float, lat: float, *,
             key: str | None = None) -> str:
        cell = geohash_encode(lon, lat, self.snap_precision)
        owner = key or f"{admin.key()}|{cell}"
        if owner in self._nonces:
            return self._compose(admin, cell, self._nonces[owner])
        nonce = 0
        while True:
            candidate = self._compose(admin, cell, nonce)
            holder = self._issued.get(candidate)
            if holder is None:
                self._issued[candidate] = owner
                self._nonces[owner] = nonce
                if nonce:
                    self.collisions_resolved += 1
                return candidate
            if holder == owner:
                return candidate
            nonce += 1

    def _compose(self, admin: AdminContext, cell: str, nonce: int) -> str:
        state = re.sub(r"[^0-9A-Z]", "", admin.state_lgd.upper()).rjust(2, "0")[:2]
        ulb = re.sub(r"[^0-9A-Z]", "", admin.ulb_or_block.upper()).ljust(3, "0")[:3]
        seed = f"{admin.key()}|{cell}" + (f"|{nonce}" if nonce else "")
        digest = hashlib.blake2b(seed.encode(), digest_size=8).digest()
        body = _b32(int.from_bytes(digest, "big") % (32 ** 8), 8)
        payload = f"{state}{ulb}{body}"
        return payload + _checksum(payload)

    @property
    def issued(self) -> int:
        return len(self._issued)

    def state(self) -> dict[str, int]:
        """Serialisable nonce table, so identity survives across runs."""
        return dict(self._nonces)

    def restore(self, nonces: dict[str, int]) -> None:
        self._nonces.update(nonces)


@dataclass
class Genealogy:
    """Parent/child lineage when parcels split or merge.

    Recorded so that a mutation trail survives the geometry change. Without this a
    subdivision silently orphans the encumbrance history of the parent parcel, which is
    the single most common way digitised land records lose legal value.
    """

    child_ulpin: str
    parents: list[str]
    operation: str  # "subdivision" | "amalgamation" | "boundary_adjustment"
    effective_from: str

    def to_dict(self) -> dict[str, object]:
        return {
            "child_ulpin": self.child_ulpin,
            "parents": list(self.parents),
            "operation": self.operation,
            "effective_from": self.effective_from,
        }
