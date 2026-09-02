# Deployment

## 1. Quick start (evaluation)

```bash
git clone <repo> && cd samanvay
make setup            # python deps + PostGIS schema
make data             # fetch the real corpus (~2.1 GB) and clip to the AOI
make pipeline         # run the harmonisation DAG
make api              # OGC API + platform API on :8000
make ui               # the console at http://localhost:8000/map
```

Runs on a single machine. The reference run in `docs/08-evaluation-results.md` was produced
on **2 vCPU / 7 GB RAM**, which is deliberately modest: a system that needs a data centre to
harmonise one ward cannot be deployed to four thousand ULBs.

## 2. Runtime footprint

| Stage | Cost driver | Memory |
|---|---|---|
| Ingest | Streaming JSONL with a textual bbox pre-screen | ~40 MB regardless of file size |
| Reproject | One PROJ transform per geometry | O(features) |
| Topology | STRtree pairwise scan + union for the gap check | Highest stage; scales with parcel count |
| Match | Blocking then two-stage feature extraction | O(candidate pairs) |
| Resolve → publish | Per-entity, streaming | Low |

The whole pipeline is **deterministic per AOI tile and tiles share nothing** except the
canonical store, so a state-level run is an embarrassingly parallel map over tiles with a
final merge. Horizontal scaling is by AOI, not by sharding a single graph.

## 3. Production topology

```
              ┌───────────── District / ULB ─────────────┐
              │  Web-GIS console      Field PWA (offline) │
              └───────────────┬──────────────────────────┘
                              │ HTTPS
        ┌─────────────────────┴──────────────────────┐
        │            State data centre                │
        │  nginx → FastAPI (N replicas)               │
        │  Celery workers (M replicas)  ←→  Redis     │
        └───────┬──────────────────────┬──────────────┘
                │                      │
    ┌───────────▼──────────┐  ┌────────▼─────────────┐
    │ PostgreSQL + PostGIS │  │ Object store (COG,   │
    │ (primary + replica)  │  │ STAC, ledger backup) │
    └──────────────────────┘  └──────────────────────┘
                │
    ┌───────────▼───────────────────────────────────┐
    │ Other departments: OGC API / webhook delivery │
    └───────────────────────────────────────────────┘
```

### Sizing guidance

| Deployment | Parcels | API | Workers | PostGIS |
|---|---|---|---|---|
| Single ULB pilot | < 500 k | 1 × 2 vCPU / 4 GB | 2 × 4 vCPU / 8 GB | 4 vCPU / 16 GB / 200 GB SSD |
| District | 2–5 M | 2 × 4 vCPU / 8 GB | 4 × 8 vCPU / 16 GB | 8 vCPU / 32 GB / 1 TB NVMe |
| State (Tamil Nadu, 6 M parcels) | 6 M+ | 4 × 4 vCPU / 8 GB | 8–16 × 8 vCPU / 16 GB | 16 vCPU / 64 GB / 4 TB NVMe + replica |

PostGIS is the sizing constraint, not the workers. Spatial indexes on six million parcels
want RAM; the pipeline itself is CPU-bound and horizontally trivial.

## 4. docker-compose (reference)

```yaml
services:
  db:
    image: postgis/postgis:16-3.4
    environment: [POSTGRES_DB=samanvay, POSTGRES_PASSWORD=${DB_PASSWORD}]
    volumes: ["pgdata:/var/lib/postgresql/data",
              "./backend/samanvay/db/schema.sql:/docker-entrypoint-initdb.d/10-schema.sql"]
    healthcheck: {test: ["CMD-SHELL", "pg_isready -U postgres"], interval: 10s}

  redis:
    image: redis:7-alpine

  api:
    build: ./backend
    command: uvicorn samanvay.api.app:app --host 0.0.0.0 --port 8000
    environment: [SAMANVAY_OUT=/data/out, DATABASE_URL=postgresql://postgres:${DB_PASSWORD}@db/samanvay]
    volumes: ["./data:/data"]
    depends_on: {db: {condition: service_healthy}}
    ports: ["8000:8000"]

  worker:
    build: ./backend
    command: celery -A samanvay.pipeline.tasks worker -l info -c 4
    environment: [REDIS_URL=redis://redis:6379/0]
    volumes: ["./data:/data"]
    depends_on: [redis, db]

volumes: {pgdata: {}}
```

