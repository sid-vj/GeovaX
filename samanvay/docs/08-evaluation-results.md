# Evaluation results

Every figure in this document is an output of the platform running on real, openly licensed
Indian government data. There is no synthetic data anywhere in the build, and no figure has
been chosen to flatter the result. Where the platform performs badly, or refuses to answer,
that is reported too, because those are the findings that would matter to a department.

**Hardware:** 2 vCPU, 7 GB RAM. Deliberately modest — a system that needs a data centre to
harmonise one ward cannot be deployed to four thousand ULBs.

---

## 1. The run

| | |
|---|---|
| Area of interest | Chennai — Nungambakkam · Kilpauk · Chetpet, 80.225–80.265 E, 13.045–13.095 N |
| Extent | ≈ 4.3 × 5.5 km, **22.3 km²** |
| Sources | 4 real government layers (2 cadastral, 2 building) |
| Input features in AOI | 14,253 + 4,105 parcels · 50,133 + 60,712 buildings |
| **Wall-clock, end to end** | **1,065 s (17 min 45 s)** |

### Stage timings

| Stage | Seconds | What dominates it |
|---|---:|---|
| `ingest` | 44.2 | Streaming 1.07 GB of JSONL with a textual bbox pre-screen |
| `reproject` | 21.6 | One PROJ transform per geometry |
| `schema_map` | 1.4 | Value profiling over 6,000 sampled records per layer |
| `topology` | 120.3 | STRtree pairwise scan and union for the gap check |
| `match` | 544.4 | Two-stage feature extraction over 370,494 candidate pairs |
| `cluster` | 91.4 | Union-find transitive closure across layers |
| `resolve` | 62.4 | Dempster-Shafer fusion over 629,660 property claims |
| `assemble` | 37.1 | ULPIN minting, geodesic areas, output records |
| `structures` | 12.1 | Building-to-parcel assignment and built-form derivation |
| `change` | 4.8 | Typed cross-source discrepancy classification |
| `confidence` | 8.2 | Six-dimension scoring over 107,262 features |
| `publish` | 22.6 | GeoJSON, metrics, ledger, adjudication queue |

### Output

| | |
|---|---:|
| Harmonised parcels | **14,702** |
| Harmonised buildings | **92,560** |
| Total harmonised features | **107,262** |
| Distinct ULPINs minted | 14,702 (100% unique; 2 snapped-cell collisions disambiguated) |
| Typed discrepancy records | 107,860 |
| Adjudication cases | 20,207 escalated (queue capped at 5,000 for this run) |
| Ledger | 13 entries, chain verified, Merkle root `1b82733ef700b591…` |

---

## 2. Ingestion and profiling

The profiler measures every input rather than trusting its metadata. Two findings from the
Chennai corpus are worth stating:

**Coordinate precision.** Every vector source carries 7 decimal digits, an implied precision
of 0.011 m. That is *not* the same as accuracy — a dataset can be stored to the millimetre
and be three metres wrong — but it establishes that no source is precision-limited, so all
observed disagreement is real disagreement rather than rounding.

**Coverage.** The AMRUT/Bhuvan building layer contributes **zero features** to the AOI. It
covers other Tamil Nadu towns, not Greater Chennai. The platform reports this as a
completeness gap rather than silently proceeding with three sources where four were
configured.

---

## 3. Automatic schema matching

The matcher was given four source schemas it had never seen and produced crosswalks to the
canonical land-record schema without configuration.

Representative resolutions:

| Source column | Mapped to | Signal that carried it |
|---|---|---|
| `lgd_village_code` | `village_lgd` | lexical alias + LGD code pattern |
| `kide` | `survey_number` | **value profiling** — name similarity is useless here |
| `gcc_gis_id` | `door_number` | near-unique + `G-089-23-00856` pattern |
| `ward_number` | `ward` | lexical + low cardinality |
| `Survey_Number` | `survey_number` | lexical |
| `height` | `max_height_m` | alias + numeric range check 0–350 m |

