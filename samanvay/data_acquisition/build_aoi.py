#!/usr/bin/env python3
"""Clip the national/state-scale real datasets down to the area of interest.

The raw corpus is about 4 GB uncompressed and 8.4 million features. Nothing about the
platform requires that to be loaded at once, and requiring it would make the system
undemonstrable on the hardware a district office actually has. This script streams each
source once, keeps only what falls inside the AOI, and writes a compact working copy plus a
manifest recording exactly what was taken, from where, and with what checksum.

Run:

    python data_acquisition/build_aoi.py --raw data/raw --out data/aoi
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from typing import Any, Iterator

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sources import CATALOGUE, DEFAULT_AOI, AreaOfInterest  # noqa: E402


def _first_point(coords: Any) -> tuple[float, float] | None:
    c = coords
    while isinstance(c, list) and c and isinstance(c[0], list):
        c = c[0]
    if isinstance(c, list) and len(c) >= 2 and isinstance(c[0], (int, float)):
        return float(c[0]), float(c[1])
    return None


def _bounds(coords: Any) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []

    def walk(c: Any) -> None:
        if isinstance(c, list):
            if c and isinstance(c[0], (int, float)) and len(c) >= 2:
                xs.append(float(c[0]))
                ys.append(float(c[1]))
            else:
                for x in c:
                    walk(x)

    walk(coords)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _hits(b: tuple[float, float, float, float],
          box: tuple[float, float, float, float]) -> bool:
    return not (b[2] < box[0] or b[0] > box[2] or b[3] < box[1] or b[1] > box[3])


def clip_geojsonl(path: str, out_path: str, box: tuple[float, float, float, float],
                  *, id_field: str | None = None) -> dict[str, Any]:
    kept = 0
    scanned = 0
    t0 = time.time()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    lo_x, hi_x = int(box[0]) - 1, int(box[2]) + 1
    with open(path, encoding="utf-8") as src, open(out_path, "w", encoding="utf-8") as dst:
        for line in src:
            scanned += 1
            # cheap textual pre-screen: reject lines whose first coordinate's integer part
            # cannot fall in the AOI, without paying for a JSON parse
            i = line.find('"coordinates"')
            if i < 0:
                continue
            seg = line[i + 14: i + 60]
            j = 0
            while j < len(seg) and not (seg[j].isdigit() or seg[j] == "-"):
                j += 1
            k = j
            while k < len(seg) and (seg[k].isdigit() or seg[k] in ".-"):
                k += 1
            try:
                if not (lo_x <= float(seg[j:k]) <= hi_x):
                    continue
            except ValueError:
                pass
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            geom = obj.get("geometry") or {}
            coords = geom.get("coordinates")
            if not coords:
                continue
            b = _bounds(coords)
            if b is None or not _hits(b, box):
                continue
            dst.write(line if line.endswith("\n") else line + "\n")
            kept += 1
    return {
        "input": path,
        "output": out_path,
        "scanned": scanned,
        "kept": kept,
        "seconds": round(time.time() - t0, 2),
        "bytes_out": os.path.getsize(out_path),
    }


def clip_parquet(path: str, out_path: str, box: tuple[float, float, float, float],
                 *, min_confidence: float = 0.0) -> dict[str, Any]:
    """Clip a GeoParquet using the bbox struct column and row-group statistics."""
    import pyarrow.parquet as pq
    from shapely import wkb

    t0 = time.time()
    pf = pq.ParquetFile(path)
    kept = 0
    scanned = 0
    groups_read = 0
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as dst:
        for rg in range(pf.metadata.num_row_groups):
            md = pf.metadata.row_group(rg)
            # row-group pruning on the bbox columns: skip whole groups without reading them
            stats = {}
            for c in range(md.num_columns):
                col = md.column(c)
                name = col.path_in_schema
                if name.startswith("bbox.") and col.is_stats_set:
                    stats[name] = (col.statistics.min, col.statistics.max)
            if stats:
                gx0 = stats.get("bbox.xmin", (None, None))[0]
                gx1 = stats.get("bbox.xmax", (None, None))[1]
                gy0 = stats.get("bbox.ymin", (None, None))[0]
                gy1 = stats.get("bbox.ymax", (None, None))[1]
                if None not in (gx0, gx1, gy0, gy1):
                    if not _hits((gx0, gy0, gx1, gy1), box):
                        continue
            groups_read += 1
            tbl = pf.read_row_group(rg)
            scanned += tbl.num_rows
            bbox = tbl.column("bbox").to_pylist()
            geoms = tbl.column("geometry").to_pylist()
            conf = tbl.column("confidence").to_pylist() if "confidence" in tbl.column_names else [None] * tbl.num_rows
            pres = tbl.column("presence").to_pylist() if "presence" in tbl.column_names else [None] * tbl.num_rows
            hgt = tbl.column("height").to_pylist() if "height" in tbl.column_names else [None] * tbl.num_rows
            for i in range(tbl.num_rows):
                bb = bbox[i]
                if bb is None:
                    continue
                if not _hits((bb["xmin"], bb["ymin"], bb["xmax"], bb["ymax"]), box):
                    continue
                if conf[i] is not None and conf[i] < min_confidence:
                    continue
                g = wkb.loads(geoms[i])
                dst.write(json.dumps({
                    "type": "Feature",
                    "geometry": g.__geo_interface__,
                    "properties": {
                        "gob_id": f"GOB-{rg:04d}-{i:07d}",
                        "confidence": conf[i],
                        "presence": pres[i],
                        "height_m": hgt[i],
                    },
                }, separators=(",", ":")) + "\n")
                kept += 1
    return {
        "input": path,
        "output": out_path,
        "row_groups_total": pf.metadata.num_row_groups,
        "row_groups_read": groups_read,
        "scanned": scanned,
        "kept": kept,
        "seconds": round(time.time() - t0, 2),
        "bytes_out": os.path.getsize(out_path),
    }


def clip_geojson(path: str, out_path: str,
                 box: tuple[float, float, float, float]) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    feats = data.get("features", [])
    keep = []
    for f in feats:
        g = f.get("geometry") or {}
        b = _bounds(g.get("coordinates"))
        if b and _hits(b, box):
            keep.append(f)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": keep}, fh)
    return {"input": path, "output": out_path, "scanned": len(feats), "kept": len(keep),
            "bytes_out": os.path.getsize(out_path)}


def sha256(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


PLAN = [
    ("tngis_cadastre", "TNGIS_TN_Cadastrals.geojsonl", "cadastre_tngis.geojsonl", "geojsonl"),
    ("ncscm_cadastre", "NCSCM_TN_Cadastrals.geojsonl", "cadastre_ncscm.geojsonl", "geojsonl"),
    ("gcc_buildings", "TNGIS_GCC_Chennai_buildings.geojsonl", "buildings_gcc.geojsonl", "geojsonl"),
    ("amrut_buildings", "TN_AMRUT_Buildings.geojsonl", "buildings_amrut.geojsonl", "geojsonl"),
    ("google_open_buildings", "gobi_010001.parquet", "buildings_gob.geojsonl", "parquet"),
    ("gcc_wards", "chennai_wards.geojson", "wards_gcc.geojson", "geojson"),
    ("gcc_zones", "chennai_zones.geojson", "zones_gcc.geojson", "geojson"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="data/aoi")
    ap.add_argument("--bbox", default=None, help="minx,miny,maxx,maxy")
    ap.add_argument("--name", default=DEFAULT_AOI.name)
    args = ap.parse_args()

    aoi = DEFAULT_AOI
    if args.bbox:
        vals = tuple(float(v) for v in args.bbox.split(","))
        aoi = AreaOfInterest(name=args.name, bbox=vals)  # type: ignore[arg-type]

    os.makedirs(args.out, exist_ok=True)
    manifest: dict[str, Any] = {
        "aoi": {**asdict(aoi), "area_km2": round(aoi.area_km2, 3),
                "width_km": round(aoi.width_km, 3), "height_km": round(aoi.height_km, 3)},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "datasets": [],
    }

    for key, raw_name, out_name, kind in PLAN:
        src_path = os.path.join(args.raw, raw_name)
        if not os.path.exists(src_path):
            print(f"  ! missing {src_path}, skipping {key}", flush=True)
            continue
        out_path = os.path.join(args.out, out_name)
        print(f"  clipping {key} ...", flush=True)
        if kind == "geojsonl":
            stats = clip_geojsonl(src_path, out_path, aoi.bbox)
        elif kind == "parquet":
            stats = clip_parquet(src_path, out_path, aoi.bbox)
        else:
            stats = clip_geojson(src_path, out_path, aoi.bbox)
        ds = CATALOGUE[key]
        manifest["datasets"].append({
            "key": key,
            "title": ds.title,
            "authority": f"{ds.authority_name} ({ds.authority_code})",
            "licence": ds.licence,
            "source_type": ds.source_type,
            "feature_class": ds.feature_class,
            "crs": ds.crs,
            "upstream": ds.upstream,
            "vintage": ds.vintage,
            "declared_accuracy_m": ds.accuracy_m,
            "role": ds.role,
            "url": ds.url,
            "sha256_aoi_copy": sha256(out_path),
            **stats,
        })
        print(f"    kept {stats['kept']:,} of {stats['scanned']:,} "
              f"-> {out_name} ({stats['bytes_out'] / 1e6:.1f} MB)", flush=True)

    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    total = sum(d["kept"] for d in manifest["datasets"])
    print(f"\n  AOI corpus: {total:,} real features across "
          f"{len(manifest['datasets'])} datasets over {aoi.area_km2:.1f} km²")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
