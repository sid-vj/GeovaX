# SAMANVAY

*Sanskrit: harmonisation.*

> An AI-enabled geospatial integration platform that automatically integrates, harmonises,
> validates and synchronises multi-source land-related datasets with AI-generated feature
> extraction outputs — and shows its working.

**Smart India Hackathon 2026 · Problem Statement 26013**
Ministry of Rural Development · Department of Land Resources (DoLR) · Theme: Smart Automation

---

## The result, first

One 17-minute run on 2 vCPU over 22 km² of central Chennai, on **real Indian government
data with no synthetic features anywhere**:

| | |
|---|---:|
| Harmonised parcels and buildings | **107,262** |
| Systematic offset detected between two departments' layers | **1.51 m @ 073°** |
| Candidate pairs eliminated by blocking | **99.988%** |
| Matcher AUC / expected calibration error | 1.000 / 0.0002 |
| Conflicts auto-resolved | 43.4% |
| Subdivisions and amalgamations detected | 1,854 |
| ULPINs minted, all unique | 14,702 |
| Provenance ledger | verified, Merkle-anchored |
| Tests | **81 passing** |

And the finding that matters:

> **No feature in central Chennai reaches grade A or B against NAKSHA's 0.5 m urban
> specification** — the best available source is a 1 m municipal survey, and 68% of features
> are corroborated by only one source. The platform quantifies that gap parcel by parcel.
> It is a plan for where to fly the drones first.

Full numbers, including what the evaluation does *not* establish:
**[`docs/08-evaluation-results.md`](docs/08-evaluation-results.md)**

---

## The problem, restated

For any piece of urban ground in India there are between four and ten authoritative-looking
geometries, and **they disagree**.

| Producer | Artefact | Typical error | Cadence |
|---|---|---|---|
| Drone survey / NAKSHA | ORI, DSM, DTM | 3–10 cm | one campaign |
| GeoAI feature extraction | building / parcel polygons | 0.3–2 m | per campaign |
| Municipal corporation GIS | ward, road, property | 1–5 m | continuous |
| Revenue department | cadastral FMB / survey numbers | 5–30 m | decadal |
| Utility agencies | water, sewer, power | 1–10 m | continuous |
| GNSS / CORS | control | 1–3 cm | campaign |

None of them is simply wrong. Today a GIS analyst reconciles them by hand — slowly,
irreproducibly, and without preserving why any decision was made.

**This is not an ETL problem.** ETL assumes the right answer is in the sources and the job
is to move it. Here the job is to *adjudicate*, defensibly, at scale.

---

## Three ideas the whole system rests on

**Claims, not records.** Nothing entering the platform is treated as fact. A source asserts
a claim; claims are never deleted, only superseded by a resolution that names them. So any
decision can be re-examined years later, and a policy change can be replayed over the same
claims without re-ingesting anything.

**Belief and plausibility, not probability.** Dempster-Shafer evidence theory lets mass sit
on *unknown*, which is the correct state when only one department has an opinion. The gap
between belief and plausibility is the platform's measure of its own ignorance, and it is
what decides whether a human is needed. Conflict mass is retained, not normalised away —
high conflict is the signal, not noise to be divided out.

**The platform never invents a boundary.** Where sources disagree it selects one source's
*actually observed* boundary by evidence. An averaged boundary is attributable to no survey,
can be asked of no surveyor, and is indefensible in a dispute.

---

## Everything the problem statement asks for

| PS requirement | Module | Demonstrated on |
|---|---|---|
| Drone imagery | `ingest.tiles` | Real UAV flights, 3 independent reconstructions |
| Orthorectified Imagery (ORI) | `raster.cog`, `raster.coreg` | ORI rebuilt at 0.051 m/px; co-registration measured |
| DSM / DTM datasets | `raster.terrain` | Real calibrated float DSM → DTM → nDSM |
| Existing cadastral maps | `ingest.vector` | 6.02 M TNGIS parcels + 220 k NCSCM parcels |
| Revenue records | `attributes.canonical` | Survey numbers, LGD hierarchy, FMB flags |
| Municipal GIS layers | `attributes.schema_match` | 964 k GCC footprints with ward/zone/locality |
| Utility network data | `topology.validate_network` | Dangle, undershoot, connectivity rules |
| Ground truthing (GT) | `crs.gcp`, `quality.accuracy` | Blunder detection, CE90/LE90, Moran's I |
| GNSS / CORS survey data | `ingest.gnss` | RINEX, NMEA, CSV — uncertainty carried through |
| Building footprint datasets | 3 independent real sources | GCC survey + Google Open Buildings + AMRUT |
| **AI/ML spatial matching** | `matching.*` | Weak-supervised + self-trained GBM |
| **Automated topology correction** | `topology.repair` | Bounded, audited, refusable |
| **Intelligent attribute mapping** | `attributes.schema_match` | 4-signal automatic crosswalk |
| **Geo-referencing & CRS engine** | `crs.engine`, `crs.gcp` | Everest/Kalianpur datums, GCP model ladder |
| **Change detection** | `change.*`, `geoai.change` | Typed, offset-aware, 3-signal raster |
| **Spatial conflict resolution** | `conflict.*` | Dempster-Shafer + statutory rules + queue |
| **Confidence scoring** | `confidence.scorer` | 6 dimensions, explainable, graded A–E |

