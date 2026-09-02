# SAMANVAY — SIH 2026 · PS 26013 · delivery index

*Automated Integration and Intelligent Harmonisation of Multi-source Geospatial Data for
Urban Land Record Management · Ministry of Rural Development / Department of Land Resources.*

Everything below was produced from **real, openly licensed Indian government data**. There
are no synthetic features anywhere in the corpus, the evaluation or the demo.

---

## Start here — in this order

| # | File | What it is |
|---|---|---|
| 1 | **`samanvay-demo.html`** | **Double-click it.** The jury demo: a real run, inlined into one file, no server, no install. Click any parcel or building to see its sources, conflicts and six-dimension confidence. |
| 2 | `SAMANVAY_PS26013.pdf` / `.pptx` | The 15-slide pitch deck. PDF for reading, PPTX to edit. |
| 3 | `README.md` | The one-page argument: the result first, then the problem, then what is built. |
| 4 | `docs/08-evaluation-results.md` | Every measured number — **and what the evaluation does not establish.** |
| 5 | `samanvay-source.tar.gz` | The whole platform: ~12,700 lines of Python, 30 modules, 81 passing tests. |

---

## The headline

One 17-minute run on 2 vCPU over 22 km² of central Chennai:

| | |
|---|---:|
| Harmonised parcels and buildings | **107,262** |
| Systematic offset recovered between two departments' layers | **1.51 m @ 073°** |
| Candidate pairs eliminated by blocking | **99.988%** |
| Conflicts auto-resolved (the rest queued, not guessed) | **43.4%** |
| Subdivisions and amalgamations detected | **1,854** |
| ULPINs minted, all unique | **14,702** |
| Provenance ledger | verified, Merkle-anchored |
| Tests | **81 passing** |

And the finding that matters:

> **No feature in central Chennai reaches grade A or B against NAKSHA's 0.5 m urban
> specification.** The best available source is a 1 m municipal survey and 68% of features are
> corroborated by a single source. The platform's job is to quantify that gap parcel by
> parcel — it is a plan for where to fly the drones first.

A platform that reported this data as fine would be more impressive and less useful.

---

## Everything in this folder

### Deliverable 1 — jury demo prototype

- **`samanvay-demo.html`** — self-contained console. Real harmonised parcels and buildings
  from the run, the live adjudication queue with the platform's own reasoning, per-village and
  per-ward quality, the full 12-stage run record with the ledger's Merkle root, and the change
  histogram. Opens offline in any browser; nothing is fetched.

### Deliverable 2 — pitch deck / idea submission

- **`SAMANVAY_PS26013.pptx`** — 15 slides, editable.
- **`SAMANVAY_PS26013.pdf`** — the same deck, fixed layout.

### Deliverable 3 — architecture and solution document

`docs/` — nine documents:

| File | Contents |
|---|---|
| `00-solution-overview.md` | What the problem really is, and how the solution answers it |
| `01-architecture.md` | Layers, components, data flow, deployment, failure behaviour |
| `02-data-sources.md` | Every dataset: authority, licence, extent, vintage, SHA-256 |
| `03-algorithms.md` | Each algorithm, why the obvious alternative fails, how it is validated |
| `04-api.md` | Every endpoint — OGC API - Features and platform API — and what is deliberately absent |
| `05-standards-compliance.md` | DILRMP, NAKSHA, LGD, DPDP, OGC, ISO — and what it will *not* do |
| `06-deployment.md` | Sizing, topology, offline operation, monitoring, onboarding |
| `07-security-governance.md` | Threat model, DPDP handling, ledger integrity, refusals encoded in code |
| `08-evaluation-results.md` | Every measured number, and the limits of the evaluation |
| `09-roadmap.md` | Built vs designed-not-built, what a deployment needs next, what would make it fail |

### Deliverable 4 — working codebase

- **`samanvay-source.tar.gz`** — the complete repository.

