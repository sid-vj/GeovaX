"""Intelligent attribute mapping — automatic schema matching.

Two departments describe the same parcel with ``survey_number`` and ``kide``; a third calls
it ``Survey_Number``; a fourth stores it inside a compound ``parcel_ref``. Hand-writing the
crosswalk is what makes onboarding a new department a three-month project, and it is
exactly the manual GIS work the problem statement asks to remove.

The matcher combines four independent signals, because each fails on a different input and
their errors are close to uncorrelated:

**1. Lexical.** Normalised name similarity plus the curated alias table in
``canonical.py``. Strong when names are honest; useless for ``kide``.

**2. Instance-based value profiling.** What the column's *values* look like — regex
family, length distribution, cardinality ratio, null rate, numeric range. A column of
values like ``437``, ``437/2A``, ``12`` is a survey number whatever it is called. This is
the signal that actually solves the hard cases.

**3. Distributional.** Jensen-Shannon divergence between the value distributions of the
candidate column and the reference column, computed over character n-grams. Catches
same-domain columns whose formats differ.

**4. Structural.** Correlations between columns: a column that functionally determines
another (village code → village name) matches a reference pair with the same dependency.

Scores are combined with learned weights, thresholded conservatively, and *every* mapping
is emitted with its evidence, because an incorrect attribute mapping in a land record is
worse than no mapping at all — it silently writes one department's meaning into another
department's field.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from rapidfuzz.distance import JaroWinkler
from rapidfuzz import fuzz

from .canonical import PARCEL_SCHEMA, CanonicalField

# --------------------------------------------------------------------------------------
# value profiling
# --------------------------------------------------------------------------------------

PATTERNS: dict[str, re.Pattern[str]] = {
    "integer": re.compile(r"^-?\d+$"),
    "decimal": re.compile(r"^-?\d+\.\d+$"),
    "survey_number": re.compile(r"^\d{1,5}([/\-][0-9A-Za-z]{1,6})*$"),
    "lgd_code": re.compile(r"^\d{3,7}$"),
    "door_number": re.compile(r"^[0-9]{1,4}[A-Za-z]?([/\-][0-9A-Za-z]{1,4})*$"),
    "gcc_gis_id": re.compile(r"^[A-Z]-\d{3}-\d{2}-\d{5}$"),
    "date_iso": re.compile(r"^\d{4}-\d{2}-\d{2}"),
    "date_text": re.compile(r"^[A-Z][a-z]{2} \d{1,2}, \d{4}"),
    "floors": re.compile(r"^(G|B)?(\+\d+)?$|^\d{1,3}$"),
    "boolean01": re.compile(r"^[01]$"),
    "name_like": re.compile(r"^[A-Za-zऀ-෿][A-Za-zऀ-෿ .'\-]{2,}$"),
    "code_alnum": re.compile(r"^[A-Z0-9_\-]{2,20}$"),
}


@dataclass
class ColumnProfile:
    """A fingerprint of one source column, computed from its values."""

    name: str
    n: int = 0
    n_null: int = 0
    distinct: int = 0
    pattern_hits: dict[str, float] = field(default_factory=dict)
    mean_length: float = 0.0
    length_std: float = 0.0
    numeric_min: float | None = None
    numeric_max: float | None = None
    numeric_mean: float | None = None
    charset: str = ""
    ngram_dist: dict[str, float] = field(default_factory=dict)
    sample: list[str] = field(default_factory=list)

    @property
    def null_rate(self) -> float:
        return self.n_null / self.n if self.n else 1.0

    @property
    def cardinality_ratio(self) -> float:
        return self.distinct / max(self.n - self.n_null, 1)

    @property
    def dominant_pattern(self) -> str:
        if not self.pattern_hits:
            return "free_text"
        k, v = max(self.pattern_hits.items(), key=lambda kv: kv[1])
        return k if v >= 0.7 else "free_text"

    @property
    def looks_like_identifier(self) -> bool:
        return self.cardinality_ratio > 0.9 and self.null_rate < 0.1

    @property
    def looks_like_category(self) -> bool:
        return self.distinct <= 40 and self.cardinality_ratio < 0.2


def profile_column(name: str, values: Iterable[Any], *, max_n: int = 20000) -> ColumnProfile:
    p = ColumnProfile(name=name)
    seen: set[str] = set()
    lengths: list[int] = []
    nums: list[float] = []
    hits: Counter[str] = Counter()
    grams: Counter[str] = Counter()
    charset: set[str] = set()

    for v in values:
        if p.n >= max_n:
            break
        p.n += 1
        if v is None or v == "":
            p.n_null += 1
            continue
        s = str(v).strip()
        seen.add(s)
        lengths.append(len(s))
        if len(p.sample) < 12:
            p.sample.append(s[:48])
        for pname, pat in PATTERNS.items():
            if pat.match(s):
                hits[pname] += 1
        try:
            nums.append(float(s))
        except ValueError:
            pass
        for ch in s[:40]:
            charset.add("d" if ch.isdigit() else ("a" if ch.isalpha() else ch))
        low = s.lower()
        for i in range(len(low) - 1):
            grams[low[i:i + 2]] += 1

    valid = max(p.n - p.n_null, 1)
    p.distinct = len(seen)
    p.pattern_hits = {k: round(c / valid, 4) for k, c in hits.items()}
    if lengths:
        p.mean_length = sum(lengths) / len(lengths)
        p.length_std = math.sqrt(sum((x - p.mean_length) ** 2 for x in lengths) / len(lengths))
    if nums:
        p.numeric_min, p.numeric_max = min(nums), max(nums)
        p.numeric_mean = sum(nums) / len(nums)
    p.charset = "".join(sorted(charset))
    total = sum(grams.values()) or 1
    p.ngram_dist = {g: c / total for g, c in grams.most_common(120)}
    return p


# --------------------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------------------


@dataclass
class FieldMatch:
    source_column: str
    canonical_field: str
    score: float
    lexical: float
    instance: float
    distributional: float
    structural: float
    evidence: list[str] = field(default_factory=list)
    accepted: bool = False

    def explain(self) -> str:
        return (
            f"{self.source_column!r} -> {self.canonical_field!r} at {self.score:.3f} "
            f"(lexical {self.lexical:.2f}, values {self.instance:.2f}, "
            f"distribution {self.distributional:.2f}, structure {self.structural:.2f})"
            + ("; " + "; ".join(self.evidence) if self.evidence else "")
        )


#: Which value-level pattern each canonical field expects. This is the domain knowledge
#: that makes the instance-based signal work; it is small, explicit and reviewable.
FIELD_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "survey_number": {"patterns": ("survey_number", "integer"), "identifier": True,
                      "max_len": 14},
    "subdivision": {"patterns": ("code_alnum", "integer"), "max_len": 8},
    "patta_number": {"patterns": ("integer", "code_alnum"), "identifier": True},
    "door_number": {"patterns": ("door_number", "gcc_gis_id", "code_alnum"), "identifier": True},
    "state_lgd": {"patterns": ("lgd_code", "integer"), "max_len": 2, "category": True},
    "district_lgd": {"patterns": ("lgd_code", "integer"), "max_len": 4, "category": True},
    "taluk_lgd": {"patterns": ("lgd_code", "integer"), "max_len": 5, "category": True},
    "village_lgd": {"patterns": ("lgd_code", "integer"), "max_len": 7},
    "village_name": {"patterns": ("name_like",), "category": False},
    "taluk_name": {"patterns": ("name_like",), "category": True},
    "district_name": {"patterns": ("name_like",), "category": True},
    "ward": {"patterns": ("integer", "code_alnum"), "max_len": 4, "category": True},
    "zone": {"patterns": ("integer", "code_alnum", "name_like"), "category": True},
    "locality": {"patterns": ("name_like",)},
    "street": {"patterns": ("name_like",)},
    "owner_name": {"patterns": ("name_like",), "identifier": False},
    "recorded_extent_m2": {"patterns": ("decimal", "integer"), "numeric": True},
    "computed_extent_m2": {"patterns": ("decimal",), "numeric": True},
    "max_height_m": {"patterns": ("decimal", "integer"), "numeric": True,
                     "range": (0.0, 350.0)},
    "floors": {"patterns": ("floors", "integer", "code_alnum"), "category": True},
    "construction_type": {"patterns": ("name_like", "code_alnum"), "category": True},
    "land_use": {"patterns": ("name_like", "code_alnum"), "category": True},
    "survey_date": {"patterns": ("date_iso", "date_text")},
    "last_mutation_date": {"patterns": ("date_iso", "date_text")},
    "extraction_confidence": {"patterns": ("decimal",), "numeric": True, "range": (0.0, 1.0)},
}


class SchemaMatcher:
    """Maps an arbitrary source schema onto the canonical land-record schema."""

    WEIGHTS = {"lexical": 0.34, "instance": 0.36, "distributional": 0.18, "structural": 0.12}
    ACCEPT_THRESHOLD = 0.62
    REVIEW_THRESHOLD = 0.45

    def __init__(self, schema: dict[str, CanonicalField] | None = None) -> None:
        self.schema = schema or PARCEL_SCHEMA
        self._reference_profiles: dict[str, ColumnProfile] = {}

    def learn_reference(self, field_name: str, values: Iterable[Any]) -> None:
        """Teach the matcher what a canonical field's values look like in practice.

        Optional but valuable: once one department's ``survey_number`` column has been
        profiled, every other department's is matched by distribution rather than by name.
        """
        self._reference_profiles[field_name] = profile_column(field_name, values)

    # -- signals ------------------------------------------------------------------

    def _lexical(self, column: str, field_name: str, cf: CanonicalField) -> tuple[float, str]:
        col = _norm(column)
        best = JaroWinkler.similarity(col, _norm(field_name))
        why = f"name~{field_name}"
        for alias in cf.aliases:
            s = JaroWinkler.similarity(col, _norm(alias))
            if s > best:
                best, why = s, f"alias~{alias}"
        # token containment: "lgd_village_code" vs "village_lgd"
        ct, ft = set(_tokens(column)), set(_tokens(field_name)) | {
            t for a in cf.aliases for t in _tokens(a)}
        if ct and ft:
            overlap = len(ct & ft) / len(ct | ft)
            if overlap > best:
                best, why = overlap, f"token-overlap {sorted(ct & ft)}"
        partial = fuzz.partial_ratio(col, _norm(field_name)) / 100.0
        best = max(best, partial * 0.85)
        return float(best), why

    def _instance(self, prof: ColumnProfile, field_name: str,
                  cf: CanonicalField) -> tuple[float, list[str]]:
        exp = FIELD_EXPECTATIONS.get(field_name)
        ev: list[str] = []
        if exp is None:
            return 0.35, ev
        score = 0.0
        pats = exp.get("patterns", ())
        hit = max((prof.pattern_hits.get(p, 0.0) for p in pats), default=0.0)
        score += 0.55 * hit
        if hit > 0.8:
            ev.append(f"{hit * 100:.0f}% of values match the {'/'.join(pats)} pattern")
        if exp.get("identifier") and prof.looks_like_identifier:
            score += 0.15
            ev.append(f"near-unique ({prof.cardinality_ratio:.2f} distinct ratio)")
        if exp.get("category") and prof.looks_like_category:
            score += 0.15
            ev.append(f"low cardinality ({prof.distinct} distinct)")
        if exp.get("numeric") and prof.numeric_mean is not None:
            score += 0.10
            rng = exp.get("range")
            if rng and prof.numeric_min is not None:
                if rng[0] <= prof.numeric_min and prof.numeric_max <= rng[1]:
                    score += 0.15
                    ev.append(f"values within the expected range {rng}")
                else:
                    score -= 0.25
                    ev.append(
                        f"values [{prof.numeric_min:.3g}, {prof.numeric_max:.3g}] fall "
                        f"outside the expected range {rng}"
                    )
        if exp.get("max_len") and prof.mean_length > exp["max_len"] + 2:
            score -= 0.20
            ev.append(f"mean length {prof.mean_length:.1f} exceeds expected {exp['max_len']}")
        if cf.domain and prof.sample:
            dom = {d.lower() for d in cf.domain}
            frac = sum(1 for s in prof.sample if s.lower() in dom) / len(prof.sample)
            if frac > 0.5:
                score += 0.25
                ev.append(f"{frac * 100:.0f}% of sampled values are in the controlled vocabulary")
        return max(0.0, min(1.0, score)), ev

    def _distributional(self, prof: ColumnProfile, field_name: str) -> float:
        ref = self._reference_profiles.get(field_name)
        if ref is None or not ref.ngram_dist or not prof.ngram_dist:
            return 0.0
        return 1.0 - _jensen_shannon(prof.ngram_dist, ref.ngram_dist)

    def _structural(self, prof: ColumnProfile, field_name: str,
                    all_profiles: dict[str, ColumnProfile],
                    dependencies: dict[str, set[str]]) -> float:
        """Reward a column that participates in the dependency the field implies."""
        pairs = {
            "village_lgd": "village_name",
            "district_lgd": "district_name",
            "taluk_lgd": "taluk_name",
            "ward": "zone",
        }
        partner = pairs.get(field_name)
        if not partner:
            return 0.0
        deps = dependencies.get(prof.name, set())
        if not deps:
            return 0.0
        for other in deps:
            op = all_profiles.get(other)
            if op and op.dominant_pattern == "name_like":
                return 0.8
        return 0.3

    # -- driver -------------------------------------------------------------------

    def match(self, records: Sequence[dict[str, Any]], *,
              columns: Sequence[str] | None = None) -> list[FieldMatch]:
        if not records:
            return []
        cols = list(columns or sorted({k for r in records for k in r}))
        profiles = {
            c: profile_column(c, (r.get(c) for r in records)) for c in cols
        }
        deps = _functional_dependencies(records, cols)

        candidates: list[FieldMatch] = []
        for c in cols:
            prof = profiles[c]
            for fname, cf in self.schema.items():
                lex, why = self._lexical(c, fname, cf)
                inst, ev = self._instance(prof, fname, cf)
                dist = self._distributional(prof, fname)
                stru = self._structural(prof, fname, profiles, deps)
                w = self.WEIGHTS
                score = (w["lexical"] * lex + w["instance"] * inst
                         + w["distributional"] * dist + w["structural"] * stru)
                # a strong lexical hit on an alias is close to decisive
                if lex > 0.95:
                    score = max(score, 0.5 + 0.5 * inst)
                if score >= self.REVIEW_THRESHOLD:
                    candidates.append(FieldMatch(
                        source_column=c, canonical_field=fname, score=round(score, 4),
                        lexical=round(lex, 4), instance=round(inst, 4),
                        distributional=round(dist, 4), structural=round(stru, 4),
                        evidence=([why] if lex > 0.7 else []) + ev,
                    ))

        return self._assign(candidates)

    def _assign(self, candidates: list[FieldMatch]) -> list[FieldMatch]:
        """Resolve to a one-to-one mapping by greedy descent on score.

        A source column must not be mapped to two canonical fields, and two source columns
        must not both claim one canonical field — the second is how a pipeline ends up
        writing the taluk name into the district name for half a district.
        """
        used_cols: set[str] = set()
        used_fields: set[str] = set()
        out: list[FieldMatch] = []
        for m in sorted(candidates, key=lambda x: -x.score):
            if m.source_column in used_cols or m.canonical_field in used_fields:
                continue
            m.accepted = m.score >= self.ACCEPT_THRESHOLD
            if m.accepted:
                used_cols.add(m.source_column)
                used_fields.add(m.canonical_field)
            out.append(m)
        return out

    def crosswalk(self, matches: Sequence[FieldMatch]) -> dict[str, str]:
        return {m.source_column: m.canonical_field for m in matches if m.accepted}

    def apply(self, record: dict[str, Any], crosswalk: dict[str, str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in record.items():
            target = crosswalk.get(k)
            if target:
                out[target] = v
        return out


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _tokens(s: str) -> list[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(s))
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t and t not in
            {"the", "of", "no", "id", "code", "name"} or t in {"code", "name", "no", "id"}]


def _jensen_shannon(p: dict[str, float], q: dict[str, float]) -> float:
    keys = set(p) | set(q)
    if not keys:
        return 1.0
    total = 0.0
    for k in keys:
        pi, qi = p.get(k, 0.0), q.get(k, 0.0)
        mi = 0.5 * (pi + qi)
        if pi > 0:
            total += 0.5 * pi * math.log2(pi / mi)
        if qi > 0:
            total += 0.5 * qi * math.log2(qi / mi)
    return min(1.0, max(0.0, total))


def _functional_dependencies(records: Sequence[dict[str, Any]], cols: Sequence[str],
                             sample: int = 4000) -> dict[str, set[str]]:
    """Find columns A where A -> B holds on the sample (each A value implies one B)."""
    rows = records[:sample]
    out: dict[str, set[str]] = {c: set() for c in cols}
    for a in cols:
        seen: dict[Any, dict[str, Any]] = {}
        broken: set[str] = set()
        for r in rows:
            av = r.get(a)
            if av in (None, ""):
                continue
            prev = seen.get(av)
            if prev is None:
                seen[av] = {b: r.get(b) for b in cols if b != a}
                continue
            for b in list(prev):
                if b in broken:
                    continue
                if prev[b] != r.get(b):
                    broken.add(b)
        out[a] = {b for b in cols if b != a and b not in broken and len(seen) > 1}
    return out


def describe_crosswalk(matches: Sequence[FieldMatch]) -> str:
    acc = [m for m in matches if m.accepted]
    rev = [m for m in matches if not m.accepted]
    lines = [f"{len(acc)} fields mapped automatically, {len(rev)} need review:"]
    lines += [f"  ✓ {m.explain()}" for m in acc]
    lines += [f"  ? {m.explain()}" for m in rev[:12]]
    return "\n".join(lines)
