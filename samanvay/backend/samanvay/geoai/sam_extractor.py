"""GeoAI Feature Extraction Engine.

**Default path** (always available, no heavy dependencies): classical computer-vision
extraction over a real DSM raster, reusing the same progressive-morphological-filter +
structure-extraction + footprint-regularisation pipeline already implemented and tested
elsewhere in this repo (``raster/terrain.py``, ``geoai/footprints.py``). If no raster
exists at the requested path, or the requested bbox has no ground-truth pixels, this
returns an empty, honestly-labelled result — never a fabricated polygon.

This module previously claimed to run "PyTorch & Segment Anything Model (SAM)" but never
loaded a model or read a raster; it returned a hardcoded rectangle from the bbox centroid
with a fixed confidence (0.942) and height (8.5 m). That mock has been removed entirely.

**Optional path**: real Segment Anything neural segmentation over an RGB orthophoto,
active only when both extras are installed (``pip install -e .[sam]``, adding
``torch``+``segment-anything``) *and* a real checkpoint file is configured via
``SAMANVAY_SAM_CHECKPOINT``. If either is missing, or inference raises, this silently and
correctly falls back to the classical path — it never pretends SAM ran when it didn't, and
the API-facing ``method``/``model`` fields always report which path actually executed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    polygon_geojson: dict[str, Any]
    confidence: float
    area_m2: float
    feature_class: str
    height_m: Optional[float] = None
    method: str = "classical_terrain_cv"
    """Which extraction path actually produced this result: "classical_terrain_cv" (default,
    geometric, no ML) or "segment_anything" (real neural segmentation, opt-in)."""


class SAMFeatureExtractor:
    """GeoAI extraction engine: classical DSM-derived structure extraction by default,
    with a real, opt-in Segment Anything path when the extras and a checkpoint are present.
    """

    def __init__(self, model_type: str = "vit_h", checkpoint_path: Optional[str] = None):
        self.model_type = model_type
        self.checkpoint_path = checkpoint_path or os.environ.get("SAMANVAY_SAM_CHECKPOINT")
        self._device = "cpu"
        self._sam_model = None
        self._predictor = None
        self._sam_available = self._try_init_sam()

    @property
    def sam_active(self) -> bool:
        """True only if a real SAM checkpoint was actually loaded onto a real torch model."""
        return self._sam_available

    def _try_init_sam(self) -> bool:
        """Attempt to load a real SAM model. Returns True only if a real checkpoint was
        actually loaded onto a real torch model — never sets this True speculatively."""
        if not self.checkpoint_path:
            logger.info("No SAMANVAY_SAM_CHECKPOINT configured; using classical DSM extraction.")
            return False
        try:
            import torch
            from segment_anything import SamPredictor, sam_model_registry
        except ImportError:
            logger.info(
                "torch/segment_anything not installed (pip install -e '.[sam]' to enable); "
                "using classical DSM extraction."
            )
            return False
        if not os.path.exists(self.checkpoint_path):
            logger.warning(
                "SAMANVAY_SAM_CHECKPOINT=%s does not exist; using classical DSM extraction.",
                self.checkpoint_path,
            )
            return False
        try:
            self._device = ("cuda" if torch.cuda.is_available()
                             else "mps" if torch.backends.mps.is_available() else "cpu")
            model = sam_model_registry[self.model_type](checkpoint=self.checkpoint_path)
            model.to(self._device)
            self._sam_model = model
            self._predictor = SamPredictor(model)
            logger.info("Loaded real SAM checkpoint (%s) on device %s", self.model_type, self._device)
            return True
        except Exception as err:  # noqa: BLE001
            logger.warning("Failed to load SAM checkpoint (%s); using classical DSM extraction.", err)
            return False

    # -- entry point ----------------------------------------------------------------------

    def extract_from_raster(
        self,
        raster_path: str,
        bbox: Tuple[float, float, float, float],
        prompt_points: Optional[List[Tuple[float, float]]] = None,
        min_area_m2: float = 10.0,
    ) -> List[ExtractionResult]:
        """Extract building footprints for ``bbox`` from a real raster at ``raster_path``.

        Returns an empty list — not a fabricated polygon — when the raster doesn't exist,
        the bbox window is empty, or (classical path) the raster isn't in a projected CRS.
        """
        if not raster_path or not os.path.exists(raster_path):
            logger.info("No raster at %s for bbox %s; nothing to extract.", raster_path, bbox)
            return []

        if self._sam_available:
            try:
                results = self._extract_sam(raster_path, bbox, prompt_points, min_area_m2)
                if results:
                    return results
                logger.info("SAM path produced no masks for %s; falling back to classical path.", bbox)
            except Exception as err:  # noqa: BLE001
                logger.warning("SAM extraction failed (%s); falling back to classical path.", err)

        return self._extract_classical(raster_path, bbox, min_area_m2)

    # -- default: classical DSM structure extraction --------------------------------------

    def _extract_classical(self, raster_path: str, bbox: Tuple[float, float, float, float],
                            min_area_m2: float) -> List[ExtractionResult]:
        import rasterio
        from rasterio.windows import from_bounds
        from shapely.geometry import MultiPolygon, Polygon, mapping

        from ..raster.terrain import dsm_to_dtm, extract_structures, normalised_dsm, polygonise
        from .footprints import rectilinearity, regularise_polygon

        with rasterio.open(raster_path) as src:
            if src.crs is not None and src.crs.is_geographic:
                logger.warning(
                    "%s is in a geographic CRS (%s); terrain filtering assumes a projected, "
                    "metric CRS. Reproject before extraction — skipping rather than "
                    "producing distance measurements in degrees.", raster_path, src.crs,
                )
                return []
            try:
                window = from_bounds(*bbox, transform=src.transform)
            except Exception:  # noqa: BLE001
                return []
            dsm = src.read(1, window=window).astype(np.float32)
            if dsm.size == 0:
                return []
            transform = src.window_transform(window)
            cell_size_m = abs(transform.a) or 0.5
            nodata = src.nodata
            crs_str = str(src.crs) if src.crs else "EPSG:32644"

        if nodata is not None:
            dsm = np.where(dsm == nodata, np.nan, dsm)
        if np.isnan(dsm).all():
            return []

        dtm, _ground, _report = dsm_to_dtm(dsm)
        ndsm = normalised_dsm(dsm, dtm)
        labels, candidates = extract_structures(ndsm, cell_size_m=cell_size_m, min_area_m2=min_area_m2)
        if not candidates:
            return []
        polygons = polygonise(labels, transform, crs=crs_str)

        results: List[ExtractionResult] = []
        for cand in candidates:
            geom = polygons.get(cand.label)
            if geom is None or geom.is_empty:
                continue
            if isinstance(geom, MultiPolygon):
                geom = max(geom.geoms, key=lambda g: g.area)
            if not isinstance(geom, Polygon):
                continue

            squared, outcome = regularise_polygon(geom)
            if squared.is_empty:
                continue

            # Confidence is a real, disclosed heuristic derived from shape quality — not a
            # model probability (this path runs no model) and not a fixed constant: how
            # compact the extracted structure is, and how well it squares to a rectilinear
            # footprint. Both are genuine geometric properties of *this* candidate.
            rect_score = rectilinearity(squared) if outcome == "regularised" else 0.5
            shape_confidence = float(np.clip(
                0.45 * min(1.0, cand.compactness / 0.5) + 0.55 * rect_score, 0.30, 0.97
            ))

            results.append(ExtractionResult(
                polygon_geojson=mapping(squared),
                confidence=round(shape_confidence, 3),
                area_m2=round(float(squared.area), 2),
                feature_class="building_rooftop_classical_cv",
                height_m=cand.mean_height_m,
                method="classical_terrain_cv",
            ))
        return results

    # -- optional: real Segment Anything ---------------------------------------------------

    def _extract_sam(self, raster_path: str, bbox: Tuple[float, float, float, float],
                      prompt_points: Optional[List[Tuple[float, float]]],
                      min_area_m2: float) -> List[ExtractionResult]:
        """Real SAM inference over an RGB orthophoto window. Only reached when a real
        checkpoint was actually loaded in ``_try_init_sam``."""
        import rasterio
        from rasterio.features import shapes as rio_shapes
        from rasterio.windows import from_bounds
        from shapely.geometry import shape as shp_shape

        with rasterio.open(raster_path) as src:
            try:
                window = from_bounds(*bbox, transform=src.transform)
            except Exception:  # noqa: BLE001
                return []
            band_count = min(3, src.count)
            image = src.read(list(range(1, band_count + 1)), window=window)
            if image.size == 0:
                return []
            transform = src.window_transform(window)
            cell_size_m = abs(transform.a) or 0.5
        # SAM expects HWC uint8 RGB
        image = np.moveaxis(image, 0, -1)
        if image.shape[-1] == 1:
            image = np.repeat(image, 3, axis=-1)
        image = np.clip(image, 0, 255).astype(np.uint8)

        self._predictor.set_image(image)

        masks: list[np.ndarray]
        scores: list[float]
        if prompt_points:
            # Convert geographic prompt points into pixel coordinates within this window.
            inv = ~transform
            pts = np.array([inv * (x, y) for x, y in prompt_points])
            labels = np.ones(len(pts), dtype=np.int32)
            mask_batch, score_batch, _ = self._predictor.predict(
                point_coords=pts, point_labels=labels, multimask_output=True,
            )
            best = int(np.argmax(score_batch))
            masks, scores = [mask_batch[best]], [float(score_batch[best])]
        else:
            from segment_anything import SamAutomaticMaskGenerator
            generator = SamAutomaticMaskGenerator(self._sam_model)
            auto_masks = generator.generate(image)
            masks = [m["segmentation"] for m in auto_masks]
            scores = [float(m.get("predicted_iou", m.get("stability_score", 0.5))) for m in auto_masks]

        results: List[ExtractionResult] = []
        for mask, score in zip(masks, scores):
            for geom, value in rio_shapes(mask.astype(np.int32), mask=mask, transform=transform):
                if int(value) != 1:
                    continue
                poly = shp_shape(geom)
                if poly.is_empty or poly.area < min_area_m2:
                    continue
                results.append(ExtractionResult(
                    polygon_geojson=poly.__geo_interface__,
                    confidence=round(float(np.clip(score, 0.0, 1.0)), 3),
                    area_m2=round(float(poly.area), 2),
                    feature_class="building_rooftop_sam",
                    height_m=None,  # SAM has no height signal without a co-registered DSM
                    method="segment_anything",
                ))
        return results

    # -- shared post-processing -------------------------------------------------------------

    def regularize_polygon(self, coordinates: List[List[float]], tolerance: float = 0.5) -> List[List[float]]:
        """Douglas-Peucker simplification for a raw coordinate ring (kept for callers that
        pass coordinates directly rather than a shapely geometry)."""
        try:
            from shapely.geometry import Polygon
            poly = Polygon(coordinates)
            simplified = poly.simplify(tolerance, preserve_topology=True)
            return list(simplified.exterior.coords)
        except Exception:  # noqa: BLE001
            return coordinates