## 5. Offline and low-connectivity operation

This matters more than it usually does. A tahsildar's office may have an intermittent link,
and a field verification team has none.

* **No external service dependency at inference time.** The matcher, the resolver and the
  scorer run entirely locally. Nothing calls out to a hosted model.
* **The ledger is a plain JSON-lines file**, verifiable with a text editor and `sha256sum`.
  It survives a database restore and can be carried on a USB stick.
* **The field PWA** caches an AOI's features and the adjudication cases assigned to a
  surveyor, records decisions locally, and syncs when a link returns. Decisions carry the
  surveyor's identity and a timestamp and enter the ledger on sync.
* **Tile-parallel runs** mean a district can be processed in slices as capacity allows,
  rather than needing one long uninterrupted job.

## 6. Onboarding a new department

The design goal is that adding the eleventh source costs the same as adding the second.

1. Register the dataset — authority, licence, CRS, declared accuracy, vintage, lineage
   parents. Lineage parents matter: they are what stops three derivatives of one survey
   being counted as three independent witnesses.
2. Point the connector at the file or service. Formats supported out of the box: GeoJSON,
   GeoJSON-Lines, Shapefile, GeoPackage, KML, FlatGeobuf, GeoParquet, WKT-CSV, XYZ tile
   pyramids, RINEX, NMEA.
3. Run `schema_map`. The crosswalk is proposed automatically with per-signal evidence;
   an officer confirms it once. Mappings below the acceptance threshold are surfaced for
   review rather than applied silently.
4. Add the new dataset to a matching pair in the pipeline configuration.
5. Run. The reliability prior starts from the source-type table and self-corrects from
   adjudication decisions over the campaign.

There is no code to write for step 2 unless the format is genuinely novel.

## 7. Operating the confidence thresholds

The grade boundaries and the specification they are measured against are **deployment
parameters, not truths**, and they should be set deliberately:

| Parameter | Default | Set it from |
|---|---|---|
| `target_accuracy_m` | 0.5 m | The survey specification the deployment is held to (NAKSHA urban is 0.5 m; a rural re-survey may be 2 m) |
| Grade A / B thresholds | 0.90 / 0.75 | The department's tolerance for publishing without review |
| `conflict_escalation_threshold` | 0.55 | Available officer capacity — this is the dial that sets queue volume |
| `snap_tolerance_m` | 0.15 m | Larger than digitising noise, smaller than any real boundary in the fabric |
| `max_vertex_shift_m` | 0.50 m | The point beyond which a correction becomes a re-survey |

Changing `target_accuracy_m` changes every grade in the output. That is intended: a grade is
a statement *relative to a specification*, and the specification belongs to the department.

## 8. Monitoring

Ship these, and nothing else will surprise you:

* **Ledger verification** on a schedule — `GET /api/verify` must return `verified: true`.
* **Planar-partition error per run.** A jump means an upstream layer changed CRS or datum.
* **Adjudication queue depth and age.** A growing queue means the escalation threshold is
  set beyond available capacity.
* **Agreement rate between officers and the platform** (`AdjudicationQueue.stats`). A
  falling rate means the priors have drifted from practice and need the feedback loop run.
* **Per-source empirical reliability** versus its prior. A source diverging from its prior
  is a data-quality regression at the custodian, and is worth telling them about.

## 9. Backup and recovery

| Asset | Method | RPO |
|---|---|---|
| PostGIS | Streaming replication + nightly `pg_dump` | < 1 min |
| Ledger | Append-only file, replicated on write; Merkle root anchored daily | 0 |
| COGs / rasters | Object store versioning | < 1 h |
| Adjudication decisions | JSON-lines alongside the ledger | 0 |

The ledger is the asset that cannot be reconstructed. Everything else can be recomputed by
re-running the pipeline over the source corpus, which is exactly why the pipeline is
deterministic and the AOI manifest carries a checksum per input.
