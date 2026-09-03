"""Vector connectors: GeoJSON, GeoJSON-Lines, Shapefile, GeoPackage, KML, FlatGeobuf,
GeoParquet, and Well-Known-Text CSV.

GeoJSON-Lines gets a hand-written streaming reader rather than going through GeoPandas.
That is not premature optimisation: the real Tamil Nadu cadastral file is 1.07 GB of JSONL
and the Chennai building file is 664 MB. Loading either into a dataframe to answer "how
many parcels fall in ward 89" costs several gigabytes of RAM and about a minute. Streaming
with a bbox pre-filter on the raw coordinate text costs 40 MB and a few seconds, and it is
what makes the whole pipeline runnable on an ordinary district-office machine rather than
only in a data centre.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, Iterator

from shapely.geometry import shape as shp_shape
from shapely.geometry.base import BaseGeometry

from ..core.models import FeatureClass, SourceDataset
from .base import REGISTRY, Connector, DatasetProfile, RawFeature


# --------------------------------------------------------------------------------------
# GeoJSON Lines — the workhorse for the real bulk datasets
# --------------------------------------------------------------------------------------


@REGISTRY.register
class GeoJsonLinesConnector(Connector):
    """One GeoJSON Feature per line. Streamed, with an optional bbox pre-filter."""

    extensions = (".geojsonl", ".geojsonl.gz", ".ndgeojson", ".jsonl")

    def probe(self, path: str, sample: int = 5000) -> DatasetProfile:
        p = self.profile
        p.crs_declared = self.dataset.crs
        nulls: dict[str, int] = {}
        values: dict[str, set[str]] = {}
        types: dict[str, str] = {}
        coords: list[float] = []
        minx = miny = float("inf")
        maxx = maxy = float("-inf")
        seen_wkb: set[bytes] = set()
        vertex_total = 0
        n = 0

        with _open_text(path) as fh:
            for i, line in enumerate(fh):
                if i >= sample:
                    break
                line = line.strip().rstrip(",")
                if not line or line in "[]":
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    p.warnings.append(f"unparseable line at index {i}")
                    continue
                n += 1
                geom = obj.get("geometry")
                if not geom:
                    p.empty_geometry_count += 1
                else:
                    p.geometry_types[geom.get("type", "?")] = (
                        p.geometry_types.get(geom.get("type", "?"), 0) + 1
                    )
                    try:
                        g = shp_shape(geom)
                        if g.is_empty:
                            p.empty_geometry_count += 1
                        if not g.is_valid:
                            p.invalid_geometry_count += 1
                        bx = g.bounds
                        minx, miny = min(minx, bx[0]), min(miny, bx[1])
                        maxx, maxy = max(maxx, bx[2]), max(maxy, bx[3])
                        vertex_total += _count_vertices(g)
                        wkb = g.wkb
                        if wkb in seen_wkb:
                            p.duplicate_geometry_count += 1
                        elif len(seen_wkb) < 200_000:
                            seen_wkb.add(wkb)
                        if len(coords) < 4000:
                            coords.extend(_first_coords(geom))
                    except Exception as exc:  # noqa: BLE001
                        p.warnings.append(f"geometry error at {i}: {exc}")
                for k, v in (obj.get("properties") or {}).items():
                    if v is None or v == "":
                        nulls[k] = nulls.get(k, 0) + 1
                    else:
                        values.setdefault(k, set()).add(str(v)[:64])
                        types.setdefault(k, type(v).__name__)

        p.feature_count = n
        if n:
            p.attribute_null_rate = {k: round(v / n, 4) for k, v in nulls.items()}
            p.attribute_cardinality = {k: len(v) for k, v in values.items()}
            p.attribute_types = types
            p.mean_vertex_count = vertex_total / max(n, 1)
        if minx < float("inf"):
            p.bbox = (minx, miny, maxx, maxy)
            p.crs_inferred = _infer_crs(p.bbox)
        p.coordinate_precision_digits = self.coordinate_precision(coords)
        if p.coordinate_precision_digits and p.implied_precision_m() > 1.0:
            p.warnings.append(
                f"coordinates carry only {p.coordinate_precision_digits} decimal digits "
                f"(~{p.implied_precision_m():.2f} m); this dataset cannot support a "
                f"sub-metre cadastral claim no matter what its metadata says"
            )
        if p.crs_inferred and p.crs_declared and p.crs_inferred != p.crs_declared:
            p.warnings.append(
                f"declared CRS {p.crs_declared} disagrees with the CRS implied by the "
                f"coordinate range ({p.crs_inferred}); ingest will use the inferred value"
            )
        return p

    def read(self, path: str, bbox: tuple[float, float, float, float] | None = None,
             limit: int | None = None, **kwargs: Any) -> Iterator[RawFeature]:
        """Stream features, optionally clipped to ``bbox`` (in the file's own CRS).

        The bbox test is done on the parsed geometry's bounds, but a cheap textual
        pre-screen rejects most non-matching lines before ``json.loads`` runs, which is
        where nearly all the time goes on a gigabyte file.
        """
        prescreen = _bbox_prescreen(bbox)
        emitted = 0
        with _open_text(path) as fh:
            for i, line in enumerate(fh):
                line = line.strip().rstrip(",")
                if not line or line in "[]":
                    continue
                if prescreen and not prescreen(line):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                geom = obj.get("geometry")
                if not geom:
                    continue
                try:
                    g = shp_shape(geom)
                except Exception:  # noqa: BLE001
                    continue
                if g.is_empty:
                    continue
                if bbox and not _bbox_hit(g.bounds, bbox):
                    continue
                props = obj.get("properties") or {}
                fid = str(
                    obj.get("id")
                    or props.get("gcc_gis_id")
                    or props.get("object_id")
                    or props.get("id")
                    or f"{self.dataset.dataset_id}#{i}"
                )
                yield RawFeature(
                    source_feature_id=fid,
                    geometry=g,
                    properties=props,
                    crs=self.dataset.crs,
                    dataset_id=self.dataset.dataset_id,
                )
                emitted += 1
                if limit and emitted >= limit:
                    return


# --------------------------------------------------------------------------------------
# GeoJSON / OGR-backed formats
# --------------------------------------------------------------------------------------


@REGISTRY.register
class GeoJsonConnector(Connector):
    extensions = (".geojson", ".json")

    def probe(self, path: str, sample: int = 20000) -> DatasetProfile:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        feats = data.get("features", []) if isinstance(data, dict) else []
        p = self.profile
        p.feature_count = len(feats)
        p.crs_declared = _geojson_crs(data) or self.dataset.crs
        nulls: dict[str, int] = {}
        values: dict[str, set[str]] = {}
        coords: list[float] = []
        minx = miny = float("inf")
        maxx = maxy = float("-inf")
        vt = 0
        for f in feats[:sample]:
            geom = f.get("geometry")
            if not geom:
                p.empty_geometry_count += 1
                continue
            p.geometry_types[geom["type"]] = p.geometry_types.get(geom["type"], 0) + 1
            g = shp_shape(geom)
            if not g.is_valid:
                p.invalid_geometry_count += 1
            b = g.bounds
            minx, miny = min(minx, b[0]), min(miny, b[1])
            maxx, maxy = max(maxx, b[2]), max(maxy, b[3])
            vt += _count_vertices(g)
            if len(coords) < 4000:
                coords.extend(_first_coords(geom))
            for k, v in (f.get("properties") or {}).items():
                if v in (None, ""):
                    nulls[k] = nulls.get(k, 0) + 1
                else:
                    values.setdefault(k, set()).add(str(v)[:64])
        n = min(len(feats), sample) or 1
        p.attribute_null_rate = {k: round(v / n, 4) for k, v in nulls.items()}
        p.attribute_cardinality = {k: len(v) for k, v in values.items()}
        p.mean_vertex_count = vt / n
        if minx < float("inf"):
            p.bbox = (minx, miny, maxx, maxy)
            p.crs_inferred = _infer_crs(p.bbox)
        p.coordinate_precision_digits = self.coordinate_precision(coords)
        return p

    def read(self, path: str, bbox=None, limit: int | None = None, **kwargs: Any
             ) -> Iterator[RawFeature]:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        feats = data.get("features", []) if isinstance(data, dict) else data
        crs = _geojson_crs(data) or self.dataset.crs
        for i, f in enumerate(feats):
            if limit and i >= limit:
                return
            geom = f.get("geometry")
            if not geom:
                continue
            g = shp_shape(geom)
            if g.is_empty or (bbox and not _bbox_hit(g.bounds, bbox)):
                continue
            props = f.get("properties") or {}
            yield RawFeature(
                source_feature_id=str(f.get("id") or props.get("id") or f"{self.dataset.dataset_id}#{i}"),
                geometry=g,
                properties=props,
                crs=crs,
                dataset_id=self.dataset.dataset_id,
            )


@REGISTRY.register
class OgrConnector(Connector):
    """Shapefile, GeoPackage, KML, FlatGeobuf, GeoParquet via pyogrio/fiona."""

    extensions = (".shp", ".gpkg", ".kml", ".kmz", ".fgb", ".parquet", ".gdb", ".zip")

    def probe(self, path: str, sample: int = 20000) -> DatasetProfile:
        import geopandas as gpd

        gdf = gpd.read_file(path, rows=sample) if not path.endswith(".parquet") else \
            gpd.read_parquet(path)
        p = self.profile
        p.feature_count = len(gdf)
        p.crs_declared = str(gdf.crs) if gdf.crs else self.dataset.crs
        for t, c in gdf.geometry.geom_type.value_counts().items():
            p.geometry_types[str(t)] = int(c)
        p.invalid_geometry_count = int((~gdf.geometry.is_valid).sum())
        p.empty_geometry_count = int(gdf.geometry.is_empty.sum())
        if len(gdf):
            b = gdf.total_bounds
            p.bbox = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
            p.crs_inferred = _infer_crs(p.bbox)
        for col in gdf.columns:
            if col == gdf.geometry.name:
                continue
            s = gdf[col]
            p.attribute_null_rate[col] = round(float(s.isna().mean()), 4)
            p.attribute_cardinality[col] = int(s.nunique(dropna=True))
            p.attribute_types[col] = str(s.dtype)
        return p

    def read(self, path: str, bbox=None, limit: int | None = None, **kwargs: Any
             ) -> Iterator[RawFeature]:
        import geopandas as gpd

        if path.endswith(".parquet"):
            gdf = gpd.read_parquet(path)
            if bbox:
                gdf = gdf.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]
        else:
            gdf = gpd.read_file(path, bbox=bbox, rows=limit)
        crs = str(gdf.crs) if gdf.crs else self.dataset.crs
        for i, row in enumerate(gdf.itertuples(index=False)):
            d = row._asdict()
            geom = d.pop(gdf.geometry.name, None)
            if geom is None or geom.is_empty:
                continue
            yield RawFeature(
                source_feature_id=str(d.get("id") or d.get("OBJECTID") or f"{self.dataset.dataset_id}#{i}"),
                geometry=geom,
                properties={k: v for k, v in d.items()},
                crs=crs,
                dataset_id=self.dataset.dataset_id,
            )


@REGISTRY.register
class WktCsvConnector(Connector):
    """Revenue extracts are very often a CSV with a WKT column bolted on."""

    extensions = (".csv", ".tsv", ".psv")

    def __init__(self, dataset: SourceDataset, feature_class: FeatureClass,
                 geometry_column: str | None = None) -> None:
        super().__init__(dataset, feature_class)
        self.geometry_column = geometry_column

    def _delimiter(self, path: str) -> str:
        return {".tsv": "\t", ".psv": "|"}.get(os.path.splitext(path)[1], ",")

    def _find_geometry_column(self, header: list[str]) -> str | None:
        if self.geometry_column:
            return self.geometry_column
        for cand in ("wkt", "geom", "geometry", "the_geom", "shape", "wkt_geom"):
            for h in header:
                if h.strip().lower() == cand:
                    return h
        return None

    def probe(self, path: str, sample: int = 20000) -> DatasetProfile:
        p = self.profile
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rd = csv.DictReader(fh, delimiter=self._delimiter(path))
            gcol = self._find_geometry_column(rd.fieldnames or [])
            nulls: dict[str, int] = {}
            values: dict[str, set[str]] = {}
            n = 0
            for i, row in enumerate(rd):
                if i >= sample:
                    break
                n += 1
                for k, v in row.items():
                    if v in (None, ""):
                        nulls[k] = nulls.get(k, 0) + 1
                    else:
                        values.setdefault(k, set()).add(v[:64])
            p.feature_count = n
            p.attribute_null_rate = {k: round(v / max(n, 1), 4) for k, v in nulls.items()}
            p.attribute_cardinality = {k: len(v) for k, v in values.items()}
            if gcol is None:
                p.warnings.append(
                    "no geometry column found; this dataset will be ingested as "
                    "attribute-only records to be linked by identity, not by location"
                )
        return p

    def read(self, path: str, bbox=None, limit: int | None = None, **kwargs: Any
             ) -> Iterator[RawFeature]:
        from shapely import wkt as shp_wkt

        with open(path, newline="", encoding="utf-8-sig") as fh:
            rd = csv.DictReader(fh, delimiter=self._delimiter(path))
            gcol = self._find_geometry_column(rd.fieldnames or [])
            for i, row in enumerate(rd):
                if limit and i >= limit:
                    return
                geom: BaseGeometry | None = None
                if gcol and row.get(gcol):
                    try:
                        geom = shp_wkt.loads(row[gcol])
                    except Exception:  # noqa: BLE001
                        geom = None
                if geom is not None and bbox and not _bbox_hit(geom.bounds, bbox):
                    continue
                props = {k: v for k, v in row.items() if k != gcol}
                yield RawFeature(
                    source_feature_id=str(row.get("id") or f"{self.dataset.dataset_id}#{i}"),
                    geometry=geom,
                    properties=props,
                    crs=self.dataset.crs,
                    dataset_id=self.dataset.dataset_id,
                )


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _open_text(path: str):
    if path.endswith(".gz"):
        import gzip
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


def _count_vertices(geom: BaseGeometry) -> int:
    if geom.is_empty:
        return 0
    if geom.geom_type == "Point":
        return 1
    if hasattr(geom, "geoms"):
        return sum(_count_vertices(g) for g in geom.geoms)
    if geom.geom_type == "Polygon":
        return len(geom.exterior.coords) + sum(len(r.coords) for r in geom.interiors)
    return len(geom.coords)


def _first_coords(geom: dict, limit: int = 40) -> list[float]:
    out: list[float] = []

    def walk(c: Any) -> None:
        if len(out) >= limit:
            return
        if isinstance(c, (int, float)):
            out.append(float(c))
        elif isinstance(c, (list, tuple)):
            for x in c:
                walk(x)

    walk(geom.get("coordinates", []))
    return out[:limit]


def _bbox_hit(bounds: tuple[float, float, float, float],
              bbox: tuple[float, float, float, float]) -> bool:
    return not (bounds[2] < bbox[0] or bounds[0] > bbox[2]
                or bounds[3] < bbox[1] or bounds[1] > bbox[3])


def _bbox_prescreen(bbox):
    """A cheap textual filter that rejects lines which cannot intersect the bbox.

    It looks only at the leading integer part of the first coordinate pair in the line.
    Cheap, conservative and never rejects a line that could match.
    """
    if not bbox:
        return None
    lo_x, lo_y, hi_x, hi_y = bbox
    lo_xi, hi_xi = int(lo_x) - 1, int(hi_x) + 1
    lo_yi, hi_yi = int(lo_y) - 1, int(hi_y) + 1
    prefixes = tuple(f"[{v}" for v in range(lo_xi, hi_xi + 1))

    def check(line: str) -> bool:
        idx = line.find('"coordinates"')
        if idx < 0:
            return True
        seg = line[idx: idx + 400]
        # find the first number in the segment
        j = 0
        while j < len(seg) and not (seg[j].isdigit() or seg[j] == "-"):
            j += 1
        k = j
        while k < len(seg) and (seg[k].isdigit() or seg[k] in ".-"):
            k += 1
        try:
            x = float(seg[j:k])
        except ValueError:
            return True
        return lo_xi <= x <= hi_xi

    _ = prefixes, lo_yi, hi_yi
    return check


def _geojson_crs(data: dict) -> str | None:
    crs = data.get("crs") if isinstance(data, dict) else None
    if not crs:
        return None
    name = (crs.get("properties") or {}).get("name")
    if not name:
        return None
    if "EPSG" in name:
        return "EPSG:" + name.split(":")[-1]
    return name


def _infer_crs(bbox: tuple[float, float, float, float]) -> str:
    """Infer the CRS family from the coordinate magnitudes.

    A dataset that claims EPSG:4326 but whose X values are in the hundreds of thousands is
    projected, and reprojecting it as if it were degrees puts it in the Gulf of Guinea.
    Catching this at ingest costs one comparison and saves a re-run of the whole pipeline.
    """
    minx, miny, maxx, maxy = bbox
    if -180 <= minx <= 180 and -90 <= miny <= 90 and -180 <= maxx <= 180 and -90 <= maxy <= 90:
        return "EPSG:4326"
    if 100_000 <= abs(minx) <= 1_000_000 and 0 <= abs(miny) <= 10_000_000:
        return "EPSG:326xx (UTM north — zone undetermined)"
    if abs(minx) > 1_000_000:
        return "EPSG:3857"
    return "unknown"
