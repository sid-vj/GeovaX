"""Real PostGIS-backed data store.

``FeatureStore`` (api/app.py) has always been documented as "the production path is
``PostgisStore``" — that class never existed, and the schema in ``schema.sql`` was never
queried by anything. This is the real implementation, used automatically by
``api.app.create_app()`` when ``DATABASE_URL`` resolves to a reachable Postgres/PostGIS
instance, and falling back to the flat-file ``FeatureStore`` otherwise — the same
graceful-degradation pattern already used for Redis/Kafka/OpenSearch in ``api/services.py``.

Scope, stated plainly: this covers the tables the API layer actually reads —
``harmonised_parcel``, ``harmonised_building``, ``adjudication_case``, the
``provenance_ledger`` mirror, and ``pipeline_run``. The raw-claims/match-pair provenance
tables already defined in ``schema.sql`` (``source_feature``, ``source_claim``,
``match_pair``, ``conflict``, ``resolution``, ``change_record``, ``parcel_genealogy``) are
not written by this pass — that is a disclosed scope boundary, not an oversight: those
tables would need a second, larger pass wiring every pipeline stage's intermediate state to
SQL, whereas what makes the platform's persistence, adjudication queue and audit trail
"genuinely durable instead of flat-file" is exactly the five tables above.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

from ..core.ledger import ProvenanceLedger

logger = logging.getLogger(__name__)

PARCEL_TYPED_COLUMNS = (
    "survey_number", "subdivision", "patta_number", "state_lgd", "district_lgd",
    "taluk_lgd", "village_lgd", "village_name", "ulb_code", "ward", "zone", "locality",
    "street", "tenure_type", "land_use", "is_public_land", "recorded_extent_m2",
    "computed_extent_m2", "building_count", "built_up_area_m2", "ground_coverage_pct",
    "max_height_m", "confidence", "confidence_grade", "conf_positional",
    "conf_source_agreement", "conf_topological", "conf_attribute", "conf_temporal",
    "conf_lineage", "conflict_count", "adjudication_state", "change_type",
)

BUILDING_TYPED_COLUMNS = (
    "door_number", "ward", "zone", "locality", "street", "building_use",
    "construction_type", "floors", "footprint_area_m2", "max_height_m",
    "estimated_floors", "extraction_confidence", "confidence", "confidence_grade",
    "change_type",
)

_NON_ATTRIBUTE_KEYS = ("entity_id", "ulpin", "kind", "geometry", "ledger_head",
                       "confidence_explanation", "owner_name", "owner_name_normalised")


def get_engine():
    """A SQLAlchemy engine for ``DATABASE_URL`` if it's set and actually reachable, else
    ``None``. Never raises — this is a reachability probe, not a hard requirement."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    try:
        from sqlalchemy import create_engine, text
        # schema.sql creates everything in the `samanvay` schema and only sets search_path
        # for its own init session; every other connection needs it set explicitly or
        # `harmonised_parcel` etc. resolve against the default (empty) search_path and 404.
        engine = create_engine(
            url, pool_pre_ping=True,
            connect_args={"connect_timeout": 3, "options": "-c search_path=samanvay,public"},
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as err:  # noqa: BLE001
        logger.info(
            "DATABASE_URL is set but Postgres is not reachable (%s); falling back to the "
            "file-backed FeatureStore.", err,
        )
        return None


def _split_attributes(attrs: dict[str, Any], typed_columns: tuple[str, ...]) -> tuple[dict, dict]:
    typed = {k: attrs[k] for k in typed_columns if k in attrs and attrs[k] is not None}
    extra = {k: v for k, v in attrs.items()
             if k not in typed_columns and k not in _NON_ATTRIBUTE_KEYS}
    return typed, extra


# --------------------------------------------------------------------------------------
# write side — called from pipeline/harmonise.py's publish stage
# --------------------------------------------------------------------------------------


def publish_to_postgis(engine, *, run_id: str, aoi_name: str, bbox: tuple[float, float, float, float],
                        parcels: dict[str, dict[str, Any]], buildings: dict[str, dict[str, Any]],
                        queue, changes_list: list[dict[str, Any]], ledger: ProvenanceLedger,
                        metrics: dict[str, Any]) -> dict[str, int]:
    """Upsert one harmonisation run's output into Postgres. Best-effort: the file output in
    ``stage_publish`` is written unconditionally and remains authoritative; this is called
    afterwards and any failure here is caught and logged by the caller, never crashing a
    pipeline run that already succeeded at producing its file output."""
    from sqlalchemy import text

    counts = {"parcels": 0, "buildings": 0, "adjudication_cases": 0, "ledger_entries": 0}
    with engine.begin() as conn:
        for entity_id, rec in parcels.items():
            _upsert_parcel(conn, text, entity_id, rec, ledger.head)
            counts["parcels"] += 1
        for entity_id, rec in buildings.items():
            _upsert_building(conn, text, entity_id, rec, ledger.head)
            counts["buildings"] += 1
        for case in queue.cases.values():
            _upsert_adjudication_case(conn, text, case)
            counts["adjudication_cases"] += 1
        counts["ledger_entries"] = _mirror_ledger(conn, text, ledger)
        _write_pipeline_run(conn, text, run_id=run_id, aoi_name=aoi_name, bbox=bbox,
                             metrics=metrics, queue=queue, changes_list=changes_list,
                             ledger=ledger)
    return counts


def _upsert_parcel(conn, text, entity_id: str, rec: dict[str, Any], ledger_head: str) -> None:
    attrs = dict(rec.get("attributes", {}))
    conf = rec.get("confidence")
    typed, extra = _split_attributes(attrs, PARCEL_TYPED_COLUMNS)
    if conf is not None:
        typed["confidence"] = round(conf.composite, 4)
        typed["confidence_grade"] = conf.grade
        for k, v in conf.components().items():
            typed[f"conf_{k}"] = round(v, 4)
    typed["conflict_count"] = len(rec.get("conflicts", []))

    owner_name = attrs.get("owner_name")
    owner_hash = hashlib.sha256(str(owner_name).encode("utf-8")).hexdigest() if owner_name else None

    geom = rec.get("geometry")
    wkt = geom.wkt if geom is not None else None

    conn.execute(text("""
        INSERT INTO harmonised_parcel (
            entity_id, ulpin, geom, geom_metric,
            survey_number, subdivision, patta_number,
            state_lgd, district_lgd, taluk_lgd, village_lgd, village_name,
            ulb_code, ward, zone, locality, street,
            tenure_type, owner_name_hash, land_use, is_public_land,
            recorded_extent_m2, computed_extent_m2,
            building_count, built_up_area_m2, ground_coverage_pct, max_height_m,
            confidence, confidence_grade,
            conf_positional, conf_source_agreement, conf_topological,
            conf_attribute, conf_temporal, conf_lineage,
            contributing_datasets, conflict_count, adjudication_state, change_type,
            extra_attributes, ledger_head
        ) VALUES (
            :entity_id, :ulpin, ST_Multi(ST_GeomFromText(:wkt, 4326)),
            ST_Multi(ST_Transform(ST_GeomFromText(:wkt, 4326), 32644)),
            :survey_number, :subdivision, :patta_number,
            :state_lgd, :district_lgd, :taluk_lgd, :village_lgd, :village_name,
            :ulb_code, :ward, :zone, :locality, :street,
            :tenure_type, :owner_name_hash, :land_use, :is_public_land,
            :recorded_extent_m2, :computed_extent_m2,
            :building_count, :built_up_area_m2, :ground_coverage_pct, :max_height_m,
            :confidence, :confidence_grade,
            :conf_positional, :conf_source_agreement, :conf_topological,
            :conf_attribute, :conf_temporal, :conf_lineage,
            :contributing_datasets, :conflict_count, :adjudication_state, :change_type,
            CAST(:extra_attributes AS jsonb), :ledger_head
        )
        ON CONFLICT (entity_id) DO UPDATE SET
            ulpin = EXCLUDED.ulpin, geom = EXCLUDED.geom, geom_metric = EXCLUDED.geom_metric,
            survey_number = EXCLUDED.survey_number, subdivision = EXCLUDED.subdivision,
            patta_number = EXCLUDED.patta_number, state_lgd = EXCLUDED.state_lgd,
            district_lgd = EXCLUDED.district_lgd, taluk_lgd = EXCLUDED.taluk_lgd,
            village_lgd = EXCLUDED.village_lgd, village_name = EXCLUDED.village_name,
            ulb_code = EXCLUDED.ulb_code, ward = EXCLUDED.ward, zone = EXCLUDED.zone,
            locality = EXCLUDED.locality, street = EXCLUDED.street,
            tenure_type = EXCLUDED.tenure_type, owner_name_hash = EXCLUDED.owner_name_hash,
            land_use = EXCLUDED.land_use, is_public_land = EXCLUDED.is_public_land,
            recorded_extent_m2 = EXCLUDED.recorded_extent_m2,
            computed_extent_m2 = EXCLUDED.computed_extent_m2,
            building_count = EXCLUDED.building_count,
            built_up_area_m2 = EXCLUDED.built_up_area_m2,
            ground_coverage_pct = EXCLUDED.ground_coverage_pct,
            max_height_m = EXCLUDED.max_height_m,
            confidence = EXCLUDED.confidence, confidence_grade = EXCLUDED.confidence_grade,
            conf_positional = EXCLUDED.conf_positional,
            conf_source_agreement = EXCLUDED.conf_source_agreement,
            conf_topological = EXCLUDED.conf_topological,
            conf_attribute = EXCLUDED.conf_attribute, conf_temporal = EXCLUDED.conf_temporal,
            conf_lineage = EXCLUDED.conf_lineage,
            contributing_datasets = EXCLUDED.contributing_datasets,
            conflict_count = EXCLUDED.conflict_count,
            adjudication_state = EXCLUDED.adjudication_state,
            change_type = EXCLUDED.change_type,
            extra_attributes = EXCLUDED.extra_attributes,
            ledger_head = EXCLUDED.ledger_head
    """), {
        "entity_id": entity_id, "ulpin": rec.get("ulpin"), "wkt": wkt,
        "survey_number": typed.get("survey_number"), "subdivision": typed.get("subdivision"),
        "patta_number": typed.get("patta_number"), "state_lgd": typed.get("state_lgd"),
        "district_lgd": typed.get("district_lgd"), "taluk_lgd": typed.get("taluk_lgd"),
        "village_lgd": typed.get("village_lgd"), "village_name": typed.get("village_name"),
        "ulb_code": typed.get("ulb_code"), "ward": typed.get("ward"), "zone": typed.get("zone"),
        "locality": typed.get("locality"), "street": typed.get("street"),
        "tenure_type": typed.get("tenure_type"), "owner_name_hash": owner_hash,
        "land_use": typed.get("land_use"),
        "is_public_land": bool(typed.get("is_public_land", False)),
        "recorded_extent_m2": typed.get("recorded_extent_m2"),
        "computed_extent_m2": typed.get("computed_extent_m2") or 0.0,
        "building_count": typed.get("building_count") or 0,
        "built_up_area_m2": typed.get("built_up_area_m2") or 0.0,
        "ground_coverage_pct": typed.get("ground_coverage_pct"),
        "max_height_m": typed.get("max_height_m"),
        "confidence": typed.get("confidence") or 0.0,
        "confidence_grade": typed.get("confidence_grade") or "E",
        "conf_positional": typed.get("conf_positional"),
        "conf_source_agreement": typed.get("conf_source_agreement"),
        "conf_topological": typed.get("conf_topological"),
        "conf_attribute": typed.get("conf_attribute"),
        "conf_temporal": typed.get("conf_temporal"),
        "conf_lineage": typed.get("conf_lineage"),
        "contributing_datasets": list(rec.get("contributing_datasets") or []),
        "conflict_count": typed.get("conflict_count") or 0,
        "adjudication_state": typed.get("adjudication_state") or "auto_resolved",
        "change_type": typed.get("change_type") or "no_change",
        "extra_attributes": json.dumps(extra, default=str),
        "ledger_head": ledger_head,
    })


def _upsert_building(conn, text, entity_id: str, rec: dict[str, Any], ledger_head: str) -> None:
    attrs = dict(rec.get("attributes", {}))
    conf = rec.get("confidence")
    typed, extra = _split_attributes(attrs, BUILDING_TYPED_COLUMNS)
    if conf is not None:
        typed["confidence"] = round(conf.composite, 4)
        typed["confidence_grade"] = conf.grade

    geom = rec.get("geometry")
    wkt = geom.wkt if geom is not None else None
    if wkt is None:
        return

    conn.execute(text("""
        INSERT INTO harmonised_building (
            entity_id, parcel_ulpin, geom, geom_metric,
            door_number, ward, zone, locality, street,
            building_use, construction_type, floors,
            footprint_area_m2, max_height_m, estimated_floors, extraction_confidence,
            confidence, confidence_grade, contributing_datasets, change_type,
            extra_attributes, ledger_head
        ) VALUES (
            :entity_id, :parcel_ulpin, ST_Multi(ST_GeomFromText(:wkt, 4326)),
            ST_Multi(ST_Transform(ST_GeomFromText(:wkt, 4326), 32644)),
            :door_number, :ward, :zone, :locality, :street,
            :building_use, :construction_type, :floors,
            :footprint_area_m2, :max_height_m, :estimated_floors, :extraction_confidence,
            :confidence, :confidence_grade, :contributing_datasets, :change_type,
            CAST(:extra_attributes AS jsonb), :ledger_head
        )
        ON CONFLICT (entity_id) DO UPDATE SET
            parcel_ulpin = EXCLUDED.parcel_ulpin, geom = EXCLUDED.geom,
            geom_metric = EXCLUDED.geom_metric, door_number = EXCLUDED.door_number,
            ward = EXCLUDED.ward, zone = EXCLUDED.zone, locality = EXCLUDED.locality,
            street = EXCLUDED.street, building_use = EXCLUDED.building_use,
            construction_type = EXCLUDED.construction_type, floors = EXCLUDED.floors,
            footprint_area_m2 = EXCLUDED.footprint_area_m2,
            max_height_m = EXCLUDED.max_height_m,
            estimated_floors = EXCLUDED.estimated_floors,
            extraction_confidence = EXCLUDED.extraction_confidence,
            confidence = EXCLUDED.confidence, confidence_grade = EXCLUDED.confidence_grade,
            contributing_datasets = EXCLUDED.contributing_datasets,
            change_type = EXCLUDED.change_type, extra_attributes = EXCLUDED.extra_attributes,
            ledger_head = EXCLUDED.ledger_head
    """), {
        "entity_id": entity_id, "parcel_ulpin": rec.get("parcel_ulpin") or attrs.get("parcel_ulpin"),
        "wkt": wkt, "door_number": typed.get("door_number"), "ward": typed.get("ward"),
        "zone": typed.get("zone"), "locality": typed.get("locality"), "street": typed.get("street"),
        "building_use": typed.get("building_use"), "construction_type": typed.get("construction_type"),
        "floors": typed.get("floors"), "footprint_area_m2": typed.get("footprint_area_m2") or 0.0,
        "max_height_m": typed.get("max_height_m"), "estimated_floors": typed.get("estimated_floors"),
        "extraction_confidence": typed.get("extraction_confidence"),
        "confidence": typed.get("confidence") or 0.0,
        "confidence_grade": typed.get("confidence_grade") or "E",
        "contributing_datasets": list(rec.get("contributing_datasets") or []),
        "change_type": typed.get("change_type") or "no_change",
        "extra_attributes": json.dumps(extra, default=str),
        "ledger_head": ledger_head,
    })


def _upsert_adjudication_case(conn, text, case) -> None:
    conn.execute(text("""
        INSERT INTO adjudication_case (
            case_id, entity_id, property_path, priority, batch_key, state,
            decided_value, decided_by, decided_at, decision_note
        ) VALUES (
            :case_id, :entity_id, :property_path, :priority, :batch_key, :state,
            :decided_value, :decided_by, :decided_at, :decision_note
        )
        ON CONFLICT (case_id) DO UPDATE SET
            state = EXCLUDED.state, decided_value = EXCLUDED.decided_value,
            decided_by = EXCLUDED.decided_by, decided_at = EXCLUDED.decided_at,
            decision_note = EXCLUDED.decision_note, priority = EXCLUDED.priority
    """), {
        "case_id": case.case_id, "entity_id": case.entity_id,
        "property_path": case.property_path, "priority": getattr(case, "priority", 0.0),
        "batch_key": getattr(case, "batch_key", "") or case.case_id,
        "state": getattr(case, "state", "queued"),
        "decided_value": getattr(case, "decided_value", None),
        "decided_by": getattr(case, "decided_by", None),
        "decided_at": getattr(case, "decided_at", None),
        "decision_note": getattr(case, "decision_note", None),
    })


def _mirror_ledger(conn, text, ledger: ProvenanceLedger) -> int:
    """Append any ledger entries not already mirrored into Postgres. Idempotent via
    entry_hash uniqueness; the JSONL file remains the authoritative copy per core/ledger.py's
    own design — this is a queryable mirror, not a replacement."""
    n = 0
    for e in ledger:
        conn.execute(text("""
            INSERT INTO provenance_ledger (idx, ts, entity_id, operation, actor, payload,
                                           prev_hash, entry_hash)
            VALUES (:idx, :ts, :entity_id, :operation, :actor, CAST(:payload AS jsonb),
                    :prev_hash, :entry_hash)
            ON CONFLICT (entry_hash) DO NOTHING
        """), {
            "idx": e.index, "ts": e.timestamp, "entity_id": e.entity_id,
            "operation": e.operation, "actor": e.actor,
            "payload": json.dumps(e.payload, default=str),
            "prev_hash": e.prev_hash, "entry_hash": e.entry_hash,
        })
        n += 1
    return n


def _write_pipeline_run(conn, text, *, run_id: str, aoi_name: str,
                         bbox: tuple[float, float, float, float], metrics: dict[str, Any],
                         queue, changes_list: list[dict[str, Any]], ledger: ProvenanceLedger) -> None:
    minx, miny, maxx, maxy = bbox
    poly_wkt = (f"POLYGON(({minx} {miny}, {maxx} {miny}, {maxx} {maxy}, "
               f"{minx} {maxy}, {minx} {miny}))")
    briefs = [c.brief() for c in sorted(queue.cases.values(), key=lambda x: -x.priority)[:500]]
    conn.execute(text("""
        INSERT INTO pipeline_run (run_id, aoi_name, aoi, finished_at, status,
                                  stage_reports, metrics, queue_briefs, changes,
                                  ledger_root, software_version)
        VALUES (:run_id, :aoi_name, ST_GeomFromText(:aoi_wkt, 4326), now(), 'complete',
                CAST(:stage_reports AS jsonb), CAST(:metrics AS jsonb),
                CAST(:queue_briefs AS jsonb), CAST(:changes AS jsonb),
                :ledger_root, :software_version)
        ON CONFLICT (run_id) DO UPDATE SET
            finished_at = EXCLUDED.finished_at, status = EXCLUDED.status,
            stage_reports = EXCLUDED.stage_reports, metrics = EXCLUDED.metrics,
            queue_briefs = EXCLUDED.queue_briefs, changes = EXCLUDED.changes,
            ledger_root = EXCLUDED.ledger_root
    """), {
        "run_id": run_id, "aoi_name": aoi_name, "aoi_wkt": poly_wkt,
        "stage_reports": json.dumps(metrics.get("stages", {}), default=str),
        "metrics": json.dumps(metrics, default=str),
        "queue_briefs": json.dumps(briefs, default=str),
        "changes": json.dumps(changes_list[:20000], default=str),
        "ledger_root": ledger.merkle_root(), "software_version": "samanvay/1.0.0",
    })


# --------------------------------------------------------------------------------------
# read side — the FeatureStore-compatible interface used by api/app.py
# --------------------------------------------------------------------------------------


class PostgisStore:
    """Serves harmonised data live from Postgres/PostGIS. Same public interface as
    ``api.app.FeatureStore`` (``collections``, ``ledger``, ``metrics()``, ``queue()``,
    ``changes()``) so ``create_app()`` can swap between them without touching any route."""

    def __init__(self, engine, out_dir: str = "out/chennai") -> None:
        self.engine = engine
        self.out_dir = out_dir
        self._ledger_cache: ProvenanceLedger | None = None

    def _conn(self):
        return self.engine.connect()

    @property
    def collections(self) -> dict[str, list[dict[str, Any]]]:
        from sqlalchemy import text
        with self._conn() as conn:
            parcels = [self._parcel_feature(row) for row in conn.execute(text("""
                SELECT entity_id, ulpin, ST_AsGeoJSON(geom) AS geojson, survey_number,
                       subdivision, village_lgd, village_name, ulb_code, ward, zone,
                       locality, street, land_use, is_public_land, recorded_extent_m2,
                       computed_extent_m2, extent_discrepancy_pct, building_count,
                       built_up_area_m2, ground_coverage_pct, max_height_m, confidence,
                       confidence_grade, contributing_datasets, conflict_count,
                       adjudication_state, change_type, ledger_head, extra_attributes
                FROM harmonised_parcel WHERE valid_to IS NULL
            """)).mappings()]
            buildings = [self._building_feature(row) for row in conn.execute(text("""
                SELECT entity_id, parcel_ulpin, ST_AsGeoJSON(geom) AS geojson, door_number,
                       ward, zone, locality, street, building_use, construction_type,
                       floors, footprint_area_m2, max_height_m, estimated_floors,
                       confidence, confidence_grade, contributing_datasets, change_type,
                       ledger_head, extra_attributes
                FROM harmonised_building
            """)).mappings()]
            adjudication = [self._adjudication_feature(row) for row in conn.execute(text("""
                SELECT a.case_id, a.entity_id, a.property_path, a.priority, a.state,
                       COALESCE(p.geom, b.geom) AS geom_col,
                       ST_AsGeoJSON(COALESCE(p.geom, b.geom)) AS geojson
                FROM adjudication_case a
                LEFT JOIN harmonised_parcel p ON p.entity_id = a.entity_id
                LEFT JOIN harmonised_building b ON b.entity_id = a.entity_id
                WHERE a.state = 'queued'
            """)).mappings()]
        return {
            "parcels": [f for f in parcels if f],
            "buildings": [f for f in buildings if f],
            "adjudication": [f for f in adjudication if f],
        }

    @staticmethod
    def _parcel_feature(row: dict[str, Any]) -> dict[str, Any] | None:
        if not row["geojson"]:
            return None
        props = {
            "entity_id": row["entity_id"], "ulpin": row["ulpin"],
            "survey_number": row["survey_number"], "subdivision": row["subdivision"],
            "village_lgd": row["village_lgd"], "village_name": row["village_name"],
            "ulb_code": row["ulb_code"], "ward": row["ward"], "zone": row["zone"],
            "locality": row["locality"], "street": row["street"], "land_use": row["land_use"],
            "is_public_land": row["is_public_land"],
            "recorded_extent_m2": row["recorded_extent_m2"],
            "computed_extent_m2": row["computed_extent_m2"],
            "extent_discrepancy_pct": row["extent_discrepancy_pct"],
            "building_count": row["building_count"], "built_up_area_m2": row["built_up_area_m2"],
            "ground_coverage_pct": row["ground_coverage_pct"], "max_height_m": row["max_height_m"],
            "confidence": row["confidence"], "confidence_grade": row["confidence_grade"],
            "contributing_datasets": ",".join(row["contributing_datasets"] or []),
            "conflict_count": row["conflict_count"],
            "adjudication_state": row["adjudication_state"], "change_type": row["change_type"],
            "ledger_head": row["ledger_head"],
            **(row["extra_attributes"] or {}),
        }
        return {"type": "Feature", "geometry": json.loads(row["geojson"]), "properties": props}

    @staticmethod
    def _building_feature(row: dict[str, Any]) -> dict[str, Any] | None:
        if not row["geojson"]:
            return None
        props = {
            "entity_id": row["entity_id"], "parcel_ulpin": row["parcel_ulpin"],
            "door_number": row["door_number"], "ward": row["ward"], "zone": row["zone"],
            "locality": row["locality"], "street": row["street"],
            "building_use": row["building_use"], "construction_type": row["construction_type"],
            "floors": row["floors"], "footprint_area_m2": row["footprint_area_m2"],
            "max_height_m": row["max_height_m"], "estimated_floors": row["estimated_floors"],
            "confidence": row["confidence"], "confidence_grade": row["confidence_grade"],
            "contributing_datasets": ",".join(row["contributing_datasets"] or []),
            "change_type": row["change_type"], "ledger_head": row["ledger_head"],
            **(row["extra_attributes"] or {}),
        }
        return {"type": "Feature", "geometry": json.loads(row["geojson"]), "properties": props}

    @staticmethod
    def _adjudication_feature(row: dict[str, Any]) -> dict[str, Any] | None:
        if not row["geojson"]:
            return None
        return {
            "type": "Feature", "geometry": json.loads(row["geojson"]),
            "properties": {"case_id": row["case_id"], "entity_id": row["entity_id"],
                           "property": row["property_path"], "priority": row["priority"],
                           "state": row["state"]},
        }

    @property
    def ledger(self) -> ProvenanceLedger:
        if self._ledger_cache is None:
            from sqlalchemy import text
            with self._conn() as conn:
                rows = conn.execute(text(
                    "SELECT idx AS \"index\", ts AS timestamp, entity_id, operation, actor, "
                    "payload, prev_hash, entry_hash FROM provenance_ledger ORDER BY idx"
                )).mappings()
                entries = []
                for r in rows:
                    d = dict(r)
                    d["timestamp"] = d["timestamp"].isoformat() if hasattr(d["timestamp"], "isoformat") else str(d["timestamp"])
                    entries.append(d)
            self._ledger_cache = ProvenanceLedger.from_entries(entries)
        return self._ledger_cache

    def metrics(self) -> dict[str, Any]:
        from sqlalchemy import text
        with self._conn() as conn:
            row = conn.execute(text(
                "SELECT metrics FROM pipeline_run ORDER BY started_at DESC LIMIT 1"
            )).first()
        return dict(row[0]) if row and row[0] else {}

    def queue(self) -> list[dict[str, Any]]:
        from sqlalchemy import text
        with self._conn() as conn:
            row = conn.execute(text(
                "SELECT queue_briefs FROM pipeline_run ORDER BY started_at DESC LIMIT 1"
            )).first()
        return list(row[0]) if row and row[0] else []

    def changes(self) -> list[dict[str, Any]]:
        from sqlalchemy import text
        with self._conn() as conn:
            row = conn.execute(text(
                "SELECT changes FROM pipeline_run ORDER BY started_at DESC LIMIT 1"
            )).first()
        return list(row[0]) if row and row[0] else []
