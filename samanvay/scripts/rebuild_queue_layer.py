#!/usr/bin/env python3
"""Rebuild `adjudication_queue.geojson` from a completed run's checkpoint.

The publish stage writes the queue as a map layer, because that is how a tahsildar
actually wants to see contested parcels — on the ward map, not in a list. The layer is
keyed by *cluster* id (the identity the resolver worked in) while the published records
are keyed by their content-addressed entity id, so the join needs the confidence-stage
checkpoint, which still carries both.

This script exists so that layer can be regenerated after a run without paying for the
whole DAG again — useful when only the publish logic changed.

    python scripts/rebuild_queue_layer.py --out out/chennai
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="out/chennai")
    args = ap.parse_args()

    ckpt = os.path.join(args.out, ".ckpt", "harmonise.confidence.pkl")
    queue_path = os.path.join(args.out, "adjudication_queue.json")
    if not os.path.exists(ckpt) or not os.path.exists(queue_path):
        print("need both the confidence checkpoint and adjudication_queue.json")
        return 1

    with open(ckpt, "rb") as fh:
        stage = pickle.load(fh)
    # Checkpoints wrap the stage output in {"value": ..., "report": ..., "seconds": ...}.
    stage = stage.get("value", stage)
    by_cluster = {}
    for bucket in ("parcels", "buildings"):
        for rec in stage.get(bucket, {}).values():
            cid = rec.get("cluster_id")
            if cid:
                by_cluster[cid] = rec["geometry"]
    del stage

    with open(queue_path, encoding="utf-8") as fh:
        cases = json.load(fh)

    feats = []
    missing = 0
    for c in cases:
        g = by_cluster.get(c["entity_id"])
        if g is None:
            missing += 1
            continue
        why = c.get("why", "") or ""
        rule = why.split("]")[0].strip("[") if why.startswith("[") else ""
        datasets = "+".join(o["dataset"] for o in c["options"])
        feats.append({
            "type": "Feature",
            "geometry": g.__geo_interface__ if hasattr(g, "__geo_interface__") else g,
            "properties": {
                "case_id": c["case_id"],
                "property": c["property"],
                "priority": c["priority"],
                "severity": c["severity"],
                "uncertainty": c["uncertainty"],
                "state": "pending",
                "batch": f"{c['property']}|{datasets}|{rule}",
                "ward": (c.get("context") or {}).get("ward"),
                "area_m2": (c.get("context") or {}).get("area_m2"),
            },
        })

    dest = os.path.join(args.out, "adjudication_queue.geojson")
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh)
    print(f"{len(feats)} cases written to {dest}"
          + (f" ({missing} without a published geometry)" if missing else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