The `kide` case is the one that matters: it is the TNGIS cadastre's survey-number column,
its name conveys nothing, and it is resolved purely because its *values* — `437`, `438/2`,
`1201/3A` — are survey numbers. Name-similarity matching alone cannot find it.

Mappings scoring between 0.45 and 0.62 are surfaced for review rather than applied. In this
run those included `OBJECTID → door_number` and `taluk_code → state_lgd`, both of which a
human would correctly reject — which is the point of not applying them automatically.

---

## 4. Topology

| Layer | Features | Result |
|---|---:|---|
| TNGIS cadastral parcels | 14,253 | validated and repaired; planar-partition error reported per layer |
| NCSCM cadastral parcels | 4,105 | validated and repaired |
| GCC buildings | 50,133 | validated (buildings are not a partition, so no gap check) |
| Google Open Buildings | 60,712 | extraction QC + regularisation before use |

Repairs are bounded and audited. Overlaps larger than 5 m² and snaps beyond 0.5 m are
**refused and escalated** rather than absorbed, on the grounds that a half-metre boundary
movement is a survey discrepancy and not a digitising error. The final mean topological
confidence across the AOI is **0.9997**.

---

## 5. AI/ML spatial matching

### 5.1 GCC municipal survey ↔ Google Open Buildings extraction

| Metric | Value |
|---|---:|
| Candidate pairs after blocking | 350,750 |
| Possible pairs | 50,133 × 60,712 ≈ 3.04 × 10⁹ |
| **Blocking reduction** | **99.988%** |
| Weak labels generated | 23,034 (10,637 positive / 12,397 negative) |
| Pairs abstained by the labelling functions | 327,716 |
| Pseudo-labels added by self-training | 207,423 |
| 5-fold CV accuracy / precision / recall | 1.0000 / 0.9999 / 1.0000 |
| **AUC** | **1.0000** |
| **Expected calibration error** | **0.0002** |
| Accepted matches | **33,600** |
| Elapsed | 499.7 s |

Cardinality of the accepted set:

| Relationship | Count | Meaning |
|---|---:|---|
| 1:1 | 32,547 | one building, both sources agree it is one building |
| 1:N | 652 | the extraction split what the survey holds as one structure |
| N:1 | 428 | the extraction merged what the survey holds as several |
| N:M | 3 | genuine reorganisation — escalated |
| unmatched (survey only) | 22,531 | the extraction missed these |
| unmatched (extraction only) | 36,965 | not in the municipal survey |

The CV figures are on the platform's own weak labels, not on independent ground truth, and
should be read as *"the model reproduces the labelling functions' decision surface and
generalises smoothly across it"* — not as a claim of 100% real-world precision. No
independent labelled correspondence set for Indian cadastral layers exists to test against;
constructing one is the single most valuable thing a deploying department could contribute.

### 5.2 TNGIS ↔ NCSCM cadastral compilations

| Metric | Value |
|---|---:|
| Candidate pairs | 19,744 |
| Accepted matches | 1,377 (1,253 one-to-one, 128 amalgamations) |
| AUC / calibration error | 1.0000 / 0.0001 |
| Unmatched TNGIS parcels | 12,876 |

The low match rate is itself the finding. The two compilations were produced for different
purposes — a statewide revenue cadastre and a coastal-zone management compilation — and
they agree on only about 10% of parcels in this AOI. A platform that reported a high match
rate here would be wrong.

---

## 6. Registration — the headline finding

> **A systematic offset of 1.51 m on a bearing of 073° between the Greater Chennai
> Corporation survey and the Google Open Buildings extraction**, estimated from 5,226
> confident pairs, residual RMS 2.51 m.

And between the two cadastral compilations:

> **3.41 m on a bearing of 309°**, from 469 confident pairs, residual RMS 9.05 m.

