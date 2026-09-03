"""Change detection over vector layers, with typed change.

"Change detection" in a land-records context is not a difference image. It is the question:
*what happened on the ground, and what does the registry have to do about it?* A 40 cm
boundary shift and a new three-storey building are both "changes"; only one of them is a
mutation, and only one of them needs a notice served.

This module classifies change into the categories a land administration actually acts on:

============================  ===========================================================
Change type                   Registry action
============================  ===========================================================
``NEW_CONSTRUCTION``          Assess for property tax; check building approval.
``DEMOLITION``                Close assessment; verify no unauthorised reconstruction.
``EXTENSION``                 Re-assess; check set-back and coverage compliance.
``PARTIAL_DEMOLITION``        Re-assess.
``SUBDIVISION``               Mutation; mint child ULPINs; carry the encumbrance forward.
``AMALGAMATION``              Mutation; retire child ULPINs.
``BOUNDARY_ADJUSTMENT``       Correction, not a mutation — no change of title.
``POSITIONAL_ONLY``           Data-quality correction. Must NOT be recorded as a mutation.
``ATTRIBUTE_ONLY``            Update the record of rights only.
``ENCROACHMENT``              Notice under the state land encroachment act.
============================  ===========================================================

The most important distinction in that table is the last-but-two. A re-survey that moves
every boundary in a village by 1.2 m is not two thousand mutations; treating it as such
would be a catastrophe for the registry. The module therefore separates *systematic*
displacement — estimated once, across the whole layer — from *individual* movement, and
only the residual counts as real change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from shapely.geometry.base import BaseGeometry

from ..core.models import Cardinality, ChangeType, MatchPair


@dataclass
class ChangeConfig:
    positional_tolerance_m: float = 0.75
    """Movement below this is data noise, not change."""
    area_change_threshold_pct: float = 8.0
    extension_min_area_m2: float = 6.0
    demolition_area_fraction: float = 0.75
    """Fraction of area that must disappear for full demolition rather than partial."""
    height_change_threshold_m: float = 1.5
    encroachment_min_area_m2: float = 4.0
    systematic_offset_correction: bool = True
    mode: str = "temporal"
    """``"temporal"`` compares two epochs of the same phenomenon, so differences are change
    on the ground. ``"cross_source"`` compares two contemporaneous datasets, where a
    difference is *disagreement between departments*, not a mutation. Running cross-source
    comparison in temporal mode would raise a demolition notice for every building one
    department simply does not hold, which is the single most damaging error this module
    could make."""


@dataclass
class ChangeRecord:
    entity_id: str
    change_type: ChangeType
    confidence: float
    area_before_m2: float = 0.0
    area_after_m2: float = 0.0
    area_delta_m2: float = 0.0
    centroid_shift_m: float = 0.0
    residual_shift_m: float = 0.0
    """Shift after removing the layer-wide systematic offset — the honest number."""
    height_delta_m: float | None = None
    changed_attributes: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    related_ids: list[str] = field(default_factory=list)
    registry_action: str = ""
    evidence: list[str] = field(default_factory=list)
    is_actionable: bool = True

    @property
    def area_delta_pct(self) -> float:
        if self.area_before_m2 <= 0:
            return 100.0 if self.area_after_m2 > 0 else 0.0
        return 100.0 * (self.area_after_m2 - self.area_before_m2) / self.area_before_m2

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "change_type": self.change_type.value,
            "confidence": round(self.confidence, 4),
            "area_before_m2": round(self.area_before_m2, 2),
            "area_after_m2": round(self.area_after_m2, 2),
            "area_delta_m2": round(self.area_delta_m2, 2),
            "area_delta_pct": round(self.area_delta_pct, 2),
            "centroid_shift_m": round(self.centroid_shift_m, 3),
            "residual_shift_m": round(self.residual_shift_m, 3),
            "height_delta_m": self.height_delta_m,
            "changed_attributes": {k: [str(a), str(b)] for k, (a, b) in
                                   self.changed_attributes.items()},
            "related_ids": self.related_ids,
            "registry_action": self.registry_action,
            "evidence": self.evidence,
            "is_actionable": self.is_actionable,
        }


REGISTRY_ACTIONS: dict[ChangeType, str] = {
    ChangeType.NEW_CONSTRUCTION: ("Raise a property tax assessment and verify the building "
                                  "approval / planning permission on record."),
    ChangeType.DEMOLITION: ("Close the assessment and flag the parcel for re-inspection "
                            "before any reconstruction is regularised."),
    ChangeType.EXTENSION: ("Re-assess the property and check the extension against set-back "
                           "and ground-coverage limits in the development control rules."),
    ChangeType.PARTIAL_DEMOLITION: "Re-assess the reduced built-up area.",
    ChangeType.SUBDIVISION: ("Record a mutation, mint child ULPINs, and carry the parent's "
                             "encumbrance and litigation history onto every child."),
    ChangeType.AMALGAMATION: ("Record a mutation, retire the child ULPINs and merge their "
                              "encumbrance histories onto the new parent."),
    ChangeType.BOUNDARY_ADJUSTMENT: ("Correct the boundary in the record. This is a survey "
                                     "correction, not a transfer of title — do not raise a "
                                     "mutation."),
    ChangeType.POSITIONAL_ONLY: ("No registry action. This is a georeferencing improvement "
                                 "affecting the whole layer, not a change on the ground."),
    ChangeType.ATTRIBUTE_ONLY: "Update the record of rights; no survey action required.",
    ChangeType.RECLASSIFICATION: "Refer to the revenue officer for land-use reclassification.",
    ChangeType.ENCROACHMENT: ("Serve notice under the state land encroachment act and refer "
                              "to the tahsildar for eviction proceedings."),
    ChangeType.NO_CHANGE: "None.",
    ChangeType.SOURCE_OMISSION: ("No registry action. The reference dataset holds a feature "
                                 "the candidate dataset does not; this is a completeness "
                                 "gap in the candidate, to be raised with its custodian."),
    ChangeType.SOURCE_COMMISSION: ("No registry action. The candidate dataset holds a "
                                   "feature the reference does not. Verify against imagery "
                                   "before assuming either source is wrong — this is the "
                                   "pool from which genuine unrecorded construction is "
                                   "identified."),
    ChangeType.GEOMETRIC_DISAGREEMENT: ("Two departments hold materially different "
                                        "boundaries for the same feature. Reconcile before "
                                        "either is used for a statutory purpose."),
    ChangeType.ATTRIBUTE_DISAGREEMENT: ("Two departments hold different attribute values "
                                        "for the same feature. Reconcile in the record of "
                                        "rights."),
}

#: How each temporal change type is re-labelled when the comparison is cross-source rather
#: than cross-epoch.
CROSS_SOURCE_EQUIVALENT: dict[ChangeType, ChangeType] = {
    ChangeType.NEW_CONSTRUCTION: ChangeType.SOURCE_COMMISSION,
    ChangeType.DEMOLITION: ChangeType.SOURCE_OMISSION,
    ChangeType.EXTENSION: ChangeType.GEOMETRIC_DISAGREEMENT,
    ChangeType.PARTIAL_DEMOLITION: ChangeType.GEOMETRIC_DISAGREEMENT,
    ChangeType.BOUNDARY_ADJUSTMENT: ChangeType.GEOMETRIC_DISAGREEMENT,
    ChangeType.ATTRIBUTE_ONLY: ChangeType.ATTRIBUTE_DISAGREEMENT,
    ChangeType.POSITIONAL_ONLY: ChangeType.POSITIONAL_ONLY,
    ChangeType.NO_CHANGE: ChangeType.NO_CHANGE,
    ChangeType.SUBDIVISION: ChangeType.SUBDIVISION,
    ChangeType.AMALGAMATION: ChangeType.AMALGAMATION,
    ChangeType.RECLASSIFICATION: ChangeType.ATTRIBUTE_DISAGREEMENT,
}


class ChangeDetector:
    def __init__(self, config: ChangeConfig | None = None) -> None:
        self.config = config or ChangeConfig()
        self.systematic_offset: tuple[float, float] = (0.0, 0.0)

    def detect(self, pairs: Sequence[MatchPair],
               before: dict[str, BaseGeometry], after: dict[str, BaseGeometry],
               *,
               before_attrs: dict[str, dict[str, Any]] | None = None,
               after_attrs: dict[str, dict[str, Any]] | None = None,
               unmatched_before: Sequence[str] = (),
               unmatched_after: Sequence[str] = (),
               heights: dict[str, float] | None = None,
               public_land: dict[str, BaseGeometry] | None = None
               ) -> list[ChangeRecord]:
        cfg = self.config
        before_attrs = before_attrs or {}
        after_attrs = after_attrs or {}
        heights = heights or {}
        out: list[ChangeRecord] = []

        if cfg.systematic_offset_correction:
            self.systematic_offset = self._estimate_systematic(pairs, before, after)

        for p in pairs:
            if not p.accepted:
                continue
            rec = self._classify_pair(p, before, after, before_attrs, after_attrs, heights)
            if rec is not None:
                out.append(rec)

        cross = cfg.mode == "cross_source"

        for fid in unmatched_after:
            g = after.get(fid)
            if g is None:
                continue
            rec = ChangeRecord(
                entity_id=fid,
                change_type=(ChangeType.SOURCE_COMMISSION if cross
                             else ChangeType.NEW_CONSTRUCTION),
                confidence=0.72,
                area_after_m2=float(g.area),
                area_delta_m2=float(g.area),
                evidence=[("held by the candidate dataset and absent from the reference"
                           if cross else
                           "present in the later epoch with no counterpart in the earlier one")],
                is_actionable=not cross,
            )
            if public_land:
                enc = self._encroachment(g, public_land)
                if enc is not None:
                    rec.change_type = ChangeType.ENCROACHMENT
                    rec.confidence = enc[0]
                    rec.evidence.append(enc[1])
            rec.registry_action = REGISTRY_ACTIONS[rec.change_type]
            out.append(rec)

        for fid in unmatched_before:
            g = before.get(fid)
            if g is None:
                continue
            ct = ChangeType.SOURCE_OMISSION if cross else ChangeType.DEMOLITION
            out.append(ChangeRecord(
                entity_id=fid,
                change_type=ct,
                confidence=0.68,
                area_before_m2=float(g.area),
                area_delta_m2=-float(g.area),
                evidence=[("held by the reference dataset and absent from the candidate"
                           if cross else
                           "present in the earlier epoch with no counterpart in the later one")],
                registry_action=REGISTRY_ACTIONS[ct],
                is_actionable=not cross,
            ))
        return out

    # -- one matched pair ----------------------------------------------------------

    def _classify_pair(self, p: MatchPair, before: dict[str, BaseGeometry],
                       after: dict[str, BaseGeometry],
                       before_attrs: dict[str, dict[str, Any]],
                       after_attrs: dict[str, dict[str, Any]],
                       heights: dict[str, float]) -> ChangeRecord | None:
        cfg = self.config
        gb = before.get(p.left_id)
        ga = after.get(p.right_id)
        if gb is None or ga is None:
            return None

        ab, aa = float(gb.area), float(ga.area)
        cb, ca = gb.centroid, ga.centroid
        shift = math.hypot(ca.x - cb.x, ca.y - cb.y)
        rx = (ca.x - cb.x) - self.systematic_offset[0]
        ry = (ca.y - cb.y) - self.systematic_offset[1]
        residual = math.hypot(rx, ry)

        rec = ChangeRecord(
            entity_id=p.left_id,
            change_type=ChangeType.NO_CHANGE,
            confidence=p.probability,
            area_before_m2=ab, area_after_m2=aa, area_delta_m2=aa - ab,
            centroid_shift_m=shift, residual_shift_m=residual,
            related_ids=[p.right_id],
        )

        hb = heights.get(p.left_id)
        ha = heights.get(p.right_id)
        if hb is not None and ha is not None:
            rec.height_delta_m = round(ha - hb, 2)

        changed = self._attribute_delta(before_attrs.get(p.left_id, {}),
                                        after_attrs.get(p.right_id, {}))
        rec.changed_attributes = changed

        # cardinality first: a split is a split whatever the areas did
        if p.cardinality is Cardinality.ONE_TO_MANY:
            rec.change_type = ChangeType.SUBDIVISION
            rec.evidence.append("one earlier parcel maps onto several later parcels whose "
                                "areas sum to it")
        elif p.cardinality is Cardinality.MANY_TO_ONE:
            rec.change_type = ChangeType.AMALGAMATION
            rec.evidence.append("several earlier parcels map onto one later parcel")
        elif p.cardinality is Cardinality.MANY_TO_MANY:
            rec.change_type = ChangeType.BOUNDARY_ADJUSTMENT
            rec.evidence.append("a group of parcels was reorganised without a net area change")
        else:
            delta_pct = abs(rec.area_delta_pct)
            grew = rec.area_delta_m2 > 0
            if (residual <= cfg.positional_tolerance_m
                    and delta_pct < cfg.area_change_threshold_pct
                    and not changed):
                rec.change_type = (ChangeType.POSITIONAL_ONLY
                                   if shift > cfg.positional_tolerance_m
                                   else ChangeType.NO_CHANGE)
                if rec.change_type is ChangeType.POSITIONAL_ONLY:
                    rec.is_actionable = False
                    rec.evidence.append(
                        f"the {shift:.2f} m shift is almost entirely the layer-wide "
                        f"systematic offset; only {residual:.2f} m is specific to this "
                        f"feature, which is within survey noise"
                    )
            elif delta_pct >= cfg.area_change_threshold_pct and grew:
                if abs(rec.area_delta_m2) >= cfg.extension_min_area_m2:
                    rec.change_type = ChangeType.EXTENSION
                    rec.evidence.append(
                        f"footprint grew by {rec.area_delta_m2:.1f} m² "
                        f"({rec.area_delta_pct:+.1f}%)")
            elif delta_pct >= cfg.area_change_threshold_pct and not grew:
                if aa <= ab * (1 - cfg.demolition_area_fraction):
                    rec.change_type = ChangeType.DEMOLITION
                    rec.evidence.append(f"{-rec.area_delta_pct:.0f}% of the footprint is gone")
                else:
                    rec.change_type = ChangeType.PARTIAL_DEMOLITION
                    rec.evidence.append(
                        f"footprint shrank by {-rec.area_delta_m2:.1f} m² "
                        f"({rec.area_delta_pct:+.1f}%)")
            elif changed:
                rec.change_type = ChangeType.ATTRIBUTE_ONLY
                rec.evidence.append(
                    f"geometry is unchanged; {len(changed)} attribute(s) differ: "
                    + ", ".join(sorted(changed)[:5]))
            elif residual > cfg.positional_tolerance_m:
                rec.change_type = ChangeType.BOUNDARY_ADJUSTMENT
                rec.evidence.append(
                    f"boundary moved {residual:.2f} m beyond the layer-wide offset")

        if (rec.height_delta_m is not None
                and abs(rec.height_delta_m) >= cfg.height_change_threshold_m
                and rec.change_type in (ChangeType.NO_CHANGE, ChangeType.POSITIONAL_ONLY,
                                        ChangeType.ATTRIBUTE_ONLY)):
            rec.change_type = (ChangeType.EXTENSION if rec.height_delta_m > 0
                               else ChangeType.PARTIAL_DEMOLITION)
            rec.evidence.append(
                f"structure height changed by {rec.height_delta_m:+.1f} m — vertical "
                f"development that a footprint comparison alone would have missed")

        if cfg.mode == "cross_source":
            rec.change_type = CROSS_SOURCE_EQUIVALENT.get(rec.change_type, rec.change_type)
        rec.registry_action = REGISTRY_ACTIONS.get(rec.change_type, "")
        rec.is_actionable = rec.change_type not in (
            ChangeType.NO_CHANGE, ChangeType.POSITIONAL_ONLY,
            ChangeType.SOURCE_OMISSION, ChangeType.SOURCE_COMMISSION,
        )
        return rec

    # -- helpers -------------------------------------------------------------------

    def _estimate_systematic(self, pairs: Sequence[MatchPair],
                             before: dict[str, BaseGeometry],
                             after: dict[str, BaseGeometry]) -> tuple[float, float]:
        dx: list[float] = []
        dy: list[float] = []
        for p in pairs:
            if not p.accepted or p.cardinality is not Cardinality.ONE_TO_ONE:
                continue
            gb, ga = before.get(p.left_id), after.get(p.right_id)
            if gb is None or ga is None:
                continue
            dx.append(ga.centroid.x - gb.centroid.x)
            dy.append(ga.centroid.y - gb.centroid.y)
        if len(dx) < 20:
            return 0.0, 0.0
        return float(np.median(dx)), float(np.median(dy))

    @staticmethod
    def _attribute_delta(a: dict[str, Any], b: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
        watched = ("land_use", "tenure_type", "owner_name", "construction_type", "floors",
                   "ward", "zone", "survey_number", "patta_number", "building_use")
        out: dict[str, tuple[Any, Any]] = {}
        for k in watched:
            va, vb = a.get(k), b.get(k)
            if va in (None, "") or vb in (None, ""):
                continue
            if str(va).strip().lower() != str(vb).strip().lower():
                out[k] = (va, vb)
        return out

    def _encroachment(self, geom: BaseGeometry,
                      public_land: dict[str, BaseGeometry]) -> tuple[float, str] | None:
        """A new structure standing on land classified as public.

        Reported as a *finding requiring verification*, never as a determination. An
        encroachment is a legal conclusion that only a revenue officer can reach; the
        platform's job is to put the geometric evidence in front of them, with the area
        and the parcel named, so that the enquiry starts from facts.
        """
        from shapely.strtree import STRtree

        ids = list(public_land)
        if not ids:
            return None
        tree = STRtree([public_land[i] for i in ids])
        best_area = 0.0
        best_id = ""
        for j in tree.query(geom):
            pid = ids[int(j)]
            try:
                a = geom.intersection(public_land[pid]).area
            except Exception:  # noqa: BLE001
                continue
            if a > best_area:
                best_area, best_id = a, pid
        if best_area < self.config.encroachment_min_area_m2:
            return None
        frac = best_area / max(geom.area, 1e-9)
        conf = min(0.95, 0.5 + 0.45 * frac)
        return conf, (
            f"{best_area:.1f} m² ({frac * 100:.0f}% of the structure) falls inside parcel "
            f"{best_id}, which the revenue record classifies as public/poramboke land. "
            f"This is a finding for verification by the revenue officer, not a determination."
        )


# --------------------------------------------------------------------------------------


@dataclass
class ChangeSummary:
    total: int = 0
    actionable: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    area_added_m2: float = 0.0
    area_removed_m2: float = 0.0
    systematic_offset_m: float = 0.0
    suppressed_as_positional: int = 0

    def summary(self) -> str:
        types = ", ".join(f"{k} {v:,}" for k, v in
                          sorted(self.by_type.items(), key=lambda kv: -kv[1]))
        return (
            f"{self.total:,} changes detected, {self.actionable:,} actionable "
            f"({self.suppressed_as_positional:,} suppressed as layer-wide re-registration "
            f"rather than real change, after removing a systematic offset of "
            f"{self.systematic_offset_m:.2f} m); {self.area_added_m2:,.0f} m² added, "
            f"{self.area_removed_m2:,.0f} m² removed. Breakdown: {types}"
        )


def summarise(records: Sequence[ChangeRecord],
              systematic_offset: tuple[float, float] = (0.0, 0.0)) -> ChangeSummary:
    s = ChangeSummary(total=len(records))
    s.systematic_offset_m = math.hypot(*systematic_offset)
    for r in records:
        s.by_type[r.change_type.value] = s.by_type.get(r.change_type.value, 0) + 1
        if r.is_actionable:
            s.actionable += 1
        if r.change_type is ChangeType.POSITIONAL_ONLY:
            s.suppressed_as_positional += 1
        if r.area_delta_m2 > 0:
            s.area_added_m2 += r.area_delta_m2
        else:
            s.area_removed_m2 += -r.area_delta_m2
    return s
