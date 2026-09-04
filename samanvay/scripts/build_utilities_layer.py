#!/usr/bin/env python3
"""Clip the real Chennai Water Transmission Network (CMWSSB, via OpenCity) to the AOI and
write it as a GeoJSON the API can serve as the "utilities" collection.

This is deliberately not run through the multi-source harmonisation pipeline: there is only
one real utility-network source in the catalogue (chennai_metrowater_transmission), and
matching/confidence fusion need at least two independent sources to mean anything. It is
served as a real, directly-clipped reference layer instead, the same way GCC ward/zone
boundaries are.

Run:
    python scripts/build_utilities_layer.py --raw data/raw --out out/chennai
"""
from __future__ import annotations

import argparse
import json
import os


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="out/chennai")
    ap.add_argument("--bbox", default="80.20,13.03,80.28,13.11")
    args = ap.parse_args()

    import geopandas as gpd
    from shapely.geometry import box

    minx, miny, maxx, maxy = (float(v) for v in args.bbox.split(","))
    src = os.path.join(args.raw, "chennai_water_transmission.kml")
    gdf = gpd.read_file(src, driver="KML")
    aoi = box(minx, miny, maxx, maxy)
    clipped = gdf[gdf.geometry.intersects(aoi)]

    features = []
    for i, row in clipped.iterrows():
        depth = row.get("average_depth_of_pipe_m")
        try:
            depth_m = float(depth) if depth not in (None, "") else None
        except (TypeError, ValueError):
            depth_m = None
        label = row.get("route_name") or row.get("road_name") or f"Main {i}"
        features.append({
            "type": "Feature",
            "geometry": row.geometry.__geo_interface__,
            "properties": {
                "utility_id": f"CMWSSB-{i}",
                "layer_name": str(label),
                "authority": "Chennai Metropolitan Water Supply and Sewerage Board (CMWSSB)",
                "utility_type": "Water Transmission Main",
                "road_name": row.get("road_name") or None,
                "material_of_pipe_code": row.get("material_of_pipe") or None,
                "size_of_pipe_mm": row.get("size_of_pipe_mm") or None,
                "depth_m": depth_m,
                "status": row.get("status") or None,
                "color": "#2f9be0",
            },
        })

    out_path = os.path.join(args.out, "utilities.geojson")
    os.makedirs(args.out, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh)
    print(f"wrote {len(features)} real CMWSSB utility segments -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