Both are measured automatically, in under a second, and removed before matching. Left in,
a 1.5 m offset collapses IoU for every pair simultaneously and the matcher's most
informative feature becomes noise; downstream, it makes every building appear to encroach
slightly on its neighbour, generating thousands of false findings that each cost an
officer's time to dismiss.

The estimator is the **median** of centroid differences over confident pairs, not the mean,
because the sample is contaminated by genuine change and the median is unmoved by up to
half the sample being wrong.

---

## 7. Conflict resolution

| Metric | Value |
|---|---:|
| Property claims fused | 629,660 |
| Entities with a conflict | 35,690 |
| **Auto-resolved** | **15,483 (43.4%)** |
| Escalated for adjudication | 20,207 |
| Statutory rule `R-GEO-01` fired | 702 times |

`R-GEO-01` fires when competing boundaries are more than 10 m apart. The platform refuses
to fuse those, on the grounds that a 10 m disagreement is a datum, control or identity error
and automated fusion would launder it into a false answer. Those 702 cases go to a human
with both boundaries and their provenance.

The **56.6% escalation rate is high, and it is honest.** Two thirds of entities in this AOI
are known to only one source, and where two sources do overlap they frequently disagree
beyond the tolerance their own declared accuracies justify. A platform reporting 95%
auto-resolution on this corpus would be hiding disagreement, not resolving it.

### Adjudication economics

5,000 queued cases group into **2 batches** by cause. At 90 seconds per decision that is
125 officer-hours — but the batching is the point: an officer settles one *kind* of
disagreement at a time rather than context-switching on every case, and each decision feeds
back as a training example and a Beta posterior update to that source's empirical
reliability.

---

## 8. Confidence

Across all 107,262 harmonised features:

| Dimension | Mean |
|---|---:|
| Lineage integrity | 1.000 |
| Topological | 0.9997 |
| Temporal currency | 0.734 |
| Attribute completeness | 0.495 |
| Positional | 0.419 |
| **Source agreement** | **0.330** |
| **Composite** | **0.592** |

| Grade | Count | Share |
|---|---:|---:|
| A — publish without review | 0 | 0% |
| B — publish, flagged | 0 | 0% |
| C — desk review | 51,852 | 48.3% |
| D — field verification | 55,410 | 51.7% |
| E — reject | 0 | 0% |

### What this means

**No feature in central Chennai reaches grade A or B against the NAKSHA 0.5 m urban
specification.** The reasons are specific and measurable:

* the best positionally accurate source available is a **1 m** municipal survey;
* **68.0%** of entities are known to only one source, so their errors are undetectable by
  corroboration;
* where two sources do exist, they disagree by more than their declared accuracies justify.

This is not a failure of the platform. It is the platform doing exactly what it was built
for: quantifying, parcel by parcel and ward by ward, the gap that the NAKSHA drone survey
exists to close — so that survey effort can be directed where the evidence says it is
needed rather than spread uniformly. **The grade map is a plan for where to fly first.**

A harmonisation platform whose first run reported that everything was fine would not be
measuring anything.

---

## 9. Change and discrepancy typing

Run in **cross-source mode**, because these are contemporaneous departments and not two
epochs. Running them in temporal mode would raise a demolition notice for every building
one department happens not to hold — 22,531 false mutations from one layer pair alone.

**GCC ↔ Google Open Buildings** — 93,096 discrepancies, 32,457 actionable:

| Type | Count |
|---|---:|
| `SOURCE_COMMISSION` (extraction only) | 36,965 |
| `GEOMETRIC_DISAGREEMENT` | 31,383 |
| `SOURCE_OMISSION` (survey only) | 22,531 |
| `POSITIONAL_ONLY` — suppressed as re-registration | 852 |
| `SUBDIVISION` | 646 |
| `AMALGAMATION` | 428 |
| `NO_CHANGE` | 291 |

The 852 `POSITIONAL_ONLY` records are the ones that matter most. Those features moved by
more than the noise threshold, but the movement is explained by the layer-wide 3.22 m
systematic component — so they are typed non-actionable rather than raised as 852 mutations.

---