### Beyond the problem statement

1. **ULPIN (Bhu-Aadhaar) minting** — stable under re-survey, unique by construction,
   checksum catching 100% of single-character errors, with split/merge genealogy so a
   mutation never orphans an encumbrance history.
2. **Hash-chained provenance ledger with Merkle inclusion proofs** — a citizen can verify
   their own record against a gazette-published root, independently of the department.
3. **Encroachment intelligence** — change detection restricted to poramboke/government land,
   reported as *findings for verification*, never as determinations.
4. **Area reconciliation** in Indian customary units (acre-cent, ground, kuzhi, guntha,
   kanal) with recorded-vs-geodesic discrepancy as a first-class field.
5. **Active-learning loop** — every adjudication becomes a training example and a Beta
   posterior update to that source's empirical reliability.
6. **LOD1 3-D city model** (CityJSON 1.1) with floor-space-index and ground-coverage checks
   against development-control limits.
7. **Inter-departmental subscription bus** — webhooks filtered by AOI, feature class, change
   type and confidence.
8. **DPDP-Act-aware PII handling** — owner data absent from the feature API by construction,
   reachable only through a purpose-bound, ledger-logged endpoint.

---

## Repository layout

```
samanvay/
├── backend/samanvay/        the platform — 12,700 lines of Python, 30 modules
│   ├── core/                domain model, ULPIN, provenance ledger, source registry
│   ├── crs/                 coordinate transformation + GCP rubber-sheeting
│   ├── ingest/              format connectors, tile mosaicking, GNSS
│   ├── db/                  PostGIS canonical schema
│   ├── topology/            validation + bounded automated repair
│   ├── matching/            blocking, features, learned matcher, global assignment
│   ├── attributes/          schema matching, Indic transliteration, record linkage
│   ├── raster/              COG, terrain (DSM→DTM), co-registration
│   ├── geoai/               footprint regularisation, raster change, LOD1
│   ├── change/              vector change detection and typing
│   ├── conflict/            evidence fusion, resolution, adjudication queue
│   ├── confidence/          explainable confidence scoring
│   ├── quality/             positional accuracy, CE90/LE90, QA reports
│   ├── pipeline/            DAG orchestration + the harmonisation pipeline
│   └── api/                 FastAPI + OGC API - Features + platform API
├── data_acquisition/        reproducible real-data fetchers with checksums
├── frontend/                the harmonisation console (MapLibre/Leaflet)
├── deck/                    SIH pitch deck generator + built .pptx
├── docs/                    solution, architecture, algorithms, standards, evaluation
├── scripts/                 pipeline, raster tier, terrain, LOD1 runners
└── tests/                   81 unit tests + 6 real-corpus integration tests
```

---

## Quick start

```bash
make setup       # dependencies + PostGIS schema
make data        # fetch the real corpus (~2.1 GB), clip to the AOI, checksum it
make pipeline    # run the harmonisation DAG        (~18 min on 2 vCPU)
make lod1        # build the CityJSON 3-D model
make terrain     # DSM → DTM → nDSM → structures on a calibrated float DSM
make raster      # ORI rebuild, co-registration, null change experiment
make api         # OGC API + platform API on :8000, console at /map
make test        # 81 tests
```

---

## Documentation

| | |
|---|---|
| [`00-solution-overview.md`](docs/00-solution-overview.md) | What the problem really is and how the solution answers it |
| [`01-architecture.md`](docs/01-architecture.md) | Layers, components, data flow, deployment, failure behaviour |
| [`02-data-sources.md`](docs/02-data-sources.md) | Every dataset: authority, licence, extent, checksum |
| [`03-algorithms.md`](docs/03-algorithms.md) | Each algorithm, why the obvious alternative fails, how it is validated |
| [`04-api.md`](docs/04-api.md) | Every endpoint — OGC API - Features and the platform API — and what is deliberately absent |
| [`05-standards-compliance.md`](docs/05-standards-compliance.md) | DILRMP, NAKSHA, LGD, DPDP, OGC, ISO — and what it will *not* do |
| [`06-deployment.md`](docs/06-deployment.md) | Sizing, topology, offline operation, monitoring, onboarding |
| [`07-security-governance.md`](docs/07-security-governance.md) | Threat model, DPDP handling, ledger integrity, and what the platform refuses to assert |
| [`08-evaluation-results.md`](docs/08-evaluation-results.md) | Every measured number, and what the evaluation does not establish |
| [`09-roadmap.md`](docs/09-roadmap.md) | Built vs designed-not-built, what a deployment needs next, and what would make it fail |

---

## Data provenance

Every byte of demonstration data is real, openly licensed government or institutional data:
TNGIS and NCSCM cadastrals, Greater Chennai Corporation building survey, Google Open
Buildings v3, AMRUT/Bhuvan footprints, OpenDroneMap UAV corpus, Geobasis NRW DOM1.
`data/aoi/manifest.json` records the authority, licence, upstream service, vintage and
SHA-256 for each.

Synthetic data would have made every number in the evaluation better and none of them
meaningful — a matcher tuned on generated offsets learns the generator.

## Licence

Apache-2.0. Datasets retain their upstream licences (CC0-1.0 / CC-BY-4.0 / ODbL / dl-de-by-2.0).
