#!/usr/bin/env python3
"""Build the self-contained jury demo from a completed run.

The operator console (`frontend/index.html`) talks to the live API. That is the right
architecture and the wrong artefact for an evaluation, where a jury has ten minutes, no
Python environment and no appetite for `docker compose up`.

This script samples a dense sub-window of the real harmonised output — real parcels, real
buildings, real conflicts, real confidence scores, no synthetic features anywhere — and
inlines it into a single HTML file that opens by double-clicking.

    python scripts/build_demo.py --out out/chennai --dest demo/samanvay-demo.html
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

PRECISION = 6  # ~0.11 m at the equator — well below any source's accuracy


def _round(obj):
    if isinstance(obj, float):
        return round(obj, PRECISION)
    if isinstance(obj, list):
        return [_round(x) for x in obj]
    return obj


def _bounds(geom) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []

    def walk(c):
        if isinstance(c, (int, float)):
            return
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
            return
        for sub in c:
            walk(sub)

    walk(geom.get("coordinates", []))
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _inside(geom, box) -> bool:
    b = _bounds(geom)
    if b is None:
        return False
    return b[0] >= box[0] and b[1] >= box[1] and b[2] <= box[2] and b[3] <= box[3]


def _slim(f: dict, keep: tuple[str, ...]) -> dict:
    p = f["properties"]
    return {
        "g": _round(f["geometry"]["coordinates"]),
        "t": 1 if f["geometry"]["type"] == "Polygon" else 2,
        "p": {k: p.get(k) for k in keep if p.get(k) not in (None, "")},
    }


def load_features(path: str, box, keep, cap: int, seed: int = 7) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        feats = json.load(fh)["features"]
    hits = [f for f in feats if _inside(f["geometry"], box)]
    rng = random.Random(seed)
    if len(hits) > cap:
        hits = rng.sample(hits, cap)
    return [_slim(f, keep) for f in hits]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="out/chennai")
    ap.add_argument("--dest", default="demo/samanvay-demo.html")
    ap.add_argument("--template", default="frontend/demo_template.html")
    ap.add_argument("--window", default="80.235,13.030,80.275,13.060",
                    help="minx,miny,maxx,maxy of the demo window in CRS84")
    ap.add_argument("--max-parcels", type=int, default=4000)
    ap.add_argument("--max-buildings", type=int, default=9000)
    args = ap.parse_args()

    box = tuple(float(v) for v in args.window.split(","))

    parcels = load_features(
        os.path.join(args.out, "harmonised_parcels.geojson"), box,
        ("entity_id", "ulpin", "survey_number", "subdivision", "village_name",
         "village_lgd", "taluk_name", "district_name",
         "contributing_datasets", "n_sources", "conflicts", "confidence",
         "confidence_grade", "computed_extent_m2", "recorded_extent_display",
         "building_count", "built_up_area_m2", "ground_coverage_pct", "max_height_m",
         "conf_positional", "conf_source_agreement",
         "conf_topological", "conf_attribute_completeness", "conf_temporal_currency",
         "conf_lineage_integrity"),
        args.max_parcels)

    buildings = load_features(
        os.path.join(args.out, "harmonised_buildings.geojson"), box,
        ("entity_id", "ward", "zone", "street", "locality", "contributing_datasets",
         "n_sources", "conflicts", "confidence", "confidence_grade", "max_height_m",
         "footprint_area_m2", "conf_positional", "conf_source_agreement",
         "conf_topological", "conf_attribute_completeness", "conf_temporal_currency",
         "conf_lineage_integrity"),
        args.max_buildings)

    queue_path = os.path.join(args.out, "adjudication_queue.geojson")
    queue: list[dict] = []
    if os.path.exists(queue_path):
        with open(queue_path, encoding="utf-8") as fh:
            qf = json.load(fh)["features"]
        queue = [_slim(f, ("case_id", "property", "priority", "severity", "uncertainty",
                           "state", "batch", "ward", "area_m2"))
                 for f in qf if _inside(f["geometry"], box)]

    briefs = []
    bpath = os.path.join(args.out, "adjudication_queue.json")
    if os.path.exists(bpath):
        with open(bpath, encoding="utf-8") as fh:
            cases = json.load(fh)
        by_case = {q["p"]["case_id"] for q in queue}
        briefs = [c for c in cases if c["case_id"] in by_case][:120]
        if len(briefs) < 40:
            briefs = cases[:60]

    with open(os.path.join(args.out, "metrics.json"), encoding="utf-8") as fh:
        metrics = json.load(fh)

    lod1 = {}
    lpath = os.path.join(args.out, "lod1_report.json")
    if os.path.exists(lpath):
        with open(lpath, encoding="utf-8") as fh:
            lod1 = json.load(fh)

    # Quality is aggregated over the whole run rather than the demo window, so the tables
    # report the run and not the sample. Parcels and buildings are aggregated on *different*
    # keys, and that is the point rather than an inconvenience: the revenue department works
    # in villages and the corporation works in wards, and neither hierarchy is a refinement
    # of the other. Forcing one onto the other is how a harmonisation platform starts lying.
    def aggregate(features, key_fields, area_field, label):
        groups: dict[str, dict] = {}
        for f in features:
            p = f["properties"]
            k = next((str(p[kf]) for kf in key_fields if p.get(kf) not in (None, "")), "—")
            e = groups.setdefault(k, {"n": 0, "c": 0.0, "cf": 0, "a": 0.0, "d": 0})
            e["n"] += 1
            e["c"] += float(p.get("confidence") or 0)
            e["cf"] += int(p.get("conflicts") or 0)
            e["a"] += float(p.get(area_field) or 0)
            if (p.get("confidence_grade") or "") in ("D", "E"):
                e["d"] += 1
        rows = sorted(
            ({"key": k, "n": e["n"], "mean_confidence": round(e["c"] / e["n"], 4),
              "conflicts": e["cf"], "needs_check": e["d"],
              "area_km2": round(e["a"] / 1e6, 3)}
             for k, e in groups.items() if e["n"] >= 5),
            key=lambda r: -r["n"])[:40]
        return {"label": label, "rows": rows}

    with open(os.path.join(args.out, "harmonised_parcels.geojson"), encoding="utf-8") as fh:
        all_parcels = json.load(fh)["features"]
    with open(os.path.join(args.out, "harmonised_buildings.geojson"), encoding="utf-8") as fh:
        all_buildings = json.load(fh)["features"]
    quality = [
        aggregate(all_parcels, ("village_name", "village_lgd"), "computed_extent_m2",
                  "Revenue villages · parcels"),
        aggregate(all_buildings, ("ward",), "footprint_area_m2",
                  "Municipal wards · buildings"),
    ]
    del all_buildings

    changes: dict = {}
    cpath = os.path.join(args.out, "changes.json")
    if os.path.exists(cpath):
        with open(cpath, encoding="utf-8") as fh:
            recs = json.load(fh)
        for r in recs:
            t = r.get("change_type", "?")
            changes[t] = changes.get(t, 0) + 1

    payload = {
        "window": list(box),
        "parcels": parcels,
        "buildings": buildings,
        "queue": queue,
        "briefs": briefs,
        "metrics": metrics,
        "lod1": lod1,
        "quality": quality,
        "changes": dict(sorted(changes.items(), key=lambda kv: -kv[1])),
    }

    with open(args.template, encoding="utf-8") as fh:
        html = fh.read()
    blob = json.dumps(payload, separators=(",", ":"))
    html = html.replace("/*__SAMANVAY_DATA__*/null", blob)

    os.makedirs(os.path.dirname(args.dest) or ".", exist_ok=True)
    with open(args.dest, "w", encoding="utf-8") as fh:
        fh.write(html)

    size = os.path.getsize(args.dest)
    print(f"{args.dest}: {size/1e6:.2f} MB — {len(parcels)} parcels, "
          f"{len(buildings)} buildings, {len(queue)} queued cases, "
          f"{len(briefs)} case briefs, "
          + " / ".join(f"{len(q['rows'])} {q['label'].split('·')[0].strip().lower()}"
                       for q in quality))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
