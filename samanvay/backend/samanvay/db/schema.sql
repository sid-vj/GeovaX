-- SAMANVAY canonical spatial schema (PostgreSQL 16 + PostGIS 3.4)
--
-- Design commitments encoded in this schema:
--
--   1. Claims are never destroyed. `source_claim` is append-only; `harmonised_*` tables
--      hold the current decision and always name the claims they supersede. A land record
--      whose history cannot be reconstructed is not a land record, it is a rumour with a
--      geometry column.
--
--   2. Geometry is stored in EPSG:4326 for interchange and in a metric CRS for
--      computation. Storing only 4326 forces every area and distance query to reproject
--      on the fly, which is both slow and — because people forget — wrong.
--
--   3. Confidence is first-class and decomposed. A single float would be unusable by the
--      officer who has to sign the record.
--
--   4. Every table that can be published carries a `ledger_head`, so any row can be tied
--      back to the hash chain that produced it.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE SCHEMA IF NOT EXISTS samanvay;
SET search_path TO samanvay, public;

-- ---------------------------------------------------------------------------------------
-- reference
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS authority (
    code            text PRIMARY KEY,
    name            text NOT NULL,
    tier            text NOT NULL CHECK (tier IN ('central','state','ulb','parastatal','institutional')),
    contact         jsonb DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS source_dataset (
    dataset_id              text PRIMARY KEY,
    title                   text NOT NULL,
    source_type             text NOT NULL,
    authority_code          text REFERENCES authority(code),
    licence                 text,
    crs                     text NOT NULL,
    acquired_on             date,
    published_on            date,
    feature_count           bigint,
    positional_accuracy_m   double precision,
    completeness            double precision CHECK (completeness BETWEEN 0 AND 1),
    uri                     text,
    checksum_sha256         char(64),
    profile                 jsonb DEFAULT '{}'::jsonb,
    lineage_parents         text[] DEFAULT '{}',
    -- lineage_parents is what stops three derivatives of one survey being counted as
    -- three independent witnesses during evidence fusion
    ingested_at             timestamptz NOT NULL DEFAULT now(),
    notes                   text
);

CREATE INDEX IF NOT EXISTS ix_source_dataset_type ON source_dataset (source_type);

-- ---------------------------------------------------------------------------------------
-- raw claims (append only)
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS source_feature (
    id                  bigserial PRIMARY KEY,
    dataset_id          text NOT NULL REFERENCES source_dataset(dataset_id),
    source_feature_id   text NOT NULL,
    feature_class       text NOT NULL,
    geom                geometry(Geometry, 4326),
    geom_metric         geometry(Geometry, 32644),
    properties          jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_on         date,
    ingested_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, source_feature_id)
);

CREATE INDEX IF NOT EXISTS ix_source_feature_geom       ON source_feature USING gist (geom);
CREATE INDEX IF NOT EXISTS ix_source_feature_geom_m     ON source_feature USING gist (geom_metric);
CREATE INDEX IF NOT EXISTS ix_source_feature_dataset    ON source_feature (dataset_id);
CREATE INDEX IF NOT EXISTS ix_source_feature_class      ON source_feature (feature_class);
CREATE INDEX IF NOT EXISTS ix_source_feature_props      ON source_feature USING gin (properties jsonb_path_ops);

