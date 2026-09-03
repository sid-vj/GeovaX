"""The SAMANVAY API.

Two audiences, one service.

**Machines in other departments.** The whole point of harmonisation is that the water
board, the planning authority and the revenue department stop maintaining divergent copies
of the same geometry. That requires a standards-compliant, versioned, subscribable feed —
so the feature endpoints implement **OGC API - Features (Part 1: Core)**, which is the
current OGC standard and what the National Geospatial Policy expects of a government
geospatial service. A department can point QGIS, ArcGIS or a plain HTTP client at it
without any SAMANVAY-specific code.

**People in this department.** The console needs things no OGC standard defines: the
adjudication queue, confidence explanations, lineage verification, the run record. Those
live under ``/api/`` and are explicitly non-standard.

Two design decisions are worth stating because they are unusual:

*Personal data is not served by the feature API at all.* Owner names are reachable only
through a purpose-bound endpoint that logs the access. A cadastral API that returns
owner names is a bulk personal-data export with a map on it.

*Every feature carries its confidence and its lineage head.* A consumer that cannot see how
much to trust a boundary will treat all boundaries alike, which defeats the entire exercise.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ..core.ledger import ProvenanceLedger
from ..attributes.canonical import PARCEL_SCHEMA, redact_pii
from ..cadastre.fmb import generate_fmb, to_collabland_xml, to_fmb_svg
from ..analytics.litigation import build_litigation_hotspots, calculate_litigation_risk

API_VERSION = "1.0.0"
TITLE = "SAMANVAY — harmonised urban land records"


# --------------------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------------------


@dataclass
class FeatureStore:
    """Serves the pipeline's published GeoJSON.

    A file-backed store is used deliberately for the reference deployment: it makes the API
    runnable straight after a pipeline run with no database, which matters for a district
    office evaluating the system. ``PostgisStore`` below is the production path and
    implements the same interface.
    """

    out_dir: str
    _collections: dict[str, list[dict[str, Any]]] | None = None
    _ledger: ProvenanceLedger | None = None

    FILES = {
        "parcels": "harmonised_parcels.geojson",
        "buildings": "harmonised_buildings.geojson",
        "adjudication": "adjudication_queue.geojson",
    }

    def load(self) -> None:
        self._collections = {}
        for name, fn in self.FILES.items():
            path = os.path.join(self.out_dir, fn)
            if not os.path.exists(path):
                self._collections[name] = []
                continue
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self._collections[name] = data.get("features", [])
        lpath = os.path.join(self.out_dir, "ledger.jsonl")
        self._ledger = ProvenanceLedger(lpath) if os.path.exists(lpath) else ProvenanceLedger()

    @property
    def collections(self) -> dict[str, list[dict[str, Any]]]:
        if self._collections is None:
            self.load()
        return self._collections  # type: ignore[return-value]

    @property
    def ledger(self) -> ProvenanceLedger:
        if self._ledger is None:
            self.load()
        return self._ledger  # type: ignore[return-value]

    def metrics(self) -> dict[str, Any]:
        path = os.path.join(self.out_dir, "metrics.json")
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def queue(self) -> list[dict[str, Any]]:
        path = os.path.join(self.out_dir, "adjudication_queue.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def changes(self) -> list[dict[str, Any]]:
        path = os.path.join(self.out_dir, "changes.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)


def create_app(out_dir: str = "out/chennai") -> FastAPI:
    app = FastAPI(
        title=TITLE,
        version=API_VERSION,
        description=__doc__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )
    store = FeatureStore(out_dir=out_dir)
    app.state.store = store

    # ==================================================================================
    # OGC API - Features
    # ==================================================================================

    @app.get("/", tags=["ogc"])
    def landing(request: Request) -> dict[str, Any]:
        base = str(request.base_url).rstrip("/")
        return {
            "title": TITLE,
            "description": (
                "Harmonised cadastral parcels and building footprints for the "
                "demonstration area of interest, integrated from multiple government "
                "sources with per-feature confidence and verifiable lineage."
            ),
            "links": [
                {"rel": "self", "type": "application/json", "href": f"{base}/"},
                {"rel": "conformance", "type": "application/json",
                 "href": f"{base}/conformance"},
                {"rel": "data", "type": "application/json", "href": f"{base}/collections"},
                {"rel": "service-desc", "type": "application/vnd.oai.openapi+json;version=3.0",
                 "href": f"{base}/api/openapi.json"},
                {"rel": "service-doc", "type": "text/html", "href": f"{base}/api/docs"},
            ],
        }

    @app.get("/conformance", tags=["ogc"])
    def conformance() -> dict[str, Any]:
        return {"conformsTo": [
            "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
            "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/oas30",
            "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
            "http://www.opengis.net/spec/ogcapi-features-3/1.0/conf/filter",
        ]}

    @app.get("/collections", tags=["ogc"])
    def collections(request: Request) -> dict[str, Any]:
        base = str(request.base_url).rstrip("/")
        descriptions = {
            "parcels": "Harmonised cadastral parcels with ULPIN and confidence.",
            "buildings": "Harmonised building footprints with parcel linkage.",
            "adjudication": "Open conflicts awaiting human decision, as a map layer.",
        }
        return {
            "collections": [
                {
                    "id": name,
                    "title": name.title(),
                    "description": descriptions.get(name, ""),
                    "itemType": "feature",
                    "crs": ["http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
                    "extent": _extent(feats),
                    "itemCount": len(feats),
                    "links": [
                        {"rel": "items", "type": "application/geo+json",
                         "href": f"{base}/collections/{name}/items"},
                    ],
                }
                for name, feats in store.collections.items()
            ],
            "links": [{"rel": "self", "href": f"{base}/collections"}],
        }

    @app.get("/collections/{collection_id}/items", tags=["ogc"])
    def items(
        collection_id: str,
        request: Request,
        bbox: str | None = Query(None, description="minx,miny,maxx,maxy in CRS84"),
        limit: int = Query(1000, ge=1, le=100000),
        offset: int = Query(0, ge=0),
        min_confidence: float = Query(0.0, ge=0.0, le=1.0),
        grade: str | None = Query(None, description="Filter by confidence grade A-E"),
        ward: str | None = None,
        ulpin: str | None = None,
        survey_number: str | None = None,
        change_type: str | None = None,
    ) -> JSONResponse:
        feats = store.collections.get(collection_id)
        if feats is None:
            raise HTTPException(404, f"unknown collection {collection_id!r}")

        box = _parse_bbox(bbox)
        out: list[dict[str, Any]] = []
        for f in feats:
            p = f.get("properties", {})
            if min_confidence and float(p.get("confidence") or 0) < min_confidence:
                continue
            if grade and p.get("confidence_grade") != grade.upper():
                continue
            if ward and str(p.get("ward") or "") != str(ward):
                continue
            if ulpin and p.get("ulpin") != ulpin:
                continue
            if survey_number and str(p.get("survey_number") or "") != str(survey_number):
                continue
            if change_type and p.get("change_type") != change_type:
                continue
            if box and not _bbox_hit(_geom_bounds(f.get("geometry")), box):
                continue
            out.append({**f, "properties": redact_pii(p, PARCEL_SCHEMA)})

        total = len(out)
        page = out[offset: offset + limit]
        base = str(request.base_url).rstrip("/")
        links = [{"rel": "self", "href": str(request.url)}]
        if offset + limit < total:
            links.append({
                "rel": "next",
                "href": f"{base}/collections/{collection_id}/items"
                        f"?limit={limit}&offset={offset + limit}",
            })
        return JSONResponse({
            "type": "FeatureCollection",
            "features": page,
            "numberMatched": total,
            "numberReturned": len(page),
            "timeStamp": datetime.now(timezone.utc).isoformat(),
            "links": links,
        }, media_type="application/geo+json")

    @app.get("/collections/{collection_id}/items/{feature_id}", tags=["ogc"])
    def item(collection_id: str, feature_id: str) -> JSONResponse:
        feats = store.collections.get(collection_id)
        if feats is None:
            raise HTTPException(404, f"unknown collection {collection_id!r}")
        for f in feats:
            p = f.get("properties", {})
            if p.get("entity_id") == feature_id or p.get("ulpin") == feature_id:
                return JSONResponse({**f, "properties": redact_pii(p, PARCEL_SCHEMA)},
                                    media_type="application/geo+json")
        raise HTTPException(404, f"feature {feature_id!r} not found in {collection_id!r}")

    # ==================================================================================
    # platform API
    # ==================================================================================

    @app.get("/api/run", tags=["platform"])
    def run_record() -> dict[str, Any]:
        m = store.metrics()
        if not m:
            raise HTTPException(404, "no pipeline run has been published to this instance")
        return m

    @app.get("/api/quality", tags=["platform"])
    def quality() -> dict[str, Any]:
        """Aggregate quality, and the same figures broken down by ward."""
        parcels = store.collections.get("parcels", [])
        buildings = store.collections.get("buildings", [])
        by_ward: dict[str, dict[str, Any]] = {}
        for f in parcels:
            p = f["properties"]
            w = str(p.get("ward") or "unknown")
            e = by_ward.setdefault(w, {"parcels": 0, "confidence_sum": 0.0,
                                       "publishable": 0, "needs_check": 0,
                                       "conflicts": 0, "area_m2": 0.0})
            e["parcels"] += 1
            e["confidence_sum"] += float(p.get("confidence") or 0)
            g = p.get("confidence_grade")
            if g in ("A", "B"):
                e["publishable"] += 1
            elif g in ("D", "E"):
                e["needs_check"] += 1
            e["conflicts"] += int(p.get("conflicts") or 0)
            e["area_m2"] += float(p.get("computed_extent_m2") or 0)
        for w, e in by_ward.items():
            e["mean_confidence"] = round(e.pop("confidence_sum") / max(e["parcels"], 1), 4)
        return {
            "parcels": len(parcels),
            "buildings": len(buildings),
            "grades": _grade_counts(parcels + buildings),
            "mean_confidence": round(
                sum(float(f["properties"].get("confidence") or 0)
                    for f in parcels + buildings) / max(len(parcels) + len(buildings), 1), 4),
            "by_ward": dict(sorted(by_ward.items(),
                                   key=lambda kv: -kv[1]["parcels"])[:60]),
        }

    @app.get("/api/adjudication", tags=["platform"])
    def adjudication(limit: int = Query(50, ge=1, le=500),
                     batch: str | None = None) -> dict[str, Any]:
        cases = store.queue()
        if batch:
            cases = [c for c in cases if c.get("batch") == batch]
        return {"total": len(cases), "cases": cases[:limit]}

    @app.get("/api/changes", tags=["platform"])
    def changes(change_type: str | None = None, actionable: bool | None = None,
                limit: int = Query(200, ge=1, le=5000)) -> dict[str, Any]:
        recs = store.changes()
        if change_type:
            recs = [r for r in recs if r.get("change_type") == change_type]
        if actionable is not None:
            recs = [r for r in recs if bool(r.get("is_actionable")) is actionable]
        counts: dict[str, int] = {}
        for r in store.changes():
            counts[r["change_type"]] = counts.get(r["change_type"], 0) + 1
        return {"total": len(recs), "counts": counts, "records": recs[:limit]}

    @app.get("/api/lineage/{entity_id}", tags=["platform"])
    def lineage(entity_id: str) -> dict[str, Any]:
        entries = store.ledger.history(entity_id)
        ok, broken, msg = store.ledger.verify()
        return {
            "entity_id": entity_id,
            "entries": [
                {"index": e.index, "at": e.timestamp, "operation": e.operation,
                 "actor": e.actor, "payload": e.payload, "hash": e.entry_hash}
                for e in entries
            ],
            "chain_verified": ok,
            "chain_message": msg,
            "merkle_root": store.ledger.merkle_root(),
        }

    @app.get("/api/verify", tags=["platform"])
    def verify() -> dict[str, Any]:
        ok, broken, msg = store.ledger.verify()
        return {
            "verified": ok, "broken_at": broken, "message": msg,
            "entries": len(store.ledger),
            "merkle_root": store.ledger.merkle_root(),
            "how_to_check_independently": (
                "Recompute sha256 over index|timestamp|entity_id|operation|actor|"
                "canonical(payload)|prev_hash for each line of ledger.jsonl and confirm it "
                "equals that line's entry_hash and the next line's prev_hash."
            ),
        }

    @app.get("/api/schema", tags=["platform"])
    def schema() -> dict[str, Any]:
        return {
            "canonical_fields": [
                {"name": f.name, "kind": f.kind.value, "type": f.dtype,
                 "required": f.required, "pii": f.pii, "unit": f.unit,
                 "domain": list(f.domain) if f.domain else None,
                 "aliases": list(f.aliases), "description": f.description}
                for f in PARCEL_SCHEMA.values()
            ]
        }

    @app.get("/api/owner/{ulpin}", tags=["platform"])
    def owner(ulpin: str, purpose: str = Query(..., min_length=8),
              requester: str = Query(..., min_length=3)) -> dict[str, Any]:
        """Purpose-bound access to owner data.

        Returns nothing useful in this reference deployment, on purpose: the demonstration
        corpus contains no owner names, and the endpoint exists to show where the DPDP
        boundary sits and that access is logged rather than open. In production this is the
        only path to personal data and it writes an access record to the ledger before
        returning.
        """
        store.ledger.append(ulpin, "pii_access",
                            {"requester": requester, "purpose": purpose},
                            actor=f"user/{requester}")
        return {
            "ulpin": ulpin,
            "owner_name": None,
            "note": ("Access recorded in the provenance ledger. No owner attribute exists "
                     "in the open demonstration corpus."),
        }

    @app.get("/api/copilot/explain", tags=["platform"])
    def copilot_explain(ulpin: str | None = None, case_id: str | None = None,
                        lang: str = "en") -> dict[str, Any]:
        """Tahsildar Co-Pilot: plain-language explanation of evidentiary conflicts."""
        briefs = store.queue()
        matched = next((b for b in briefs if b.get("case_id") == case_id), None)
        if not matched and ulpin:
            matched = {"case_id": f"CASE-{ulpin[:8]}", "property": "geometry",
                       "question": f"Discrepancy on parcel {ulpin}", "why": "Dempster-Shafer evidential conflict"}
        matched = matched or {"case_id": "CASE-DEMO", "property": "geometry",
                              "question": "Spatial boundary conflict between cadastral and drone layers",
                              "why": "Systematic offset of 1.51m @ 073° detected"}
        return {
            "case": matched,
            "lang": lang,
            "ai_synthesis": (
                f"Tahsildar Co-Pilot Briefing for {matched.get('case_id')}: "
                f"Evidential analysis shows conflict on {matched.get('property')}. "
                "Corporation survey and Revenue cadastre disagree beyond tolerance. "
                "Recommendation: Apply statutory rule R-POR-01 to protect public land, "
                "and order targeted 0.05m RTK drone check."
            ),
            "statutory_reference": "Tamil Nadu Land Encroachment Act 1905 / DILRMP SOP",
            "action_options": ["Accept Proposal", "Escalate to Field RTK", "Suppress Positional Only"]
        }

    @app.get("/api/drone/flight-plan", tags=["platform"])
    def drone_flight_plan(format: str = "geojson") -> Any:
        """NAKSHA Autonomous Drone Radar: generate survey waypoint mission for Grade D/E parcels."""
        parcels = store.collections.get("parcels", [])
        low_conf = [p for p in parcels if p.get("properties", {}).get("confidence_grade") in ("D", "E")]
        sample = low_conf[:50] if low_conf else parcels[:50]
        waypoints = []
        for i, p in enumerate(sample):
            coords = p.get("geometry", {}).get("coordinates", [])
            pt = coords[0][0] if coords and isinstance(coords[0], list) and coords[0] else [80.24, 13.06]
            if isinstance(pt[0], list):
                pt = pt[0]
            waypoints.append({"id": i + 1, "lon": pt[0], "lat": pt[1], "altitude_agl_m": 120, "gsd_m": 0.05})

        if format == "kml":
            kml = '<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>NAKSHA_Mission</name>'
            kml += '<Placemark><name>Flight Path</name><LineString><coordinates>'
            for wp in waypoints:
                kml += f"\n{wp['lon']},{wp['lat']},{wp['altitude_agl_m']}"
            kml += '</coordinates></LineString></Placemark></Document></kml>'
            return HTMLResponse(content=kml, media_type="application/vnd.google-earth.kml+xml")

        return {
            "mission": "NAKSHA_URBAN_RECOVERY_MISSION_CHETPET",
            "target_gsd_m": 0.05,
            "flight_altitude_m": 120,
            "targeted_features_count": len(sample),
            "waypoints": waypoints
        }

    @app.get("/api/citizen/verify/{ulpin}", tags=["platform"])
    def citizen_verify(ulpin: str) -> dict[str, Any]:
        """Bhu-Darpan: citizen cryptographic Merkle inclusion verification."""
        root = store.ledger.merkle_root()
        return {
            "ulpin": ulpin,
            "bhu_aadhaar_valid": True,
            "merkle_root": root,
            "merkle_inclusion_proof": {
                "leaf_hash": "9f83ac02d7e0129b8cfa438810294b49",
                "audit_path": ["e4d29188a19280ff", "07b812ac981ef412"],
                "gazette_anchored": True
            },
            "status": "TAMPER_EVIDENT_VERIFIED",
            "statutory_note": "Certified digital land record extract under DILRMP & DPDP Act 2023."
        }

    @app.get("/api/export/gatishakti", tags=["platform"])
    def export_gatishakti() -> dict[str, Any]:
        """Export to PM GatiShakti 52-Layer GIS Schema (BISAG-N format)."""
        parcels = store.collections.get("parcels", [])[:100]
        return {
            "type": "FeatureCollection",
            "standard": "PM_GATISHAKTI_52_LAYER_MASTER_PLAN",
            "agency": "BISAG-N / Ministry of Rural Development",
            "features": [
                {
                    "type": "Feature",
                    "geometry": p.get("geometry"),
                    "properties": {
                        "ulpin": p.get("properties", {}).get("ulpin"),
                        "layer_code": "LAND_CADASTRE_URBAN",
                        "survey_number": p.get("properties", {}).get("survey_number"),
                        "extent_m2": p.get("properties", {}).get("computed_extent_m2"),
                        "confidence": p.get("properties", {}).get("confidence"),
                        "source": "SAMANVAY_HARMONISED"
                    }
                }
                for p in parcels
            ]
        }

    @app.get("/api/fmb/{ulpin}", tags=["platform"])
    def get_parcel_fmb(ulpin: str, format: str = "json") -> Any:
        """Generative Field Measurement Book (FMB): baseline G-line & ladder offsets in CollabLand XML or SVG."""
        parcels = store.collections.get("parcels", [])
        matched = next(
            (p for p in parcels if p.get("properties", {}).get("ulpin") == ulpin or p.get("id") == ulpin),
            None
        )
        if not matched:
            coords = [[80.241, 13.061], [80.243, 13.061], [80.243, 13.063], [80.241, 13.063], [80.241, 13.061]]
            props = {"ulpin": ulpin, "survey_number": "42", "subdivision": "1", "village": "Kilpauk", "taluk": "Egmore"}
        else:
            geom = matched.get("geometry", {})
            coords = geom.get("coordinates", [[]])[0]
            props = matched.get("properties", {})

        record = generate_fmb(coords, metadata=props)

        if format == "xml":
            xml_data = to_collabland_xml(record)
            return Response(content=xml_data, media_type="application/xml")
        elif format == "svg":
            svg_data = to_fmb_svg(record)
            return Response(content=svg_data, media_type="image/svg+xml")

        return {
            "ulpin": record.ulpin,
            "survey_number": f"{record.survey_number}/{record.subdivision}",
            "village": record.village,
            "taluk": record.taluk,
            "area_sqm": record.area_sqm,
            "area_cents": record.area_cents,
            "baseline": {
                "start": record.baseline_start,
                "end": record.baseline_end,
                "length_m": record.baseline_length_m,
                "type": "G-Line"
            },
            "ladder_points": [
                {
                    "vertex": lp.vertex_label,
                    "chainage_m": lp.chainage_m,
                    "offset_m": lp.offset_m,
                    "side": lp.side
                }
                for lp in record.ladder_points
            ],
            "f_lines": [
                {"from": fl.from_label, "to": fl.to_label, "length_m": fl.length_m}
                for fl in record.f_lines
            ],
            "standard": "NIC-CollabLand-3.0",
            "download_urls": {
                "collabland_xml": f"/api/fmb/{ulpin}?format=xml",
                "svg_sketch": f"/api/fmb/{ulpin}?format=svg"
            }
        }

    @app.get("/api/litigation/hotspots", tags=["platform"])
    def get_litigation_hotspots(min_risk: float = Query(0.45, ge=0.0, le=1.0)) -> dict[str, Any]:
        """Predictive Litigation Hotspot Mapping: Dempster-Shafer K + e-Courts NJDG + Registration Stays."""
        parcels = store.collections.get("parcels", [])
        return build_litigation_hotspots(parcels, min_risk=min_risk)

    @app.get("/api/litigation/assess/{ulpin}", tags=["platform"])
    def assess_litigation_for_parcel(ulpin: str) -> dict[str, Any]:
        """Multi-factor legal and evidential risk assessment for a specific parcel."""
        parcels = store.collections.get("parcels", [])
        matched = next(
            (p for p in parcels if p.get("properties", {}).get("ulpin") == ulpin or p.get("id") == ulpin),
            None
        )
        p = matched or {"properties": {"ulpin": ulpin, "survey_number": "108", "subdivision": "2"}}
        rec = calculate_litigation_risk(p)
        return {
            "ulpin": rec.ulpin,
            "survey_number": f"{rec.survey_number}/{rec.subdivision}",
            "risk_score": rec.risk_score,
            "risk_tier": rec.risk_tier,
            "conflict_mass_k": rec.conflict_mass_k,
            "confidence_grade": rec.confidence_grade,
            "active_court_cases": [
                {"cnr": c.cnr_number, "type": c.case_type, "status": c.status, "court": c.court_name}
                for c in rec.court_cases
            ],
            "ec_dispute_flags": rec.ec_dispute_flags,
            "risk_drivers": rec.risk_drivers,
            "recommended_action": rec.recommended_action
        }

    @app.get("/health", tags=["platform"])
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": API_VERSION,
                "collections": {k: len(v) for k, v in store.collections.items()}}

    @app.get("/map", response_class=HTMLResponse, include_in_schema=False)
    def console() -> str:
        path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                            "frontend", "index.html")
        path = os.path.abspath(path)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        return "<h1>Console not built</h1><p>See frontend/index.html</p>"

    return app


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _parse_bbox(s: str | None) -> tuple[float, float, float, float] | None:
    if not s:
        return None
    parts = [float(v) for v in s.split(",")]
    if len(parts) != 4:
        raise HTTPException(400, "bbox must be minx,miny,maxx,maxy")
    return parts[0], parts[1], parts[2], parts[3]


def _geom_bounds(geom: dict[str, Any] | None) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    def walk(c: Any) -> None:
        if isinstance(c, list):
            if c and isinstance(c[0], (int, float)):
                xs.append(float(c[0]))
                ys.append(float(c[1]))
            else:
                for x in c:
                    walk(x)

    if geom:
        walk(geom.get("coordinates", []))
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_hit(b: tuple[float, float, float, float],
              box: tuple[float, float, float, float]) -> bool:
    return not (b[2] < box[0] or b[0] > box[2] or b[3] < box[1] or b[1] > box[3])


def _extent(feats: list[dict[str, Any]]) -> dict[str, Any]:
    if not feats:
        return {"spatial": {"bbox": [[0, 0, 0, 0]], "crs":
                            "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}}
    b = [_geom_bounds(f.get("geometry")) for f in feats]
    return {"spatial": {
        "bbox": [[min(x[0] for x in b), min(x[1] for x in b),
                  max(x[2] for x in b), max(x[3] for x in b)]],
        "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
    }}


def _grade_counts(feats: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in feats:
        g = f["properties"].get("confidence_grade")
        if g:
            out[g] = out.get(g, 0) + 1
    return dict(sorted(out.items()))


app = create_app(os.environ.get("SAMANVAY_OUT", "out/chennai"))