```bash
tar xzf samanvay-source.tar.gz && cd samanvay
make setup      # dependencies + PostGIS schema
make data       # fetch the real corpus (~2.1 GB), clip to the AOI, checksum it
make pipeline   # run the 12-stage harmonisation DAG   (~18 min on 2 vCPU)
make lod1       # build the CityJSON 3-D city model
make terrain    # DSM -> DTM -> nDSM -> structures on a calibrated float DSM
make raster     # ORI rebuild, co-registration, null-change experiment
make api        # OGC API + platform API on :8000, console at /map
make demo       # rebuild samanvay-demo.html from the latest run
make test       # 81 tests
```

### Run outputs (real, from the run the demo and the docs report)

| Archive | Contents |
|---|---|
| `samanvay-results.tar.gz` | `metrics.json` (the full run record), `lod1_report.json`, `ledger.jsonl` (hash-chained provenance), `adjudication_queue.json` + `.geojson`, `changes.json`, `harmonised_parcels.geojson` |
| `samanvay-buildings.tar.gz` | `harmonised_buildings.geojson` — 92,560 harmonised footprints |
| `samanvay-lod1-cityjson.tar.gz` | `city_model_lod1.city.json` — CityGML LOD1 solids as CityJSON 1.1 |
| `samanvay-raster-terrain.tar.gz` | Terrain (DSM→DTM→nDSM) and ORI / co-registration reports |

Verify the provenance ledger yourself, without trusting anything here:

```bash
cd samanvay && samanvay verify --out <extracted results dir>
```

---

## What the problem statement asked for, and where it is

| PS requirement | Where |
|---|---|
| Drone imagery, ORI, DSM/DTM | `raster/`, `ingest/tiles.py` — real UAV flights, 3 reconstructions; ORI rebuilt at 0.051 m/px |
| Existing cadastral maps | `ingest/vector.py` — 6.02 M TNGIS + 220 k NCSCM parcels |
| Revenue records | `attributes/canonical.py` — survey numbers, LGD hierarchy, FMB flags |
| Municipal GIS layers | `attributes/schema_match.py` — 964 k GCC footprints with ward/zone/locality |
| Utility network data | `topology/validate.py` — dangle, undershoot, connectivity rules |
| Ground truthing | `crs/gcp.py`, `quality/accuracy.py` — blunder detection, CE90/LE90, Moran's I |
| GNSS / CORS | `ingest/gnss.py` — RINEX, NMEA, CSV, with uncertainty carried through |
| Building footprints | Three independent real sources reconciled |
| **AI/ML spatial matching** | `matching/` — weak-supervised + self-trained gradient boosting |
| **Automated topology correction** | `topology/repair.py` — bounded, audited, and able to refuse |
| **Intelligent attribute mapping** | `attributes/schema_match.py` — four-signal automatic crosswalk |
| **Geo-referencing / CRS engine** | `crs/engine.py`, `crs/gcp.py` — Everest/Kalianpur datums, GCP model ladder |
| **Change detection** | `change/`, `geoai/change.py` — typed, offset-aware, three-signal raster |
| **Spatial conflict resolution** | `conflict/` — Dempster–Shafer + statutory rules + ranked human queue |
| **Confidence scoring** | `confidence/scorer.py` — six dimensions, explainable, graded A–E |

Beyond the statement: ULPIN (Bhu-Aadhaar) minting with split/merge genealogy; a hash-chained
provenance ledger with Merkle inclusion proofs a citizen can check independently; encroachment
*findings* restricted to government land; area reconciliation in Indian customary units;
an active-learning loop that updates each source's empirical reliability from every human
decision; a LOD1 3-D city model with FSI and ground-coverage checks; an inter-departmental
subscription bus; and DPDP-Act-aware PII handling with owner data absent from the feature API
by construction.

---

## Licence

Apache-2.0. Datasets retain their upstream licences (CC0-1.0 / CC-BY-4.0 / ODbL / dl-de-by-2.0);
`data/aoi/manifest.json` in the source archive records authority, licence, vintage and SHA-256
for every one.
