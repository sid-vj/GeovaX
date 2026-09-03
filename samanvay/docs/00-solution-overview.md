# SAMANVAY — solution overview

**Problem Statement 26013** · Automated Integration and Intelligent Harmonization of
Multi-source Geospatial Data for Urban Land Record Management
Ministry of Rural Development · Department of Land Resources · Smart India Hackathon 2026

---

## 1. What the problem actually is

The problem statement asks for a system that automatically integrates, harmonises,
validates and synchronises multi-source land datasets with AI-generated feature extraction
outputs. Underneath that phrasing is a specific and unglamorous difficulty.

For any given piece of urban ground in India there are, today, between four and ten
authoritative-looking geometries, and **they disagree**. The revenue department's cadastral
map, the corporation's property survey, the utility agency's service layer, the ISRO
satellite-derived footprint layer and the new drone survey each describe the same wall in a
different place, with a different identifier, in a different schema, from a different year,
to a different accuracy. None of them is simply wrong.

That is not an ETL problem. ETL assumes there is a right answer in the sources and the job
is to move it. Here the job is to **adjudicate** — and to do so in a way a revenue officer
can defend in an appeal three years later.

Every design decision in SAMANVAY follows from that.

## 2. What SAMANVAY does

It takes the raw multi-source corpus for an area, and emits:

* **one harmonised parcel and building fabric**, with a ULPIN (Bhu-Aadhaar) for every parcel;
* a **six-dimension explainable confidence score** on every feature, and an A–E grade that
  tells an officer whether to publish it, review it, or send a team to the field;
* an **adjudication queue** of what it could not decide, ranked by the value of deciding it
  and batched so an officer decides one *kind* of thing at a time;
* a **typed discrepancy log** that distinguishes a subdivision from a re-survey from a
  department's missing data — because those imply completely different registry actions;
* a **tamper-evident provenance ledger** in which every claim, every decision and the
  reasoning behind it is recorded and independently verifiable;
* **OGC API - Features** endpoints so another department can consume it from QGIS with no
  SAMANVAY-specific code.

## 3. The three ideas that make it work

### 3.1 Claims, not records

Nothing entering the platform is treated as fact. A source *asserts a claim* about one
property of one entity. Claims are never deleted; they are superseded by a resolution that
names them. Three consequences:

* any decision can be re-examined, years later, with the evidence intact;
* a change of policy — a new reliability prior, a new statutory rule — can be **replayed**
  over the same claims without re-ingesting anything;
* the platform can say *why* it chose a boundary, in the language of a land record rather
  than the language of a model.

### 3.2 Belief and plausibility, not probability

Probability forces the mass not assigned to a hypothesis onto its negation, so a single
source asserting something becomes an implicit refutation of every alternative.
Dempster-Shafer evidence theory lets mass sit on **"unknown"**, which is the correct state
when only one department has an opinion.

The gap between belief and plausibility is the platform's measure of its own ignorance, and
it is what decides whether a human is needed. The conflict mass **K** is retained rather
than normalised away, because a high K is precisely the signal that two confident sources
contradict each other — the most valuable output the system produces.

### 3.3 The platform never invents a boundary

Where sources disagree geometrically, SAMANVAY selects **one source's actually observed
boundary** by evidence. It does not average them. An averaged boundary is attributable to
no survey, can be asked of no surveyor, and is indefensible in a dispute — which makes it
the worst possible output, however good it looks on a map.

## 4. Everything the problem statement asks for

| PS requirement | Module | Demonstrated on real data |
|---|---|---|
| Drone imagery | `ingest.tiles` | Real UAV flights, 3 independent reconstructions |
| Orthorectified Imagery | `raster.cog`, `raster.coreg` | ORI rebuilt at 0.051 m/px; co-registration measured |
| DSM / DTM datasets | `raster.terrain` | Real calibrated float DSM → DTM → nDSM |
| Existing cadastral maps | `ingest.vector` | 6.02 M real TNGIS parcels; 220 k NCSCM parcels |
| Revenue records | `attributes.canonical` | Survey numbers, LGD hierarchy, FMB flags |
| Municipal GIS layers | `attributes.schema_match` | 964 k GCC footprints with ward/zone/locality |
| Utility network data | `topology.validate_network` | Dangle / undershoot / connectivity rules |
| Ground truthing | `crs.gcp`, `quality.accuracy` | Blunder detection, CE90/LE90, Moran's I |
| GNSS / CORS | `ingest.gnss` | RINEX, NMEA, CSV; uncertainty carried through |
| Building footprints | 3 independent real sources | GCC survey + Google Open Buildings + AMRUT |
| **AI/ML spatial matching** | `matching.*` | Weak-supervised + self-trained GBM, AUC 1.000 |
| **Automated topology correction** | `topology.repair` | Bounded, audited, refusable |
| **Intelligent attribute mapping** | `attributes.schema_match` | 4-signal automatic crosswalk |
| **Geo-referencing & CRS engine** | `crs.engine`, `crs.gcp` | Everest/Kalianpur datums, GCP ladder |
| **Change detection** | `change.*`, `geoai.change` | Typed, systematic-offset-aware, 3-signal raster |
| **Spatial conflict resolution** | `conflict.*` | Dempster-Shafer + statutory rules + queue |
| **Confidence scoring** | `confidence.scorer` | 6 dimensions, explainable, graded A–E |