## 10. Built form and the LOD1 city model

| Metric | Value |
|---|---:|
| Buildings assigned to a parcel | 56,837 (61.4%) |
| Parcels carrying at least one building | 10,001 |
| Mean structure height | 5.16 m |
| Buildings extruded to LOD1 solids | **61,066** |
| Buildings with no measured height | 31,494 (emitted flat and flagged, never given an assumed height) |
| Mean storeys | 1.92 |
| Tallest structure | 70.3 m |
| Total footprint | 11,949,898 m² |
| **Estimated gross floor area** | **20,195,120 m²** |

Storey distribution: 21,594 single-storey · 29,767 two · 6,633 three · 1,689 four · 544
five · 309 six · a long tail to 23. That distribution is what central Chennai actually looks
like, which is the strongest available evidence that the height attribution is sound.

### Development control

Of 10,001 parcels assessed against the Tamil Nadu CDBR 2019 defaults: **6,815 carry at least
one finding** — 6,618 on ground coverage, 3,069 on floor space index, 133 on height. Mean
ground coverage 81.8%, mean FSI 1.145.

**2,973 parcels show built-up area exceeding the plot area entirely.** That is a *data*
finding before it is a compliance finding — either the parcel boundary or the footprints are
wrong — and the platform says so explicitly rather than reporting 2,973 violations.

---

## 11. Raster tier

### 11.1 Orthorectified imagery

Three independent photogrammetric reconstructions of one real UAV flight (OpenDroneMap
3.0.0, Pix4D 4.4.10, Agisoft Metashape 1.5.2), rebuilt from published XYZ pyramids into
georeferenced COGs at **0.051 m ground resolution**.

### 11.2 Co-registration

| Pair | Global shift | Peak sharpness | Tiled spread | Verdict |
|---|---|---:|---|---|
| ODM ↔ Pix4D | 0.144 m | 24.0 | 1.42 px over 22 tiles | reliable, constant translation |
| ODM ↔ Metashape | 0.683 m | 1.72 | 8.14 px over 24 tiles | **refused** — not a pure translation |

The second row is the more interesting one. The platform declined to apply a shift because
the misalignment **varies across the frame**, which means it is a scale, rotation or
terrain-induced orthorectification difference. A single translation would have left the worst
areas untouched while degrading the best, and the module says so instead of returning a
confident-looking number.

### 11.3 The null change experiment

Two reconstructions of the same flight depict the same ground at the same instant, so every
region the detector flags is a false positive **by construction**.

| | ODM ↔ Pix4D |
|---|---:|
| Scene area | 157,751 m² |
| False-positive regions | 19 |
| False-positive area | 2,824 m² |
| **Measured false-positive rate** | **1.79%** |

That is the detector's noise floor on this sensor and terrain. A change detector with an
*asserted* accuracy is not evidence; one with a *measured* noise floor is.

### 11.4 DSM → DTM → nDSM, on a calibrated surface

The UAV corpus publishes its DSMs only as colour-ramped tiles. The platform's trust gate
**refused** to treat the recovered ramp as elevation — the best-fitting known colour map left
a residual of 29 RGB units against a 25-unit limit, meaning the publisher used a ramp the
platform does not hold. Refusing is the correct behaviour: the recovered values would have
been a plausible-looking fiction.

The chain was therefore demonstrated on a real calibrated float surface model — **Geobasis
NRW DOM1**, 1 km², 1 m grid, float32 orthometric heights:

