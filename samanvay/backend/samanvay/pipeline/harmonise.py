"""The harmonisation pipeline.

This is the whole platform assembled into one runnable DAG. It takes the raw multi-source
corpus for an area of interest and emits a single harmonised parcel and building fabric,
with per-feature confidence, an adjudication queue for what it could not decide, a change
log, and a verifiable provenance ledger.

Stages, and why each is where it is:

===  ==================  =========================================================
 1   ``ingest``          Stream and profile every source. Nothing is altered yet.
 2   ``schema_map``      Learn each source's crosswalk onto the canonical schema.
 3   ``reproject``       Move everything into one metric CRS. Every later measurement
                         depends on this and nothing before it can be trusted without it.
 4   ``topology``        Validate and repair each layer *independently*. Repairing after
                         merging would hide which source the defect came from.
 5   ``match``           Learn and apply the spatial matcher, pairwise between layers.
 6   ``cluster``         Fuse the pairwise matches into entity clusters across all sources.
 7   ``resolve``         Fuse claims, detect conflicts, decide or escalate.
 8   ``assemble``        Mint identity, compute derived attributes, build output records.
 9   ``structures``      Attach buildings to parcels; coverage, height, floor estimates.
10   ``change``          Type the differences between sources as registry-actionable events.
11   ``confidence``      Score every output on six dimensions.
12   ``publish``         Write outputs, ledger, metrics and the adjudication queue.
===  ==================  =========================================================
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
from typing import Any, Iterable, Sequence

from ..attributes.canonical import PARCEL_SCHEMA, validate_record
from ..attributes.schema_match import SchemaMatcher, describe_crosswalk
from ..change.vector_change import ChangeDetector, summarise as summarise_change
from ..confidence.scorer import ConfidenceScorer, summarise as summarise_confidence
from ..conflict.queue import AdjudicationQueue
from ..conflict.resolver import ConflictResolver
from ..core.ids import AdminContext, UlpinMinter, entity_id as make_entity_id
from ..core.ledger import ProvenanceLedger
from ..core.models import (Claim, FeatureClass, SourceDataset, SourceType)
from ..core.registry import AUTHORITIES, SourceRegistry
from ..crs.engine import CrsEngine, format_extent, geodesic_area_m2
from ..geoai.footprints import ExtractionQC, regularise
from ..matching.features import BlockingConfig, MatchableFeature
from ..matching.pipeline import MatchingPipeline, cluster_across_layers
from ..matching.normalise import normalise_survey_number, normalise_ward, normalise_zone
from ..topology.repair import RepairConfig, TopologyRepairer
from ..topology.validate import TopologyValidator, ValidationConfig
from .dag import Dag


# --------------------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------------------


@dataclass
class LayerSpec:
    """One input layer and everything the pipeline needs to know about it."""

    dataset_id: str
    path: str
    source_type: SourceType
    feature_class: FeatureClass
    authority: str
    licence: str
    accuracy_m: float
    vintage: str = ""
    id_fields: tuple[str, ...] = ()
    role: str = "reference"       # "reference" | "candidate" | "context"
    max_features: int | None = None
    # -- provenance, carried through to SourceDataset and the ledger ---------------
    tier: str = "official"        # "official" | "mirror" | "proxy" — see SourceDataset.tier
    platform: str = ""            # the specific portal/service, distinct from `authority`
    original_format: str = ""
    coverage: str = ""
    transformation: str = ""


@dataclass
class HarmoniseConfig:
    aoi_name: str = "Chennai Central"
    bbox: tuple[float, float, float, float] = (80.20, 13.03, 80.28, 13.11)
    metric_crs: str = "EPSG:32644"
    state_lgd: str = "33"
    district_lgd: str = "571"
    ulb: str = "GCC"
    out_dir: str = "out"
    checkpoint_dir: str | None = None
    parcel_pairs: tuple[tuple[str, str], ...] = ()
    building_pairs: tuple[tuple[str, str], ...] = ()
    repair_topology: bool = True
    regularise_footprints: bool = True
    max_adjudication_cases: int = 5000
    emit_geojson: bool = True


@dataclass
class HarmoniseOutput:
    run_id: str = ""
    parcels: list[dict[str, Any]] = field(default_factory=list)
    buildings: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    queue: AdjudicationQueue | None = None
    ledger: ProvenanceLedger | None = None


# --------------------------------------------------------------------------------------
# the pipeline
# --------------------------------------------------------------------------------------


class HarmonisationPipeline:
    def __init__(self, layers: Sequence[LayerSpec], config: HarmoniseConfig) -> None:
        self.layers = list(layers)
        self.config = config
        self.registry = SourceRegistry()
        self.crs = CrsEngine()
        self.ledger = ProvenanceLedger(os.path.join(config.out_dir, "ledger.jsonl"))
        self.queue = AdjudicationQueue(os.path.join(config.out_dir, "decisions.jsonl"))
        self.run_id = str(uuid.uuid4())
        self.dag = Dag("harmonise", checkpoint_dir=config.checkpoint_dir)
        self._build()

    # -- stage definitions ---------------------------------------------------------

    def _build(self) -> None:
        d = self.dag

        @d.stage("ingest", description="Stream, profile and register every source layer.")
        def _ingest(ctx: dict[str, Any]) -> dict[str, Any]:
            return self.stage_ingest()

        @d.stage("schema_map", ["ingest"],
                 description="Learn each source schema's crosswalk to the canonical schema.")
        def _schema(ctx: dict[str, Any]) -> dict[str, Any]:
            return self.stage_schema_map(ctx["ingest"])

        @d.stage("reproject", ["ingest"],
                 description="Move every layer into the working metric CRS.")
        def _reproject(ctx: dict[str, Any]) -> dict[str, Any]:
            return self.stage_reproject(ctx["ingest"])

        @d.stage("topology", ["reproject"],
                 description="Validate and repair each layer independently.")
        def _topology(ctx: dict[str, Any]) -> dict[str, Any]:
            return self.stage_topology(ctx["reproject"])

        @d.stage("match", ["topology"],
                 description="Learn and apply the spatial matcher between layer pairs.")
        def _match(ctx: dict[str, Any]) -> dict[str, Any]:
            return self.stage_match(ctx["topology"])

        @d.stage("cluster", ["match"],
                 description="Fuse pairwise matches into cross-source entity clusters.")
        def _cluster(ctx: dict[str, Any]) -> dict[str, Any]:
            return self.stage_cluster(ctx["match"], ctx["topology"])

        @d.stage("resolve", ["cluster", "schema_map"],
                 description="Fuse claims, detect conflicts, decide or escalate.")
        def _resolve(ctx: dict[str, Any]) -> dict[str, Any]:
            return self.stage_resolve(ctx["cluster"], ctx["topology"], ctx["schema_map"])

        @d.stage("assemble", ["resolve"],
                 description="Mint identity and build the harmonised records.")
        def _assemble(ctx: dict[str, Any]) -> dict[str, Any]:
            return self.stage_assemble(ctx["resolve"], ctx["topology"])

        @d.stage("structures", ["assemble"],
                 description="Attach buildings to parcels and derive built-form attributes.")
        def _structures(ctx: dict[str, Any]) -> dict[str, Any]:
            return self.stage_structures(ctx["assemble"], ctx["topology"])

        @d.stage("change", ["match", "assemble"],
                 description="Type source differences as registry-actionable change.")
        def _change(ctx: dict[str, Any]) -> dict[str, Any]:
            return self.stage_change(ctx["match"], ctx["topology"])

        @d.stage("confidence", ["structures", "resolve"],
                 description="Score every output on six explainable dimensions.")
        def _confidence(ctx: dict[str, Any]) -> dict[str, Any]:
            return self.stage_confidence(ctx["structures"], ctx["resolve"], ctx["topology"])

        @d.stage("publish", ["confidence", "change"], cacheable=False,
                 description="Write outputs, ledger, metrics and the adjudication queue.")
        def _publish(ctx: dict[str, Any]) -> dict[str, Any]:
            return self.stage_publish(ctx)

    def run(self) -> HarmoniseOutput:
        t0 = time.time()
        self.ledger.append("run", "pipeline_start",
                           {"run_id": self.run_id, "aoi": self.config.aoi_name,
                            "layers": [l.dataset_id for l in self.layers]})
        self.dag.run()
        out = HarmoniseOutput(
            run_id=self.run_id,
            parcels=self.dag.context.get("publish", {}).get("parcels", []),
            buildings=self.dag.context.get("publish", {}).get("buildings", []),
            metrics=self.dag.context.get("publish", {}).get("metrics", {}),
            queue=self.queue,
            ledger=self.ledger,
        )
        out.metrics["total_seconds"] = round(time.time() - t0, 2)
        out.metrics["dag"] = self.dag.report()
        self.ledger.append("run", "pipeline_end",
                           {"run_id": self.run_id,
                            "seconds": out.metrics["total_seconds"],
                            "merkle_root": self.ledger.merkle_root()})
        return out

    # ==================================================================================
    # stages
    # ==================================================================================

    def stage_ingest(self) -> dict[str, Any]:
        from ..ingest.vector import GeoJsonConnector, GeoJsonLinesConnector

        out: dict[str, Any] = {"layers": {}, "_report": {}}
        for spec in self.layers:
            ds = SourceDataset(
                dataset_id=spec.dataset_id,
                title=spec.dataset_id,
                source_type=spec.source_type,
                authority=AUTHORITIES.get(spec.authority,
                                          AUTHORITIES["DOLR"]),
                licence=spec.licence,
                crs="EPSG:4326",
                positional_accuracy_m=spec.accuracy_m,
                acquired_on=_parse_vintage(spec.vintage),
                uri=spec.path,
                tier=spec.tier,
                platform=spec.platform,
                original_format=spec.original_format,
                coverage=spec.coverage,
                transformation=spec.transformation,
            )
            conn_cls = (GeoJsonLinesConnector if spec.path.endswith(".geojsonl")
                        else GeoJsonConnector)
            conn = conn_cls(ds, spec.feature_class)
            profile = conn.probe(spec.path)
            feats: list[dict[str, Any]] = []
            for i, rf in enumerate(conn.read(spec.path, bbox=self.config.bbox,
                                             limit=spec.max_features)):
                feats.append({"fid": rf.source_feature_id, "geom": rf.geometry,
                              "props": rf.properties})
            ds.feature_count = len(feats)
            self.registry.register(ds)
            out["layers"][spec.dataset_id] = {"spec": spec, "features": feats,
                                              "profile": profile}
            out["_report"][spec.dataset_id] = {
                "features_in_aoi": len(feats),
                "declared_accuracy_m": spec.accuracy_m,
                "coordinate_precision_digits": profile.coordinate_precision_digits,
                "implied_precision_m": round(profile.implied_precision_m(), 4),
                "invalid_geometries_in_sample": profile.invalid_geometry_count,
                "warnings": profile.warnings[:4],
            }
            self.ledger.append(spec.dataset_id, "ingest",
                               {"features": len(feats), "accuracy_m": spec.accuracy_m,
                                "authority": spec.authority, "licence": spec.licence,
                                "tier": spec.tier, "platform": spec.platform,
                                "original_format": spec.original_format,
                                "coverage": spec.coverage,
                                "transformation": spec.transformation,
                                "vintage": spec.vintage})
        return out

    def stage_schema_map(self, ingest: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"crosswalks": {}, "matches": {}, "_report": {}}
        matcher = SchemaMatcher(PARCEL_SCHEMA)
        for ds_id, layer in ingest["layers"].items():
            records = [f["props"] for f in layer["features"][:6000]]
            if not records:
                continue
            matches = matcher.match(records)
            cw = matcher.crosswalk(matches)
            out["crosswalks"][ds_id] = cw
            out["matches"][ds_id] = matches
            out["_report"][ds_id] = {
                "columns": len({k for r in records[:200] for k in r}),
                "mapped": len(cw),
                "crosswalk": cw,
                "needs_review": [m.explain() for m in matches if not m.accepted][:5],
            }
            self.ledger.append(ds_id, "schema_map", {"crosswalk": cw})
        return out

    def stage_reproject(self, ingest: dict[str, Any]) -> dict[str, Any]:
        from pyproj import Transformer
        from shapely.ops import transform as shp_transform

        tr = Transformer.from_crs("EPSG:4326", self.config.metric_crs, always_xy=True)
        fn = lambda x, y, z=None: tr.transform(x, y)  # noqa: E731

        out: dict[str, Any] = {"layers": {}, "_report": {}}
        for ds_id, layer in ingest["layers"].items():
            spec: LayerSpec = layer["spec"]
            feats: list[MatchableFeature] = []
            wgs: dict[str, Any] = {}
            for f in layer["features"]:
                try:
                    gm = shp_transform(fn, f["geom"])
                except Exception:  # noqa: BLE001
                    continue
                feats.append(MatchableFeature(fid=f["fid"], dataset_id=ds_id,
                                              geometry=gm, attributes=f["props"]))
                wgs[f["fid"]] = f["geom"]
            out["layers"][ds_id] = {"spec": spec, "features": feats, "wgs84": wgs}
            out["_report"][ds_id] = {"reprojected": len(feats),
                                     "target_crs": self.config.metric_crs}
        return out

    def stage_topology(self, reproject: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"layers": {}, "_report": {}}
        validator = TopologyValidator(ValidationConfig())
        repairer = TopologyRepairer(RepairConfig())

        for ds_id, layer in reproject["layers"].items():
            spec: LayerSpec = layer["spec"]
            feats: list[MatchableFeature] = layer["features"]
            geoms = {f.fid: f.geometry for f in feats}

            # Partition checks are quadratic-ish in dense fabric and only meaningful for
            # a layer that is *supposed* to be a partition. Building layers are not.
            check_partition = spec.feature_class is FeatureClass.PARCEL and len(geoms) <= 60000
            before = validator.validate(geoms, check_partition=check_partition)

            repaired = geoms
            repair_report = None
            if self.config.repair_topology and spec.feature_class is FeatureClass.PARCEL:
                try:
                    repaired, repair_report = repairer.repair(
                        geoms, validate_before_after=False)
                except Exception as exc:  # noqa: BLE001
                    out["_report"].setdefault(ds_id, {})["repair_error"] = (
                        f"{type(exc).__name__}: {exc}; the layer is carried forward "
                        f"unrepaired and its topological confidence is reduced accordingly")
                    repaired, repair_report = geoms, None

            if (self.config.regularise_footprints
                    and spec.feature_class is FeatureClass.BUILDING
                    and spec.source_type is SourceType.AI_EXTRACTION):
                qc = ExtractionQC(min_confidence=0.65, min_area_m2=8.0)
                attrs = {f.fid: f.attributes for f in feats}
                repaired, dropped = qc.filter(repaired, attrs)
                repaired, reg_report = regularise(repaired)
                out["_report"].setdefault(ds_id, {})["extraction_qc_dropped"] = dropped
                out["_report"][ds_id]["regularisation"] = reg_report.summary()

            kept = [MatchableFeature(fid=f.fid, dataset_id=ds_id,
                                     geometry=repaired[f.fid], attributes=f.attributes)
                    for f in feats if f.fid in repaired]

            out["layers"][ds_id] = {"spec": spec, "features": kept,
                                    "wgs84": layer["wgs84"],
                                    "index": {f.fid: f for f in kept}}
            rep = out["_report"].setdefault(ds_id, {})
            rep["validation_before"] = before.summary()
            rep["violation_counts"] = before.counts
            if repair_report is not None:
                rep["repair"] = repair_report.summary()
                rep["repairs_refused"] = len(repair_report.refused)
                self.ledger.append(ds_id, "topology_repair", {
                    "actions": len(repair_report.actions),
                    "refused": len(repair_report.refused),
                    "area_changed_m2": round(repair_report.total_area_changed_m2, 3),
                })
        return out

    def stage_match(self, topology: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"results": {}, "_report": {}}
        pairs = list(self.config.parcel_pairs) + list(self.config.building_pairs)
        for lds, rds in pairs:
            if lds not in topology["layers"] or rds not in topology["layers"]:
                continue
            L = topology["layers"][lds]
            R = topology["layers"][rds]
            if not L["features"] or not R["features"]:
                continue
            pipe = MatchingPipeline(
                blocking=BlockingConfig(accuracy_multiplier=3.0,
                                        max_candidates_per_feature=8),
            )
            res = pipe.run(L["features"], R["features"],
                           acc_left_m=L["spec"].accuracy_m,
                           acc_right_m=R["spec"].accuracy_m)
            out["results"][(lds, rds)] = res
            out["_report"][f"{lds}->{rds}"] = {
                "candidate_pairs": res.blocking.get("candidate_pairs"),
                "accepted": len(res.accepted),
                "registration": res.offset.describe(),
                "assignment": res.assignment.summary() if res.assignment else None,
                "model": res.training.summary() if res.training else None,
                "seconds": round(res.seconds, 1),
            }
            self.ledger.append(f"{lds}->{rds}", "match", {
                "accepted": len(res.accepted),
                "offset_m": round(res.offset.magnitude_m, 3),
            })
        return out

    def stage_cluster(self, match: dict[str, Any],
                      topology: dict[str, Any]) -> dict[str, Any]:
        all_features = {ds: [f.fid for f in layer["features"]]
                        for ds, layer in topology["layers"].items()}
        clusters = cluster_across_layers(match["results"], all_features=all_features)
        by_sources: dict[int, int] = {}
        for c in clusters:
            by_sources[c.n_sources] = by_sources.get(c.n_sources, 0) + 1
        corroborated = sum(1 for c in clusters if c.n_sources > 1)
        return {
            "clusters": clusters,
            "_report": {
                "entities": len(clusters),
                "corroborated_by_2_or_more_sources": corroborated,
                "single_source_only": len(clusters) - corroborated,
                "corroboration_rate": round(corroborated / max(len(clusters), 1), 4),
                "by_source_count": by_sources,
                "mean_support": round(
                    sum(c.support for c in clusters) / max(len(clusters), 1), 4),
                "note": ("Single-source entities are retained in full. Dropping them would "
                         "delete land from the cadastre; they are instead published with a "
                         "low source-agreement score so the gap is visible."),
            },
        }

    def stage_resolve(self, cluster: dict[str, Any], topology: dict[str, Any],
                      schema: dict[str, Any]) -> dict[str, Any]:
        resolver = ConflictResolver(self.registry)
        entities: dict[str, dict[str, Any]] = {}
        n_conflicts = 0
        n_escalated = 0
        strategy_counts: dict[str, int] = {}
        rule_hits: dict[str, int] = {}

        for c in cluster["clusters"]:
            claims: list[Claim] = []
            geoms: list[Any] = []
            primary_ds = None
            primary_feature = None
            for ds_id, fid in c.members.items():
                layer = topology["layers"].get(ds_id)
                if layer is None:
                    continue
                f = layer["index"].get(fid)
                if f is None:
                    continue
                spec: LayerSpec = layer["spec"]
                cw = schema["crosswalks"].get(ds_id, {})
                obs = _parse_vintage(spec.vintage)
                claims.append(Claim(dataset_id=ds_id, source_type=spec.source_type,
                                    property_path="geometry", value=f.geometry.wkt,
                                    observed_on=obs, accuracy_m=spec.accuracy_m,
                                    source_feature_id=fid))
                for raw_key, value in f.attributes.items():
                    target = cw.get(raw_key)
                    if not target or value in (None, ""):
                        continue
                    claims.append(Claim(dataset_id=ds_id, source_type=spec.source_type,
                                        property_path=target,
                                        value=_normalise_value(target, value),
                                        observed_on=obs, source_feature_id=fid,
                                        extraction_confidence=_extraction_conf(f.attributes)))
                geoms.append(f.geometry)
                if primary_ds is None or spec.role == "reference":
                    primary_ds, primary_feature = ds_id, f

            if not claims or primary_feature is None:
                continue

            ctx = {
                "has_structure": any(
                    topology["layers"][ds]["spec"].feature_class is FeatureClass.BUILDING
                    for ds in c.members if ds in topology["layers"]),
                "max_pairwise_offset_m": _max_offset(geoms),
            }
            outcome = resolver.resolve_entity(c.cluster_id, claims, ctx)
            n_conflicts += len(outcome.conflicts)
            n_escalated += len(outcome.escalated)
            for k, v in outcome.strategy_counts.items():
                strategy_counts[k] = strategy_counts.get(k, 0) + v
            for k, v in outcome.rule_hits.items():
                rule_hits[k] = rule_hits.get(k, 0) + v

            entities[c.cluster_id] = {
                "cluster": c,
                "claims": claims,
                "resolutions": outcome.resolutions,
                "conflicts": outcome.conflicts,
                "primary_dataset": primary_ds,
                "primary_feature": primary_feature,
                "feature_class": topology["layers"][primary_ds]["spec"].feature_class,
            }

            for conf in outcome.escalated[: max(0, self.config.max_adjudication_cases
                                                - len(self.queue.cases))]:
                res = next((r for r in outcome.resolutions
                            if r.conflict_id == conf.conflict_id), None)
                if res is not None:
                    self.queue.enqueue(conf, res,
                                       area_m2=float(primary_feature.geometry.area),
                                       ward=str(primary_feature.attributes.get("ward_number")
                                                or primary_feature.attributes.get("ward") or ""))

        self.queue.rerank()
        return {
            "entities": entities,
            "_report": {
                "entities": len(entities),
                "conflicts": n_conflicts,
                "escalated": n_escalated,
                "auto_resolution_rate": round(
                    1.0 - n_escalated / max(n_conflicts, 1), 4),
                "strategies": strategy_counts,
                "rules_fired": rule_hits,
                "queue": self.queue.stats().summary(),
            },
        }

    def stage_assemble(self, resolve: dict[str, Any],
                       topology: dict[str, Any]) -> dict[str, Any]:
        from shapely import wkt as shp_wkt
        from pyproj import Transformer
        from shapely.ops import transform as shp_transform

        back = Transformer.from_crs(self.config.metric_crs, "EPSG:4326", always_xy=True)
        to_wgs = lambda g: shp_transform(  # noqa: E731
            lambda x, y, z=None: back.transform(x, y), g)

        parcels: dict[str, dict[str, Any]] = {}
        buildings: dict[str, dict[str, Any]] = {}
        minter = UlpinMinter(snap_precision=10)

        for eid, ent in resolve["entities"].items():
            attrs: dict[str, Any] = {}
            geom_wkt = None
            for r in ent["resolutions"]:
                if r.property_path == "geometry":
                    geom_wkt = r.chosen_value
                else:
                    attrs[r.property_path] = r.chosen_value
            geom_m = (shp_wkt.loads(geom_wkt) if geom_wkt
                      else ent["primary_feature"].geometry)
            geom_wgs = to_wgs(geom_m)

            area_m2 = _geodesic_area(geom_wgs)
            if ent["feature_class"] is FeatureClass.BUILDING:
                attrs["footprint_area_m2"] = round(area_m2, 3)
            else:
                attrs["computed_extent_m2"] = round(area_m2, 3)
                attrs["recorded_extent_display"] = format_extent(area_m2)
            rec = attrs.get("recorded_extent_m2")
            if rec:
                try:
                    rec = float(rec)
                    attrs["extent_discrepancy_pct"] = round(
                        100.0 * (area_m2 - rec) / rec, 3) if rec else None
                except (TypeError, ValueError):
                    attrs.pop("recorded_extent_m2", None)

            c = geom_wgs.centroid
            admin = AdminContext(
                state_lgd=str(attrs.get("state_lgd") or self.config.state_lgd),
                district_lgd=str(attrs.get("district_lgd") or self.config.district_lgd),
                ulb_or_block=self.config.ulb,
                ward=str(attrs.get("ward") or ""),
                village_or_zone=str(attrs.get("village_lgd") or attrs.get("zone") or ""),
            )
            record = {
                "entity_id": make_entity_id(ent["feature_class"].value, geom_wgs.wkt,
                                            self.config.aoi_name),
                "cluster_id": eid,
                "geometry": geom_wgs,
                "geometry_metric": geom_m,
                "attributes": attrs,
                "contributing_datasets": sorted(ent["cluster"].members),
                "n_sources": ent["cluster"].n_sources,
                "support": ent["cluster"].support,
                "claims": ent["claims"],
                "resolutions": ent["resolutions"],
                "conflicts": ent["conflicts"],
            }
            if ent["feature_class"] is FeatureClass.PARCEL:
                record["ulpin"] = minter.mint(admin, c.x, c.y, key=eid)
                parcels[record["entity_id"]] = record
            else:
                buildings[record["entity_id"]] = record

        return {
            "parcels": parcels,
            "buildings": buildings,
            "_report": {
                "parcels": len(parcels),
                "buildings": len(buildings),
                "distinct_ulpins": minter.issued,
                "ulpin_collisions_resolved": minter.collisions_resolved,
                "ulpin_uniqueness": ("guaranteed unique; "
                                     f"{minter.collisions_resolved} snapped-cell collisions "
                                     f"were disambiguated by an identity-stable nonce"),
            },
        }

    def stage_structures(self, assemble: dict[str, Any],
                         topology: dict[str, Any]) -> dict[str, Any]:
        """Attach buildings to parcels and derive built-form attributes.

        This is the step that produces information no single source had: the municipal
        survey knows where the buildings are, the cadastre knows where the parcels are, and
        only the join tells you the ground coverage of each plot — which is the input to
        every development-control and property-tax question a corporation asks.
        """
        from shapely.strtree import STRtree

        parcels = assemble["parcels"]
        buildings = assemble["buildings"]
        if not parcels or not buildings:
            return {**assemble, "_report": {"note": "no parcel/building overlap to compute"}}

        pids = list(parcels)
        tree = STRtree([parcels[p]["geometry_metric"] for p in pids])
        assigned = 0
        for bid, b in buildings.items():
            g = b["geometry_metric"]
            best, best_area = None, 0.0
            for j in tree.query(g):
                pid = pids[int(j)]
                try:
                    a = g.intersection(parcels[pid]["geometry_metric"]).area
                except Exception:  # noqa: BLE001
                    continue
                if a > best_area:
                    best, best_area = pid, a
            if best is None or best_area < 0.25 * g.area:
                continue
            b["parcel_entity_id"] = best
            b["attributes"]["parcel_ulpin"] = parcels[best].get("ulpin")
            p = parcels[best]
            p.setdefault("_buildings", []).append(bid)
            assigned += 1

        heights: list[float] = []
        for pid, p in parcels.items():
            bl = p.get("_buildings", [])
            built = sum(buildings[b]["geometry_metric"].area for b in bl)
            area = p["attributes"].get("computed_extent_m2") or p["geometry_metric"].area
            p["attributes"]["building_count"] = len(bl)
            p["attributes"]["built_up_area_m2"] = round(built, 2)
            p["attributes"]["ground_coverage_pct"] = round(
                100.0 * built / area, 2) if area else None
            hs = [float(buildings[b]["attributes"].get("max_height_m"))
                  for b in bl if buildings[b]["attributes"].get("max_height_m") not in (None, "")]
            if hs:
                p["attributes"]["max_height_m"] = round(max(hs), 2)
                heights.extend(hs)

        over = [p for p in parcels.values()
                if (p["attributes"].get("ground_coverage_pct") or 0) > 100.0]
        return {
            **assemble,
            "_report": {
                "buildings_assigned_to_parcels": assigned,
                "assignment_rate": round(assigned / max(len(buildings), 1), 4),
                "parcels_with_buildings": sum(
                    1 for p in parcels.values() if p["attributes"].get("building_count")),
                "parcels_over_100pct_coverage": len(over),
                "note": ("Ground coverage above 100% is not a bug: it means the harmonised "
                         "footprints extend beyond the cadastral parcel, which is either a "
                         "boundary error or a genuine set-back violation. Both need a human."),
                "mean_structure_height_m": round(
                    sum(heights) / len(heights), 2) if heights else None,
            },
        }

    def stage_change(self, match: dict[str, Any],
                     topology: dict[str, Any]) -> dict[str, Any]:
        # Every pair configured here compares two *contemporaneous* departments rather than
        # two epochs, so the detector runs in cross-source mode. In temporal mode it would
        # report a demolition for every building the corporation holds and the extraction
        # missed — tens of thousands of false mutations. The mode switch is the difference
        # between a useful discrepancy report and an unusable one.
        from ..change.vector_change import ChangeConfig

        detector = ChangeDetector(ChangeConfig(mode="cross_source"))
        records: list[Any] = []
        summaries: dict[str, Any] = {}
        for (lds, rds), res in match["results"].items():
            L = topology["layers"].get(lds)
            R = topology["layers"].get(rds)
            if not L or not R or res.assignment is None:
                continue
            before = {f.fid: f.geometry for f in L["features"]}
            after = {f.fid: f.geometry for f in R["features"]}
            recs = detector.detect(
                res.pairs, before, after,
                before_attrs={f.fid: f.attributes for f in L["features"]},
                after_attrs={f.fid: f.attributes for f in R["features"]},
                unmatched_before=res.assignment.unmatched_left,
                unmatched_after=res.assignment.unmatched_right,
            )
            records.extend(recs)
            summaries[f"{lds}->{rds}"] = summarise_change(
                recs, detector.systematic_offset).summary()
        return {"records": records, "_report": summaries}

    def stage_confidence(self, structures: dict[str, Any], resolve: dict[str, Any],
                         topology: dict[str, Any]) -> dict[str, Any]:
        scorer = ConfidenceScorer(self.registry)
        ok, _, _ = self.ledger.verify()
        reports: dict[str, Any] = {}
        for coll in ("parcels", "buildings"):
            for eid, rec in structures[coll].items():
                topo = {
                    "area_m2": rec["geometry_metric"].area,
                    "invalid": not rec["geometry_metric"].is_valid,
                }
                r = scorer.score(
                    eid,
                    claims=rec["claims"],
                    resolutions=rec["resolutions"],
                    attributes=rec["attributes"],
                    topology=topo,
                    lineage_ok=ok,
                    independent_sources=rec["n_sources"],
                    feature_class="building" if coll == "buildings" else "parcel",
                )
                rec["confidence"] = r
                reports[eid] = r
        summary = summarise_confidence(list(reports.values()))
        return {**structures, "confidence": reports,
                "_report": {"summary": summary.summary(),
                            "mean_components": summary.mean_components,
                            "grades": summary.grade_counts}}

    def stage_publish(self, ctx: dict[str, Any]) -> dict[str, Any]:
        cfg = self.config
        os.makedirs(cfg.out_dir, exist_ok=True)
        conf_stage = ctx["confidence"]
        parcels = conf_stage["parcels"]
        buildings = conf_stage["buildings"]

        parcel_rows = [self._row(r, "parcel") for r in parcels.values()]
        building_rows = [self._row(r, "building") for r in buildings.values()]

        if cfg.emit_geojson:
            _write_geojson(os.path.join(cfg.out_dir, "harmonised_parcels.geojson"),
                           parcels.values())
            _write_geojson(os.path.join(cfg.out_dir, "harmonised_buildings.geojson"),
                           buildings.values())
            # Adjudication cases are keyed by cluster id, which is the identity the
            # resolver worked in; the published records are keyed by content-addressed
            # entity id. Index by cluster id or the queue layer comes out empty.
            by_cluster = {r["cluster_id"]: r["geometry"]
                          for r in list(parcels.values()) + list(buildings.values())}
            queue_geoms = {c.entity_id: by_cluster[c.entity_id]
                           for c in self.queue.cases.values() if c.entity_id in by_cluster}
            with open(os.path.join(cfg.out_dir, "adjudication_queue.geojson"), "w",
                      encoding="utf-8") as fh:
                json.dump(self.queue.to_geojson(queue_geoms), fh)

        changes = ctx.get("change", {}).get("records", [])
        with open(os.path.join(cfg.out_dir, "changes.json"), "w", encoding="utf-8") as fh:
            json.dump([c.to_dict() for c in changes[:20000]], fh, indent=1)

        ledger_ok, broken, msg = self.ledger.verify()
        metrics = {
            "run_id": self.run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "aoi": {"name": cfg.aoi_name, "bbox": list(cfg.bbox)},
            "stages": {name: self.dag.results[name].report for name in self.dag.stages},
            "stage_seconds": {name: round(self.dag.results[name].seconds, 2)
                              for name in self.dag.stages},
            "outputs": {
                "harmonised_parcels": len(parcel_rows),
                "harmonised_buildings": len(building_rows),
                "changes": len(changes),
                "adjudication_cases": len(self.queue.cases),
            },
            "ledger": {
                "entries": len(self.ledger),
                "verified": ledger_ok,
                "message": msg,
                "merkle_root": self.ledger.merkle_root(),
            },
        }
        with open(os.path.join(cfg.out_dir, "metrics.json"), "w", encoding="utf-8") as fh:
            json.dump(metrics, fh, indent=2, default=str)
        with open(os.path.join(cfg.out_dir, "adjudication_queue.json"), "w",
                  encoding="utf-8") as fh:
            json.dump([c.brief() for c in
                       sorted(self.queue.cases.values(), key=lambda x: -x.priority)[:500]],
                      fh, indent=1, default=str)

        db_counts = self._publish_to_database(parcels, buildings, changes, metrics)
        if db_counts is not None:
            metrics["database"] = db_counts

        return {"parcels": parcel_rows, "buildings": building_rows, "metrics": metrics,
                "_report": {"written_to": os.path.abspath(cfg.out_dir),
                            **metrics["outputs"]}}

    def _publish_to_database(self, parcels: dict[str, Any], buildings: dict[str, Any],
                              changes: list[Any], metrics: dict[str, Any]) -> dict[str, int] | None:
        """Best-effort real PostGIS write. The file output above is always written and
        remains authoritative regardless of whether a database is reachable — this never
        raises out of a pipeline run that already succeeded at producing its file output."""
        from ..db.store import get_engine, publish_to_postgis

        engine = get_engine()
        if engine is None:
            return None
        try:
            return publish_to_postgis(
                engine, run_id=self.run_id, aoi_name=self.config.aoi_name,
                bbox=self.config.bbox, parcels=parcels, buildings=buildings,
                queue=self.queue, changes_list=[c.to_dict() for c in changes[:20000]],
                ledger=self.ledger, metrics=metrics,
            )
        except Exception as err:  # noqa: BLE001
            logger.warning("PostGIS publish failed (%s); file output is unaffected.", err)
            return None

    # -- helpers -------------------------------------------------------------------

    def _row(self, rec: dict[str, Any], kind: str) -> dict[str, Any]:
        conf = rec.get("confidence")
        row = {
            "entity_id": rec["entity_id"],
            "ulpin": rec.get("ulpin"),
            "kind": kind,
            "contributing_datasets": rec["contributing_datasets"],
            "n_sources": rec["n_sources"],
            **rec["attributes"],
        }
        if conf is not None:
            row["confidence"] = round(conf.composite, 4)
            row["confidence_grade"] = conf.grade
            for k, v in conf.components().items():
                row[f"conf_{k}"] = round(v, 4)
            row["confidence_explanation"] = conf.explain()
        row["conflict_count"] = len(rec.get("conflicts", []))
        row["ledger_head"] = self.ledger.head
        return row


# --------------------------------------------------------------------------------------
# module helpers
# --------------------------------------------------------------------------------------


def _write_geojson(path: str, records: Iterable[dict[str, Any]]) -> None:
    feats = []
    for r in records:
        conf = r.get("confidence")
        props = {
            "entity_id": r["entity_id"],
            "ulpin": r.get("ulpin"),
            "contributing_datasets": ",".join(r["contributing_datasets"]),
            "n_sources": r["n_sources"],
            "cluster_support": r.get("support"),
            "conflicts": len(r.get("conflicts", [])),
            **{k: v for k, v in r["attributes"].items() if not k.startswith("_")},
        }
        if conf is not None:
            props["confidence"] = round(conf.composite, 4)
            props["confidence_grade"] = conf.grade
            for k, v in conf.components().items():
                props[f"conf_{k}"] = round(v, 4)
        feats.append({"type": "Feature",
                      "geometry": r["geometry"].__geo_interface__,
                      "properties": props})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh)


def _geodesic_area(geom_wgs) -> float:
    try:
        if geom_wgs.geom_type == "Polygon":
            a = geodesic_area_m2(list(geom_wgs.exterior.coords))
            for r in geom_wgs.interiors:
                a -= geodesic_area_m2(list(r.coords))
            return max(a, 0.0)
        if hasattr(geom_wgs, "geoms"):
            return sum(_geodesic_area(g) for g in geom_wgs.geoms)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _max_offset(geoms: Sequence[Any]) -> float:
    if len(geoms) < 2:
        return 0.0
    cs = [g.centroid for g in geoms]
    return max(a.distance(b) for i, a in enumerate(cs) for b in cs[i + 1:])


def _normalise_value(target: str, value: Any) -> Any:
    if target == "survey_number":
        return normalise_survey_number(value) or value
    if target == "ward":
        return normalise_ward(value) or value
    if target == "zone":
        return normalise_zone(value) or value
    if isinstance(value, str):
        return value.strip()
    return value


def _extraction_conf(attrs: dict[str, Any]) -> float | None:
    v = attrs.get("confidence")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _parse_vintage(v: str) -> datetime | None:
    if not v:
        return None
    try:
        return datetime(int(str(v)[:4]), 6, 30, tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
