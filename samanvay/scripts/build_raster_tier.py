#!/usr/bin/env python3
"""Build and evaluate the raster tier on real UAV photogrammetry.

The problem statement lists drone imagery, orthorectified imagery and DSM/DTM as inputs.
This script exercises that whole path on real data:

1. Rebuild georeferenced COGs from the published XYZ pyramids of a real UAV survey,
   reconstructed independently by three photogrammetry engines (OpenDroneMap, Pix4D,
   Agisoft Metashape) from the *same* flight.
2. Recover the elevation surface from the published colour-ramped DSM tiles.
3. Derive a DTM with the progressive morphological ground filter, and the nDSM from the
   difference.
4. Extract structures from the nDSM alone — no imagery, no training data.
5. Co-register the three orthophotos against each other and report the residual.
6. Run change detection between two reconstructions of the same flight, which is a
   controlled null experiment: every region it flags is a false positive by construction,
   so the result is a measured false-positive rate rather than an asserted one.

Usage:
    python scripts/build_raster_tier.py --uav /tmp/uav/data --out out/raster
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np  # noqa: E402

from samanvay.geoai import change as raster_change  # noqa: E402
from samanvay.ingest.tiles import (ColourRampInverter, TilePyramid, ground_resolution,  # noqa: E402
                                   mosaic_to_array, resolution_at, write_geotiff)
from samanvay.raster import coreg  # noqa: E402
from samanvay.raster.terrain import (GroundFilterConfig, dsm_to_dtm, extract_structures,  # noqa: E402
                                     hillshade, normalised_dsm, slope_aspect)

ENGINES = ["odm-3.0.0", "pix4d-4.4.10", "metashape-1.5.2"]


def build(uav_root: str, site: str, engine: str, out_dir: str, zoom: int,
          dsm_zoom: int | None = None) -> dict:
    base = os.path.join(uav_root, engine, site)
    result: dict = {"engine": engine, "site": site, "zoom": zoom}

    ortho_dir = os.path.join(base, "orthophoto", "tiles")
    if os.path.isdir(ortho_dir):
        pyr = TilePyramid(ortho_dir)
        zooms = pyr.zooms()
        z = zoom if zoom in zooms else max(zooms)
        rgb, alpha, bounds = mosaic_to_array(pyr, z, bands=3)
        path = os.path.join(out_dir, f"{site}_{engine}_ori.tif")
        write_geotiff(path, rgb, bounds)
        result["ori"] = {
            "path": path, "zoom": z, "shape": list(rgb.shape),
            "resolution_m_mercator": round(resolution_at(z), 4),
            "ground_resolution_m": round(ground_resolution(z, _lat(bounds)), 4),
            "bounds_3857": [round(b, 2) for b in bounds],
            "valid_fraction": round(float((alpha > 8).mean()), 4),
        }
        result["_ortho"] = (rgb, alpha, bounds, z)

    dsm_dir = os.path.join(base, "dsm", "tiles")
    if os.path.isdir(dsm_dir):
        # The surface model is rebuilt at a coarser zoom than the orthophoto on purpose.
        # The ground filter's largest structuring element spans the biggest building on
        # site — about 45 m — and at 11 cm/px that is a 400-pixel kernel over a 17
        # megapixel array, which costs minutes for no benefit: terrain and building
        # envelopes carry no useful detail below about half a metre.
        pyr = TilePyramid(dsm_dir)
        zooms = pyr.zooms()
        want = dsm_zoom if dsm_zoom is not None else zoom
        z = want if want in zooms else max(zooms)
        rgb, alpha, bounds = mosaic_to_array(pyr, z, bands=3)
        cmap, residual = ColourRampInverter.detect(rgb, alpha)
        inv = ColourRampInverter(cmap)
        field = inv.invert(rgb, alpha)
        trustworthy = residual <= ColourRampInverter.MAX_TRUSTWORTHY_RESIDUAL
        result["dsm"] = {
            "colormap_detected": cmap,
            "colour_fit_residual_rgb": round(residual, 2),
            "recovery_trustworthy": trustworthy,
            "gate": ("the ramp fits the observed pixels to within "
                     f"{residual:.1f} RGB units, below the {ColourRampInverter.MAX_TRUSTWORTHY_RESIDUAL:.0f} "
                     "unit limit, so the recovered surface is usable as relative elevation"
                     if trustworthy else
                     f"the best-fitting known ramp leaves a residual of {residual:.1f} RGB units, "
                     f"above the {ColourRampInverter.MAX_TRUSTWORTHY_RESIDUAL:.0f} unit limit. The "
                     f"publisher used a ramp this platform does not hold, so the recovered "
                     f"values are NOT treated as elevation and the derived DTM/nDSM are "
                     f"reported as illustrative only."),
            "valid_fraction": round(float(np.isfinite(field).mean()), 4),
            "note": ("The published DSM is a colour-ramped image, so the recovered surface "
                     "is faithful in relative height only; the absolute vertical datum is "
                     "not recoverable from it and would come from the survey's own control."),
        }
        result["_dsm"] = (field, bounds, z)
    return result


def terrain(result: dict, out_dir: str, site: str, engine: str) -> dict:
    if "_dsm" not in result:
        return {}
    field, bounds, z = result["_dsm"]
    # The recovered field is normalised 0..1. Scale it to a plausible relief so the ground
    # filter's metric thresholds mean something; the scale is reported, not hidden.
    relief_m = 40.0
    dsm = (field * relief_m).astype(np.float32)
    cell = round(ground_resolution(z, _lat(bounds)), 4)

    cfg = GroundFilterConfig(cell_size_m=cell, max_window_m=45.0,
                             initial_threshold_m=0.25, slope_tolerance=0.25)
    t0 = time.time()
    dtm, ground, rep = dsm_to_dtm(dsm, cfg)
    ndsm = normalised_dsm(dsm, dtm)
    labels, structures = extract_structures(ndsm, cell_size_m=cell,
                                            min_height_m=2.2, min_area_m2=12.0)
    slope, _ = slope_aspect(dtm, cell)
    hs = hillshade(dtm, cell)

    write_geotiff(os.path.join(out_dir, f"{site}_{engine}_dsm.tif"), dsm, bounds,
                  dtype="float32")
    write_geotiff(os.path.join(out_dir, f"{site}_{engine}_dtm.tif"), dtm, bounds,
                  dtype="float32")
    write_geotiff(os.path.join(out_dir, f"{site}_{engine}_ndsm.tif"), ndsm, bounds,
                  dtype="float32")
    write_geotiff(os.path.join(out_dir, f"{site}_{engine}_hillshade.tif"),
                  (hs * 255).astype(np.uint8), bounds, dtype="uint8")

    areas = [s.area_m2 for s in structures]
    return {
        "cell_size_m": cell,
        "assumed_relief_m": relief_m,
        "vertical_scale_caveat": (
            "The published DSM carries no value range, so the recovered field is normalised "
            "0..1 and scaled by an assumed relief. Every height below is therefore correct "
            "in proportion and uncalibrated in absolute terms. In a NAKSHA deployment the "
            "DSM arrives as a float GeoTIFF with a real vertical datum and this scaling step "
            "does not exist."),
        "elevation_trustworthy": bool(result.get("dsm", {}).get("recovery_trustworthy", False)),
        "ground_filter": rep.summary(),
        "ground_filter_notes": rep.notes,
        "seconds": round(time.time() - t0, 1),
        "ndsm": {
            "p50_m": round(float(np.nanpercentile(ndsm, 50)), 3),
            "p95_m": round(float(np.nanpercentile(ndsm, 95)), 3),
            "max_m": round(float(np.nanmax(ndsm)), 3),
        },
        "mean_slope_deg": round(float(np.nanmean(slope)), 2),
        "structures": {
            "count": len(structures),
            "total_area_m2": round(sum(areas), 1) if areas else 0.0,
            "median_area_m2": round(float(np.median(areas)), 1) if areas else 0.0,
            "median_height_m": round(
                float(np.median([s.mean_height_m for s in structures])), 2) if structures else 0.0,
            "median_estimated_floors": int(
                np.median([s.estimated_floors for s in structures])) if structures else 0,
            "method": ("purely geometric — thresholded normalised DSM with morphological "
                       "cleanup. No imagery and no training data are used, which makes this "
                       "the honest baseline any learned extractor must beat."),
        },
    }


def registration_and_null(results: dict[str, dict], out_dir: str) -> dict:
    """Co-register the engines' orthophotos and measure the change detector's noise floor."""
    engines = [e for e in ENGINES if "_ortho" in results.get(e, {})]
    if len(engines) < 2:
        return {"note": "fewer than two reconstructions available"}

    ref_engine = engines[0]
    ref_rgb, ref_alpha, ref_bounds, ref_z = results[ref_engine]["_ortho"]
    px = resolution_at(ref_z)
    out: dict = {"reference": ref_engine, "pixel_size_m": round(px, 4), "pairs": {}}

    for other in engines[1:]:
        rgb, alpha, bounds, z = results[other]["_ortho"]
        a, b = _crop_to_common(ref_rgb, ref_bounds, rgb, bounds)
        if a is None:
            out["pairs"][f"{ref_engine} vs {other}"] = {
                "skipped": "the two reconstructions share too little ground to compare"}
            continue
        res = coreg.coregister(a, b, pixel_size_m=px)
        tiled, detail = coreg.coregister_tiled(a, b, tile=512, pixel_size_m=px)
        out["pairs"][f"{ref_engine} vs {other}"] = {
            "common_ground_shape": list(a.shape[:2]),
            "global": res.summary(),
            "global_notes": res.notes,
            "tiled": tiled.summary(),
            "tiled_notes": tiled.notes,
            "tile_detail": detail,
        }

        # controlled null experiment
        cfg = raster_change.RasterChangeConfig(
            cell_size_m=px, require_agreeing_signals=2, min_region_m2=12.0)
        aligned = coreg.shift_image(b, -res.dy_px, -res.dx_px) if res.reliable else b
        null = raster_change.validate_against_null(a, aligned, config=cfg)
        out["pairs"][f"{ref_engine} vs {other}"]["null_change_experiment"] = null
    return out


def _crop_to_common(a: np.ndarray, a_bounds, b: np.ndarray, b_bounds):
    """Crop two rasters to the ground they actually share.

    Cropping both to the same pixel dimensions from the top-left is wrong whenever the two
    products cover slightly different extents, which independent photogrammetric
    reconstructions of the same flight always do. Doing so offers the correlator two
    different pieces of ground and it correctly reports a nonsensical 260-pixel shift. The
    comparison has to be made on the geographic intersection.
    """
    ix0 = max(a_bounds[0], b_bounds[0])
    iy0 = max(a_bounds[1], b_bounds[1])
    ix1 = min(a_bounds[2], b_bounds[2])
    iy1 = min(a_bounds[3], b_bounds[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return None, None

    def crop(arr, bounds):
        h, w = arr.shape[:2]
        px = (bounds[2] - bounds[0]) / w
        py = (bounds[3] - bounds[1]) / h
        c0 = int(round((ix0 - bounds[0]) / px))
        c1 = int(round((ix1 - bounds[0]) / px))
        r0 = int(round((bounds[3] - iy1) / py))   # north-up: row 0 is maxy
        r1 = int(round((bounds[3] - iy0) / py))
        return arr[max(r0, 0):min(r1, h), max(c0, 0):min(c1, w)]

    ca, cb = crop(a, a_bounds), crop(b, b_bounds)
    h = min(ca.shape[0], cb.shape[0])
    w = min(ca.shape[1], cb.shape[1])
    if h < 64 or w < 64:
        return None, None
    return ca[:h, :w], cb[:h, :w]


def _lat(bounds_3857: tuple[float, float, float, float]) -> float:
    import math
    y = (bounds_3857[1] + bounds_3857[3]) / 2
    return math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--uav", default="/tmp/uav/data")
    ap.add_argument("--site", default="aukerman")
    ap.add_argument("--out", default="out/raster")
    ap.add_argument("--zoom", type=int, default=20)
    ap.add_argument("--dsm-zoom", type=int, default=18)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    report: dict = {"site": args.site, "engines": {}, "zoom_requested": args.zoom,
                    "dsm_zoom_requested": args.dsm_zoom}

    results: dict[str, dict] = {}
    for engine in ENGINES:
        if not os.path.isdir(os.path.join(args.uav, engine, args.site)):
            continue
        print(f"  building {engine} …", flush=True)
        r = build(args.uav, args.site, engine, args.out, args.zoom, args.dsm_zoom)
        results[engine] = r
        entry = {k: v for k, v in r.items() if not k.startswith("_")}
        if engine == ENGINES[0]:
            print("  deriving DTM / nDSM / structures …", flush=True)
            entry["terrain"] = terrain(r, args.out, args.site, engine)
        report["engines"][engine] = entry

    print("  co-registering and running the null change experiment …", flush=True)
    report["registration"] = registration_and_null(results, args.out)

    with open(os.path.join(args.out, "raster_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(json.dumps(report, indent=2, default=str)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
