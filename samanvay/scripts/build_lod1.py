#!/usr/bin/env python3
"""Build a LOD1 3-D city model from the harmonised output.

Takes the harmonised building fabric — footprints reconciled across the municipal survey
and the ML extraction — together with the heights measured by the extraction, and extrudes
them into CityGML LOD1 solids encoded as CityJSON 1.1.

The output is not decorative. Floor space index and ground coverage are the two numbers
every Indian development-control regulation turns on, and neither can be computed from a
footprint alone or from a height alone. Producing them is the clearest demonstration of why
harmonisation is worth doing: the corporation knows where the buildings are, the extraction
knows how tall they are, and only the join answers the question the planner is actually
asking.

    python scripts/build_lod1.py --out out/chennai
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from shapely.geometry import shape  # noqa: E402
from shapely.ops import transform as shp_transform  # noqa: E402
from pyproj import Transformer  # noqa: E402

from samanvay.geoai.lod1 import (Lod1Config, assess_parcels, build,  # noqa: E402
                                 write_cityjson)


def load(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("features", [])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="out/chennai")
    ap.add_argument("--metric-crs", default="EPSG:32644")
    args = ap.parse_args()

    buildings = load(os.path.join(args.out, "harmonised_buildings.geojson"))
    parcels = load(os.path.join(args.out, "harmonised_parcels.geojson"))
    if not buildings:
        print(f"no harmonised buildings in {args.out}; run the pipeline first")
        return 1

    tr = Transformer.from_crs("EPSG:4326", args.metric_crs, always_xy=True)
    to_metric = lambda g: shp_transform(lambda x, y, z=None: tr.transform(x, y), g)  # noqa: E731

    feats: dict = {}
    attrs: dict = {}
    heights: dict = {}
    assignment: dict = {}
    for f in buildings:
        p = f["properties"]
        fid = p["entity_id"]
        feats[fid] = to_metric(shape(f["geometry"]))
        attrs[fid] = p
        h = p.get("max_height_m")
        if h not in (None, ""):
            try:
                heights[fid] = float(h)
            except (TypeError, ValueError):
                pass
        if p.get("parcel_ulpin"):
            assignment[fid] = p["parcel_ulpin"]

    lod1, rep = build(feats, heights=heights, attributes=attrs, config=Lod1Config())

    path = os.path.join(args.out, "city_model_lod1.city.json")
    write_cityjson(path, lod1, crs_epsg=int(args.metric_crs.split(":")[1]),
                   title="SAMANVAY LOD1 — Chennai harmonised building fabric")

    parcel_index = {p["properties"].get("ulpin") or p["properties"]["entity_id"]:
                    p["properties"] for p in parcels}
    checks = assess_parcels(parcel_index, lod1, assignment)
    # Aggregate by finding *type*, not by the message text: the message carries the
    # measured value, so keying on it produces one bucket per parcel and tells nobody
    # anything.
    def kind(msg: str) -> str:
        for key in ("ground coverage", "floor space index", "measured height",
                    "built-up area exceeds the plot area"):
            if msg.startswith(key):
                return key
        return "other"

    findings = Counter(kind(m) for _, msgs in checks for m in msgs)

    report = {
        "lod1": {
            "summary": rep.summary(),
            "buildings": rep.buildings,
            "extruded": rep.extruded,
            "no_measured_height": rep.no_height,
            "height_sources": rep.height_sources,
            "mean_height_m": round(rep.mean_height_m, 2),
            "max_height_m": round(rep.max_height_m, 2),
            "mean_storeys": round(rep.mean_floors, 2),
            "floor_histogram": dict(sorted(rep.floor_histogram.items())),
            "total_footprint_m2": round(rep.total_footprint_m2, 1),
            "total_gross_floor_area_m2": round(rep.total_floor_area_m2, 1),
            "notes": rep.notes,
            "output": path,
        },
        "development_control": {
            "parcels_assessed": len(checks),
            "parcels_with_findings": sum(1 for _, m in checks if m),
            "finding_types": dict(findings.most_common()),
            "mean_ground_coverage_pct": round(
                sum(c.ground_coverage_pct for c, _ in checks) / max(len(checks), 1), 2),
            "mean_floor_space_index": round(
                sum(c.floor_space_index for c, _ in checks) / max(len(checks), 1), 3),
            "caveat": ("Limits are the Tamil Nadu CDBR 2019 defaults for an ordinary "
                       "residential plot. They are parameters: every ULB and land-use zone "
                       "has its own, and a finding here means 'verify against the "
                       "sanctioned plan', never 'a violation has occurred'."),
        },
    }

    with open(os.path.join(args.out, "lod1_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
