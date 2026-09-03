#!/usr/bin/env python3
"""Calibrated DSM → DTM → nDSM → structures, on a real float surface model.

The UAV corpus publishes its surface models only as colour-ramped tiles, and the platform's
trust gate correctly refuses to treat a recovered ramp as elevation. That is the right
behaviour, but it leaves the DSM/DTM requirement demonstrated only in the negative. This
script closes that gap on a real, calibrated, absolute-height surface model:

    Geobasis NRW DOM1 — 1 km x 1 km at 1 m ground sampling, float32 orthometric heights,
    EPSG:25832 (ETRS89 / UTM 32N), acquired 2020.

It is a genuine airborne DSM of a built-up area with buildings, vegetation and terrain
relief — the same product class NAKSHA delivers from drone photogrammetry, at a coarser
sampling. Every number this script prints is in real metres above the vertical datum.

    python scripts/terrain_demo.py --dsm /tmp/gtd/files/dom1_32_356_5699_1_nw_2020.tif
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np  # noqa: E402
import rasterio  # noqa: E402

from samanvay.geoai.footprints import RegulariseConfig, regularise  # noqa: E402
from samanvay.raster.terrain import (GroundFilterConfig, dsm_to_dtm, extract_structures,  # noqa: E402
                                     hillshade, normalised_dsm, polygonise, slope_aspect)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsm", default="/tmp/gtd/files/dom1_32_356_5699_1_nw_2020.tif")
    ap.add_argument("--out", default="out/terrain")
    ap.add_argument("--max-window-m", type=float, default=60.0)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    with rasterio.open(args.dsm) as src:
        dsm = src.read(1).astype(np.float32)
        if src.nodata is not None:
            dsm = np.where(dsm == src.nodata, np.nan, dsm)
        transform = src.transform
        crs = str(src.crs)
        cell = float(abs(src.res[0]))
        bounds = tuple(src.bounds)

    report: dict = {
        "source": {
            "path": args.dsm,
            "product": "Geobasis NRW DOM1 — airborne digital surface model",
            "authority": "Bezirksregierung Köln, Geobasis NRW",
            "crs": crs,
            "cell_size_m": cell,
            "shape": list(dsm.shape),
            "bounds": [round(b, 1) for b in bounds],
            "elevation_range_m": [round(float(np.nanmin(dsm)), 2),
                                  round(float(np.nanmax(dsm)), 2)],
            "nodata_fraction": round(float(np.isnan(dsm).mean()), 6),
            "note": ("A real float surface model with absolute orthometric heights, so "
                     "every derived quantity below is in true metres — unlike a recovered "
                     "colour ramp, which the platform refuses to treat as elevation."),
        }
    }

    cfg = GroundFilterConfig(
        cell_size_m=cell,
        max_window_m=args.max_window_m,
        initial_window_m=2.0,
        initial_threshold_m=0.30,
        slope_tolerance=0.30,
    )
    t0 = time.time()
    dtm, ground, rep = dsm_to_dtm(dsm, cfg)
    ndsm = normalised_dsm(dsm, dtm)
    seconds = time.time() - t0

    slope, aspect = slope_aspect(dtm, cell)
    hs = hillshade(dtm, cell)

    labels, structures = extract_structures(
        ndsm, cell_size_m=cell, min_height_m=2.5, min_area_m2=25.0)
    polys = polygonise(labels, transform)

    # regularise the extracted outlines: raster-derived polygons are wobbly by construction
    reg, reg_report = regularise({str(k): v for k, v in polys.items()},
                                 RegulariseConfig(simplify_tolerance_m=cell * 0.8,
                                                  min_edge_length_m=cell * 1.5))

    for name, arr, dtype in [("dtm", dtm, "float32"), ("ndsm", ndsm, "float32"),
                             ("slope", slope, "float32"),
                             ("hillshade", (hs * 255).astype(np.uint8), "uint8")]:
        path = os.path.join(args.out, f"nrw_dom1_{name}.tif")
        with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0],
                           width=arr.shape[1], count=1, dtype=dtype, crs=crs,
                           transform=transform, tiled=True, compress="deflate") as dst:
            dst.write(arr.astype(dtype), 1)

    heights = [s.mean_height_m for s in structures]
    areas = [s.area_m2 for s in structures]
    floors = [s.estimated_floors for s in structures]

    report["ground_filter"] = {
        "config": {
            "cell_size_m": cell, "max_window_m": cfg.max_window_m,
            "initial_threshold_m": cfg.initial_threshold_m,
            "slope_tolerance": cfg.slope_tolerance,
        },
        "summary": rep.summary(),
        "passes": rep.passes,
        "windows_m": rep.windows_m,
        "thresholds_m": [round(t, 3) for t in rep.thresholds_m],
        "ground_fraction": round(rep.ground_fraction, 4),
        "seconds": round(seconds, 2),
        "notes": rep.notes,
    }
    report["surfaces"] = {
        "dtm_range_m": [round(float(np.nanmin(dtm)), 2), round(float(np.nanmax(dtm)), 2)],
        "dtm_relief_m": round(float(np.nanmax(dtm) - np.nanmin(dtm)), 2),
        "ndsm_p50_m": round(float(np.nanpercentile(ndsm, 50)), 3),
        "ndsm_p95_m": round(float(np.nanpercentile(ndsm, 95)), 3),
        "ndsm_p99_m": round(float(np.nanpercentile(ndsm, 99)), 3),
        "ndsm_max_m": round(float(np.nanmax(ndsm)), 3),
        "above_ground_area_pct": round(float((ndsm >= 2.5).mean()) * 100, 2),
        "mean_terrain_slope_deg": round(float(np.nanmean(slope)), 2),
    }
    report["structures"] = {
        "count": len(structures),
        "total_footprint_m2": round(float(np.sum(areas)), 1) if areas else 0.0,
        "median_footprint_m2": round(float(np.median(areas)), 1) if areas else 0.0,
        "p90_footprint_m2": round(float(np.percentile(areas, 90)), 1) if areas else 0.0,
        "median_height_m": round(float(np.median(heights)), 2) if heights else 0.0,
        "p90_height_m": round(float(np.percentile(heights, 90)), 2) if heights else 0.0,
        "max_height_m": round(float(np.max(heights)), 2) if heights else 0.0,
        "floor_histogram": {int(k): int(v) for k, v in
                            zip(*np.unique(floors, return_counts=True))} if floors else {},
        "built_up_fraction_of_tile": round(
            float(np.sum(areas)) / (dsm.shape[0] * dsm.shape[1] * cell * cell), 4)
        if areas else 0.0,
        "regularisation": reg_report.summary(),
        "method": ("Structures are segmented from the normalised DSM alone: no imagery, no "
                   "labels, no model. Height above ground of 2.5 m and a 25 m² floor are "
                   "the only domain assumptions."),
    }

    with open(os.path.join(args.out, "structures.geojson"), "w", encoding="utf-8") as fh:
        json.dump({
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": crs}},
            "features": [
                {"type": "Feature",
                 "geometry": reg[str(s.label)].__geo_interface__ if str(s.label) in reg else None,
                 "properties": {
                     "label": s.label, "area_m2": s.area_m2,
                     "mean_height_m": s.mean_height_m, "max_height_m": s.max_height_m,
                     "estimated_floors": s.estimated_floors,
                     "compactness": s.compactness,
                 }}
                for s in structures if str(s.label) in reg
            ],
        }, fh)

    with open(os.path.join(args.out, "terrain_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
