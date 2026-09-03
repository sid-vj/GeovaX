"""Normalisation of the identifiers that actually join Indian land records.

Survey numbers are the join key of the Indian cadastre and they are written a dozen ways:
``437``, ``437/2``, ``437/2A``, ``437-2A``, ``437 / 2 A``, ``0437/2A``, ``437/2-A``,
``437/2A1``, and in Tamil script. Two records that refer to the same parcel routinely
disagree textually while agreeing semantically.

Everything here is deliberately conservative: it removes formatting variation and nothing
else. Survey number ``437/2`` and ``437/3`` are adjacent siblings, never the same parcel,
and no normalisation in this module will ever conflate them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TAMIL_DIGITS = str.maketrans("௦௧௨௩௪௫௬௭௮௯", "0123456789")
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_TELUGU_DIGITS = str.maketrans("౦౧౨౩౪౫౬౭౮౯", "0123456789")


@dataclass(frozen=True)
class SurveyNumber:
    parent: str
    subdivisions: tuple[str, ...]
    raw: str

    @property
    def canonical(self) -> str:
        return "/".join([self.parent, *self.subdivisions]) if self.subdivisions else self.parent

    @property
    def depth(self) -> int:
        return len(self.subdivisions)

    def is_sibling_of(self, other: "SurveyNumber") -> bool:
        return (self.parent == other.parent
                and self.subdivisions[:-1] == other.subdivisions[:-1]
                and self.subdivisions != other.subdivisions)

    def is_descendant_of(self, other: "SurveyNumber") -> bool:
        return (self.parent == other.parent
                and len(self.subdivisions) > len(other.subdivisions)
                and self.subdivisions[:len(other.subdivisions)] == other.subdivisions)


def parse_survey_number(raw: object) -> SurveyNumber | None:
    if raw in (None, ""):
        return None
    s = str(raw).strip()
    s = s.translate(_TAMIL_DIGITS).translate(_DEVANAGARI_DIGITS).translate(_TELUGU_DIGITS)
    s = s.upper()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[.\\]", "/", s)
    s = re.sub(r"[-–—_]", "/", s)
    s = re.sub(r"/{2,}", "/", s).strip("/")
    if not s:
        return None
    parts = s.split("/")
    parent = parts[0].lstrip("0") or "0"
    if not re.fullmatch(r"\d{1,6}[A-Z]?", parent):
        # things like "TS 12" or "RS437"
        m = re.search(r"(\d{1,6}[A-Z]?)", parent)
        if not m:
            return None
        parent = m.group(1).lstrip("0") or "0"
    subs = []
    for p in parts[1:]:
        p = p.strip()
        if not p:
            continue
        # split "2A1" into a canonical token but keep it atomic; "2A" != "2"
        p = re.sub(r"^0+(?=\d)", "", p)
        subs.append(p)
    return SurveyNumber(parent=parent, subdivisions=tuple(subs), raw=str(raw))


def normalise_survey_number(raw: object) -> str:
    sn = parse_survey_number(raw)
    return sn.canonical if sn else ""


def normalise_ward(raw: object) -> str:
    if raw in (None, ""):
        return ""
    s = re.sub(r"[^0-9A-Za-z]", "", str(raw)).upper()
    s = s.lstrip("0")
    return s or "0"


_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def normalise_zone(raw: object) -> str:
    """GCC writes zones as Roman numerals in one layer and Arabic in another."""
    if raw in (None, ""):
        return ""
    s = str(raw).strip().upper()
    if re.fullmatch(r"[IVXLCDM]+", s):
        total = 0
        prev = 0
        for ch in reversed(s):
            v = _ROMAN[ch]
            total = total - v if v < prev else total + v
            prev = max(prev, v)
        return str(total)
    digits = re.sub(r"\D", "", s)
    return digits.lstrip("0") or (s if not digits else "0")


def normalise_door_number(raw: object) -> str:
    if raw in (None, ""):
        return ""
    s = str(raw).upper().strip()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[,\\]", "/", s)
    s = re.sub(r"(?:NO|D\.?NO|DOOR)\.?", "", s)
    s = re.sub(r"[-–—]", "/", s)
    s = re.sub(r"/{2,}", "/", s).strip("/")
    return s


def normalise_lgd(raw: object) -> str:
    if raw in (None, ""):
        return ""
    s = re.sub(r"\D", "", str(raw))
    return s.lstrip("0") or "0"