CREATE TABLE IF NOT EXISTS source_claim (
    id                  bigserial PRIMARY KEY,
    fingerprint         char(16) NOT NULL,
    dataset_id          text NOT NULL REFERENCES source_dataset(dataset_id),
    source_feature_id   text NOT NULL,
    entity_id           text,
    property_path       text NOT NULL,
    value_text          text,
    value_num           double precision,
    value_geom          geometry(Geometry, 4326),
    observed_on         date,
    accuracy_m          double precision,
    extraction_confidence double precision,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_claim_entity   ON source_claim (entity_id, property_path);
CREATE INDEX IF NOT EXISTS ix_claim_dataset  ON source_claim (dataset_id);
CREATE INDEX IF NOT EXISTS ix_claim_fp       ON source_claim (fingerprint);

-- ---------------------------------------------------------------------------------------
-- matching
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS match_pair (
    id              bigserial PRIMARY KEY,
    run_id          uuid NOT NULL,
    left_dataset    text NOT NULL,
    left_id         text NOT NULL,
    right_dataset   text NOT NULL,
    right_id        text NOT NULL,
    probability     double precision NOT NULL,
    cardinality     text NOT NULL,
    group_id        text,
    accepted        boolean NOT NULL DEFAULT false,
    features        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_match_run   ON match_pair (run_id);
CREATE INDEX IF NOT EXISTS ix_match_left  ON match_pair (left_dataset, left_id);
CREATE INDEX IF NOT EXISTS ix_match_right ON match_pair (right_dataset, right_id);

-- ---------------------------------------------------------------------------------------
-- harmonised output
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS harmonised_parcel (
    entity_id               text PRIMARY KEY,
    ulpin                   char(14) UNIQUE,
    geom                    geometry(MultiPolygon, 4326) NOT NULL,
    geom_metric             geometry(MultiPolygon, 32644) NOT NULL,

    survey_number           text,
    subdivision             text,
    patta_number            text,

    state_lgd               text,
    district_lgd            text,
    taluk_lgd               text,
    village_lgd             text,
    village_name            text,
    ulb_code                text,
    ward                    text,
    zone                    text,
    locality                text,
    street                  text,

    tenure_type             text,
    owner_name_hash         char(64),   -- DPDP: the plaintext lives in a separate,
    owner_name_encrypted    bytea,      -- access-controlled store, never in the open table
    land_use                text,
    is_public_land          boolean DEFAULT false,

    recorded_extent_m2      double precision,
    computed_extent_m2      double precision NOT NULL,
    extent_discrepancy_pct  double precision
        GENERATED ALWAYS AS (
            CASE WHEN recorded_extent_m2 IS NULL OR recorded_extent_m2 = 0 THEN NULL
                 ELSE 100.0 * (computed_extent_m2 - recorded_extent_m2) / recorded_extent_m2
            END
        ) STORED,

    building_count          integer DEFAULT 0,
    built_up_area_m2        double precision DEFAULT 0,
    ground_coverage_pct     double precision,
    max_height_m            double precision,

    confidence              double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    confidence_grade        char(1) NOT NULL CHECK (confidence_grade IN ('A','B','C','D','E')),
    conf_positional         double precision,
    conf_source_agreement   double precision,
    conf_topological        double precision,
    conf_attribute          double precision,
    conf_temporal           double precision,
    conf_lineage            double precision,

    contributing_datasets   text[] NOT NULL DEFAULT '{}',
    conflict_count          integer NOT NULL DEFAULT 0,
    adjudication_state      text NOT NULL DEFAULT 'auto_resolved',
    change_type             text NOT NULL DEFAULT 'no_change',

    extra_attributes        jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Any canonical parcel field (attributes/canonical.py: PARCEL_SCHEMA) without its own
    -- typed column above, so the database-backed store is lossless relative to the
    -- flat-file GeoJSON output rather than silently dropping fields as the schema evolves.

    ledger_head             char(64),
    valid_from              timestamptz NOT NULL DEFAULT now(),
    valid_to                timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_parcel_geom       ON harmonised_parcel USING gist (geom);
CREATE INDEX IF NOT EXISTS ix_parcel_geom_m     ON harmonised_parcel USING gist (geom_metric);
CREATE INDEX IF NOT EXISTS ix_parcel_ward       ON harmonised_parcel (ulb_code, ward);
CREATE INDEX IF NOT EXISTS ix_parcel_village    ON harmonised_parcel (village_lgd);
CREATE INDEX IF NOT EXISTS ix_parcel_survey     ON harmonised_parcel (village_lgd, survey_number);
CREATE INDEX IF NOT EXISTS ix_parcel_grade      ON harmonised_parcel (confidence_grade);
CREATE INDEX IF NOT EXISTS ix_parcel_survey_trgm ON harmonised_parcel USING gin (survey_number gin_trgm_ops);

CREATE TABLE IF NOT EXISTS harmonised_building (
    entity_id           text PRIMARY KEY,
    parcel_ulpin        char(14) REFERENCES harmonised_parcel(ulpin) ON DELETE SET NULL,
    geom                geometry(MultiPolygon, 4326) NOT NULL,
    geom_metric         geometry(MultiPolygon, 32644) NOT NULL,
    door_number         text,
    ward                text,
    zone                text,
    locality            text,
    street              text,
    building_use        text,
    construction_type   text,
    floors              text,
    footprint_area_m2   double precision NOT NULL,
    max_height_m        double precision,
    estimated_floors    integer,
    extraction_confidence double precision,
    confidence          double precision NOT NULL,
    confidence_grade    char(1) NOT NULL,
    contributing_datasets text[] NOT NULL DEFAULT '{}',
    change_type         text NOT NULL DEFAULT 'no_change',
    extra_attributes    jsonb NOT NULL DEFAULT '{}'::jsonb,
    ledger_head         char(64),
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_building_geom   ON harmonised_building USING gist (geom);
CREATE INDEX IF NOT EXISTS ix_building_parcel ON harmonised_building (parcel_ulpin);

-- ---------------------------------------------------------------------------------------
-- conflicts, resolutions, adjudication
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS conflict (
    conflict_id     text PRIMARY KEY,
    entity_id       text NOT NULL,
    kind            text NOT NULL,
    property_path   text NOT NULL,
    severity        double precision NOT NULL,
    disagreement    double precision NOT NULL,
    competing       text[] NOT NULL DEFAULT '{}',
    detected_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_conflict_entity ON conflict (entity_id);
CREATE INDEX IF NOT EXISTS ix_conflict_sev    ON conflict (severity DESC);

CREATE TABLE IF NOT EXISTS resolution (
    id              bigserial PRIMARY KEY,
    conflict_id     text,
    entity_id       text NOT NULL,
    property_path   text NOT NULL,
    chosen_value    text,
    strategy        text NOT NULL,
    belief          double precision NOT NULL,
    plausibility    double precision NOT NULL,
    state           text NOT NULL,
    rationale       text NOT NULL,
    superseded      char(16)[] NOT NULL DEFAULT '{}',
    resolved_by     text NOT NULL DEFAULT 'samanvay/auto',
    resolved_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_resolution_entity ON resolution (entity_id, property_path);

CREATE TABLE IF NOT EXISTS adjudication_case (
    case_id         text PRIMARY KEY,
    entity_id       text NOT NULL,
    property_path   text NOT NULL,
    conflict_id     text REFERENCES conflict(conflict_id),
    priority        double precision NOT NULL,
    batch_key       text NOT NULL,
    state           text NOT NULL DEFAULT 'queued',
    assigned_to     text,
    decided_value   text,
    decided_by      text,
    decided_at      timestamptz,
    decision_note   text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_adj_state ON adjudication_case (state, priority DESC);
CREATE INDEX IF NOT EXISTS ix_adj_batch ON adjudication_case (batch_key);

-- ---------------------------------------------------------------------------------------
-- change and genealogy
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS change_record (
    id                  bigserial PRIMARY KEY,
    entity_id           text NOT NULL,
    epoch_from          date,
    epoch_to            date,
    change_type         text NOT NULL,
    confidence          double precision NOT NULL,
    area_before_m2      double precision,
    area_after_m2       double precision,
    centroid_shift_m    double precision,
    residual_shift_m    double precision,
    height_delta_m      double precision,
    registry_action     text,
    is_actionable       boolean NOT NULL DEFAULT true,
    evidence            jsonb DEFAULT '[]'::jsonb,
    geom                geometry(Geometry, 4326),
    detected_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_change_entity ON change_record (entity_id);
CREATE INDEX IF NOT EXISTS ix_change_type   ON change_record (change_type);
CREATE INDEX IF NOT EXISTS ix_change_geom   ON change_record USING gist (geom);

CREATE TABLE IF NOT EXISTS parcel_genealogy (
    id              bigserial PRIMARY KEY,
    child_ulpin     char(14) NOT NULL,
    parent_ulpin    char(14) NOT NULL,
    operation       text NOT NULL CHECK (operation IN
                        ('subdivision','amalgamation','boundary_adjustment')),
    effective_from  date NOT NULL,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (child_ulpin, parent_ulpin, operation)
);

CREATE INDEX IF NOT EXISTS ix_genealogy_child  ON parcel_genealogy (child_ulpin);
CREATE INDEX IF NOT EXISTS ix_genealogy_parent ON parcel_genealogy (parent_ulpin);

-- ---------------------------------------------------------------------------------------
-- provenance ledger mirror (the authoritative copy is the append-only JSONL file)
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS provenance_ledger (
    idx             bigint PRIMARY KEY,
    ts              timestamptz NOT NULL,
    entity_id       text NOT NULL,
    operation       text NOT NULL,
    actor           text NOT NULL,
    payload         jsonb NOT NULL,
    prev_hash       char(64) NOT NULL,
    entry_hash      char(64) NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS ix_ledger_entity ON provenance_ledger (entity_id, idx);

-- ---------------------------------------------------------------------------------------
-- inter-departmental exchange
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS subscription (
    id              bigserial PRIMARY KEY,
    subscriber      text NOT NULL,
    authority_code  text REFERENCES authority(code),
    aoi             geometry(Polygon, 4326),
    feature_classes text[] NOT NULL DEFAULT '{}',
    change_types    text[] NOT NULL DEFAULT '{}',
    min_confidence  double precision NOT NULL DEFAULT 0.0,
    webhook_url     text,
    active          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_subscription_aoi ON subscription USING gist (aoi);

CREATE TABLE IF NOT EXISTS delivery_log (
    id              bigserial PRIMARY KEY,
    subscription_id bigint REFERENCES subscription(id),
    entity_id       text NOT NULL,
    change_type     text,
    status          text NOT NULL,
    http_status     integer,
    attempted_at    timestamptz NOT NULL DEFAULT now(),
    response        text
);

-- ---------------------------------------------------------------------------------------
-- pipeline runs
-- ---------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id          uuid PRIMARY KEY,
    aoi_name        text NOT NULL,
    aoi             geometry(Polygon, 4326),
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text NOT NULL DEFAULT 'running',
    stage_reports   jsonb NOT NULL DEFAULT '{}'::jsonb,
    metrics         jsonb NOT NULL DEFAULT '{}'::jsonb,
    queue_briefs    jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- Pre-rendered adjudication-queue briefs (AdjudicationCase.brief()), mirroring
    -- adjudication_queue.json in the file-backed store — the structured case rows live in
    -- adjudication_case, this is the display-ready form the API/console reads directly.
    changes         jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- Pre-rendered change records (ChangeRecord.to_dict()), mirroring changes.json.
    ledger_root     char(64),
    software_version text
);

-- ---------------------------------------------------------------------------------------
-- views for the API and the console
-- ---------------------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_parcel_public AS
SELECT
    p.entity_id, p.ulpin, p.geom,
    p.survey_number, p.village_lgd, p.village_name, p.ward, p.zone, p.locality,
    p.land_use, p.is_public_land,
    round(p.computed_extent_m2::numeric, 2)  AS computed_extent_m2,
    round(p.recorded_extent_m2::numeric, 2)  AS recorded_extent_m2,
    round(p.extent_discrepancy_pct::numeric, 2) AS extent_discrepancy_pct,
    p.building_count, p.built_up_area_m2, p.ground_coverage_pct, p.max_height_m,
    round(p.confidence::numeric, 4) AS confidence, p.confidence_grade,
    p.contributing_datasets, p.conflict_count, p.adjudication_state, p.change_type,
    p.ledger_head
FROM harmonised_parcel p
WHERE p.valid_to IS NULL;
-- owner_name is deliberately absent: personal data is served only through the
-- purpose-bound endpoint, never through the general feature API

CREATE OR REPLACE VIEW v_quality_by_ward AS
SELECT
    ulb_code, ward,
    count(*)                                              AS parcels,
    round(avg(confidence)::numeric, 4)                    AS mean_confidence,
    count(*) FILTER (WHERE confidence_grade IN ('A','B')) AS publishable,
    count(*) FILTER (WHERE confidence_grade IN ('D','E')) AS needs_field_check,
    sum(conflict_count)                                   AS conflicts,
    round(avg(abs(extent_discrepancy_pct))::numeric, 2)   AS mean_abs_extent_gap_pct,
    round(sum(computed_extent_m2)::numeric / 10000.0, 2)  AS total_hectares
FROM harmonised_parcel
WHERE valid_to IS NULL
GROUP BY ulb_code, ward;

CREATE OR REPLACE VIEW v_encroachment_candidates AS
SELECT
    c.entity_id, c.change_type, c.confidence, c.area_after_m2, c.geom,
    p.ulpin, p.survey_number, p.ward, p.land_use
FROM change_record c
LEFT JOIN harmonised_parcel p ON ST_Intersects(p.geom, c.geom)
WHERE c.change_type = 'encroachment'
   OR (c.change_type = 'new_construction' AND p.is_public_land);