## 5. Beyond the problem statement

1. **ULPIN minting with guaranteed uniqueness and re-survey stability.** Identity derived
   from a snapped geohash cell so a 40 cm re-survey does not orphan a mutation history,
   with a nonce that guarantees uniqueness where two urban parcels share a cell.
2. **Hash-chained provenance ledger with Merkle inclusion proofs.** A citizen holding their
   record, its hash and an audit path can verify it against a gazette-published root —
   independently of the department.
3. **Encroachment intelligence.** Change detection restricted to poramboke/government land,
   reported as *findings requiring verification*, never as determinations.
4. **Area reconciliation** in Indian customary units — acre-cent, ground, kuzhi, guntha,
   kanal — with the recorded-versus-geodesic discrepancy as a first-class field.
5. **Active-learning loop.** Every adjudication becomes a training example and a Beta
   posterior update to that source's empirical reliability, so the priors self-correct over
   a campaign instead of needing an expert to tune them.
6. **LOD1 3-D city model** (CityJSON 1.1) from harmonised footprints and measured heights,
   with floor-space-index and ground-coverage checks against development-control limits.
7. **DPDP-Act-aware PII handling.** Owner data is absent from the feature API entirely and
   reachable only through a purpose-bound, ledger-logged endpoint.
8. **Inter-departmental subscription bus.** A utility agency is notified the moment a parcel
   it depends on changes, filtered by AOI, feature class, change type and confidence.

## 6. Impact, stated carefully

The problem statement asks the solution to reduce manual GIS effort, improve accuracy,
enable inter-departmental exchange, accelerate cadastral finalisation, improve
interoperability and support standardised digital land governance.

What can be measured from this build, on real data:

| Outcome | Evidence |
|---|---|
| Manual effort reduced | The pipeline reconciles a 76.7 km² AOI end to end without a human. Only genuinely contested cases — not every feature — reach the adjudication queue, and they arrive batched by cause. |
| Accuracy improved | A **1.08 m systematic offset** between the corporation survey and the ML extraction is measured and removed automatically; without that step it silently degrades every downstream overlay. |
| Errors made visible | Planar-partition error, per-ward confidence, source completeness gaps and extent discrepancies are reported as numbers rather than discovered later as disputes. |
| Exchange enabled | OGC API - Features with per-feature confidence and lineage; a consumer that cannot see how much to trust a boundary treats all boundaries alike. |
| Finalisation accelerated | Cases are ranked by the value of resolving them and grouped by cause, so an officer settles twenty instances of one systematic problem in one sitting. |
| Governance supported | Every state transition is in a verifiable chain. A land record whose history cannot be reconstructed is a rumour with a geometry column. |

What **cannot** be claimed from this build: a percentage speed-up against a named baseline.
No published measurement of manual GIS reconciliation effort for an Indian ULB exists to
compare against, and inventing one would be the easiest and least honest number in this
document.

## 7. The finding the demonstration produced

Running the platform over central Chennai's real, current, official multi-source land data
produces a result worth stating plainly:

> **The existing data does not reach NAKSHA's urban cadastral specification, and the
> platform can say exactly why and by how much.** No feature in the AOI grades A or B
> against a 0.5 m positional specification, because the best available source is a 1 m
> municipal survey and three quarters of features are corroborated by only one source.

That is not a failure of the platform. It is the platform doing its job: quantifying the
gap that the NAKSHA drone survey exists to close, parcel by parcel and ward by ward, so
that survey effort can be directed where the evidence says it is needed rather than
uniformly. A harmonisation platform whose first run reported that everything was fine
would not be measuring anything.

---

Read next: [`01-architecture.md`](01-architecture.md) ·
[`02-data-sources.md`](02-data-sources.md) ·
[`03-algorithms.md`](03-algorithms.md) ·
[`08-evaluation-results.md`](08-evaluation-results.md)