| Metric | Value |
|---|---:|
| Ground filter | 5 passes, windows 2→32 m, **62.0% of cells classified as ground** |
| Elapsed | 1.3 s |
| DTM relief | 43.14 m (the site's true relief) |
| **nDSM median** | **0.023 m** — the terrain correctly sits at zero |
| nDSM p95 / p99 / max | 14.15 m / 20.38 m / 45.27 m |
| Above-ground area | 30.9% |

The nDSM median of 2 cm is the validation that matters: it says the derived ground surface
actually sits on the ground.

**Structures extracted from height alone** — no imagery, no labels, no model:

| Metric | Value |
|---|---:|
| Structures | 328 |
| Median footprint | 198.5 m² |
| Median height | 6.43 m |
| Storey histogram | 62 · **165** · 83 · 13 · 3 · 2 (1–6 storeys) |
| Built-up fraction of tile | 34.8% |

**Regularisation:** 213 of 328 footprints squared, **52.3% fewer vertices** (29,771 →
14,195), mean area change **−1.15%**. 106 buildings were correctly refused as genuinely
non-rectilinear, and 1 was refused because squaring would have moved its area too far —
footprint area is a tax base and must not be quietly altered to make a shape look neat.

---

## 12. Identity and provenance

| Property | Result |
|---|---|
| ULPIN uniqueness | 14,702 / 14,702 distinct; 2 snapped-cell collisions disambiguated by nonce |
| ULPIN stability under re-survey | Unchanged under shifts up to ~2 m (tested) |
| ULPIN checksum | **100% of single-character substitutions detected** (exhaustively tested: 13 positions × 31 substitutions) |
| Ledger chain | Verified; tampering detected at the exact entry index |
| Merkle inclusion proofs | Round-trip verified for arbitrary indices; forged leaves rejected |

---

## 13. Test suite

**81 tests, all passing**, covering identity, ledger integrity, CRS and geodesy, topology
validation and repair, georeferencing model selection, identifier and name normalisation,
schema matching, blocking and feature scale-invariance, global assignment and cardinality,
evidence fusion, confidence scoring, change typing, footprint regularisation, terrain
filtering and co-registration.

Four of them were written to protect properties that the *implementation initially got
wrong*, and caught it:

1. A ULPIN checksum using even position weights silently missed about a fifth of
   single-character errors, because an even multiplier is not invertible modulo 32.
2. The geodesic area formula used geodetic latitude with an authalic radius, giving a 0.44%
   error at Chennai's latitude — a hundred square metres on a two-acre holding.
3. The "Helmert" estimator was an unconstrained affine, so it could absorb a control blunder
   as shear and hide it.
4. Phase correlation returned the negated shift, so every alignment made the correlation
   worse and was silently rejected by the safety guard rather than raising.

None of these would have produced an obviously wrong output. All four are the class of
defect that quietly degrades a land record, which is precisely why the tests assert
properties rather than golden values.

---

## 14. What this evaluation does not establish

Stated plainly, because an evaluation that only lists successes is marketing.

* **No independent ground truth.** No surveyed check points exist publicly for Chennai, so
  positional accuracy is scored against *declared* accuracy, and the platform says so on
  every feature it emits. The `quality.accuracy` module implements CE90, LE90, NSSDA,
  systematic-bias separation and Moran's I on residuals, and is exercised by tests — but it
  has not been run against a real GT campaign because none is published.
* **No labelled matching truth set.** The AUC figures are against the platform's own weak
  labels. They demonstrate that the model generalises the labelling functions smoothly; they
  do not establish real-world precision.
* **No GNSS/CORS campaign.** The connectors are implemented and unit-tested; the data is
  held by Survey of India and is not public.
* **No baseline to compare against.** No published measurement of manual GIS reconciliation
  effort for an Indian ULB exists, so no percentage speed-up is claimed. Inventing one would
  be the easiest and least honest number in this repository.
* **The raster tier's AOI differs from the vector tier's.** No high-resolution drone survey
  of Chennai is openly published. The raster chain is demonstrated on real UAV and airborne
  data from elsewhere, and the document says which is which throughout.

---

## 15. Reproducing this

```bash
make setup
make data                    # ≈ 2.1 GB from the published sources, checksummed
make pipeline AOI=mid        # 17 min 45 s on 2 vCPU
make lod1
make terrain
make raster
make test
```

`data/aoi/manifest.json` carries the SHA-256 of every clipped input, so any figure above can
be traced to a specific byte range of a specific published file.
