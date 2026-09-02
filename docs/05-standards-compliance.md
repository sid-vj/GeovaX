# Standards, statutes and interoperability

A land-records platform that does not fit the legal and technical frame it operates in is a
prototype, not a system. This document records what SAMANVAY conforms to, what it partially
conforms to, and what it deliberately does not.

---

## 1. Indian statutory and programme frame

| Instrument | Relevance | How SAMANVAY conforms |
|---|---|---|
| **DILRMP** (Digital India Land Records Modernisation Programme) | Mandates ULPIN, survey/settlement reconciliation, and mutation trails | Mints 14-character ULPINs with checksum; records subdivision/amalgamation genealogy so encumbrance history survives geometry change; reports recorded-vs-computed extent discrepancy as a first-class field |
| **NAKSHA** (urban survey programme, DoLR) | Drone survey at 5 cm GSD, ±10 cm planimetric specification | Positional confidence is scored against a configurable specification defaulting to the NAKSHA urban tolerance; the platform reports how far existing data falls short of it, per ward |
| **LGD** (Local Government Directory, MoPR) | The national administrative code hierarchy | The canonical schema is built on `state_lgd` / `district_lgd` / `taluk_lgd` / `village_lgd`; the TNGIS cadastre's `lgd_village_code` is the primary join key |
| **National Geospatial Policy 2022** | Liberalised geospatial data; national single-zone framework; interoperability by open standards | EPSG:7755 (WGS 84 / India NSF LCC) is a first-class target CRS; the API is OGC API - Features |
| **DPDP Act 2023** | Personal data protection | Owner attributes are flagged `pii=True` in the schema, absent from the feature API entirely, redacted by default on every outbound path, and reachable only through a purpose-bound endpoint that writes an access record to the ledger before returning |
| **Tamil Nadu Land Encroachment Act 1905** | Poramboke/government land | Rule `R-POR-01`: a structure on land classified as poramboke does **not** override the classification; the case is escalated as an encroachment *finding for verification*, never as a determination |
| **TN Combined Development and Building Rules 2019** | Ground coverage, FSI, height limits | `geoai.lod1.DevelopmentControlCheck` computes coverage and FSI against configurable limits and reports findings in the language of the rules |
| **Registration Act 1908 / RoR** | Record of rights, patta, encumbrance | Canonical schema carries `patta_number`, `tenure_type`, `owner_share`, `encumbrance`; parcel genealogy carries encumbrance forward across mutations |
| **Survey of India CORS network** | National positional reference | Rule `R-CTL-01`: where GNSS/CORS or ground-truth control exists, its position takes precedence over every photogrammetric or cartographic claim |

## 2. OGC and ISO standards

| Standard | Status | Where |
|---|---|---|
| **OGC API - Features Part 1: Core** | ✅ Conformant | `/`, `/conformance`, `/collections`, `/collections/{id}/items`, `/collections/{id}/items/{fid}` |
| OGC API - Features Part 3: Filtering | ⚙ Partial — property filters implemented, CQL2 not | `items` supports bbox, confidence, grade, ward, ULPIN, survey number, change type |
| **GeoJSON (RFC 7946)** | ✅ | All feature responses; `application/geo+json` |
| **OGC Simple Features (ISO 19125)** | ✅ | Validity model in `topology.validate`; ring orientation enforced |
| **ISO 19107 Spatial Schema** | ✅ | Geometry model via GEOS/PostGIS |
| **ISO 19115 Metadata** | ⚙ Partial | Dataset-level lineage, authority, licence, accuracy, vintage captured in `SourceDataset` and the AOI manifest; not yet emitted as ISO 19139 XML |
| **ISO 19157 Data Quality** | ✅ Conceptually aligned | Completeness (commission/omission), positional accuracy (RMSE/CE90/LE90/NSSDA), logical consistency (topological rules), temporal quality (currency) all measured and reported |
| **Cloud Optimized GeoTIFF** | ✅ | Tiled, deflate-compressed, overview-bearing output from `ingest.tiles.write_geotiff` |
| **STAC** | ⚙ Designed, not implemented | Raster catalogue endpoints are architecturally provided for; not built in this repository |
| **CityGML LOD1 / CityJSON 1.1** | ✅ | `geoai.lod1.to_cityjson` — Solid geometry, `measuredHeight`, `storeysAboveGround` |
| **EPSG registry** | ✅ | Via PROJ; Indian legacy datums catalogued explicitly in `crs.engine.INDIAN_CRS` |
| **INSPIRE Cadastral Parcels** | ⚙ Aligned in shape | Parcel model follows the same general structure; INSPIRE-specific encodings not emitted |

## 3. Coordinate reference systems supported

**Modern**
`EPSG:4326` WGS 84 · `EPSG:7755` WGS 84 / India NSF LCC · `EPSG:32642–32645` UTM 42N–45N ·
`EPSG:3857` Web Mercator (tile delivery only — never used for measurement)

**Legacy Indian**
`EPSG:4240` Indian 1975 · `EPSG:4145` Kalianpur 1937 · `EPSG:4146` Kalianpur 1962 ·
`EPSG:4147` Kalianpur 1975 · `EPSG:24378–24383` India zones I, IIa, IIb, IIIa, IVa

Datum shift parameters for Everest-based Indian datums are catalogued in
`crs.engine.DATUM_SHIFTS_TO_WGS84` for the case where a file carries no CRS at all and an
operator asserts a datum. Where an EPSG transformation path exists, PROJ is used in
preference and the published transformation accuracy is reported.

**Everest units.** The Everest 1830 spheroid was defined in Indian feet
(1 ft = 0.30479951 m, distinct from the international foot). The constant is carried
explicitly, because a naive metre/foot assumption introduces a scale error of about
1 part in 10⁵ — 10 cm per kilometre, which accumulates across a village sheet.

## 4. Interoperability in practice

A consuming department needs three things, and gets all three without writing any
SAMANVAY-specific code:

1. **Standards-compliant features.** Point QGIS at the landing page; the collections appear.
2. **Trust metadata on every feature.** `confidence`, `confidence_grade`, the six component
   scores, `contributing_datasets` and `ledger_head` travel with the geometry. A consumer
   that cannot see how much to trust a boundary will treat all boundaries alike, which
   defeats the entire exercise.
3. **Change notification.** The subscription table lets an agency register an AOI, a set of
   feature classes, a set of change types and a minimum confidence, and receive a webhook
   when something it depends on moves — rather than re-downloading the state quarterly.

## 5. What SAMANVAY deliberately does not do

Stating these matters as much as stating the conformances.

* **It does not confer title.** It harmonises spatial records. Title is a legal
  determination by the competent revenue authority, and nothing the platform emits is a
  finding of ownership.
* **It does not determine encroachment.** It reports the geometric evidence — the area, the
  parcel, the classification — to the officer who makes that determination.
* **It does not average geometries.** Every emitted boundary is a boundary some survey
  actually observed.
* **It does not overwrite a source.** Claims are immutable; resolutions supersede.
* **It does not publish personal data through the feature API.** Not redacted-by-request;
  absent by construction.
* **It does not claim measured accuracy where it has none.** Without ground control, the
  positional score falls back to declared accuracy and says so in the note attached to the
  feature.
* **It does not treat contemporaneous sources as epochs.** Cross-source disagreement is a
  completeness finding for a custodian, never a mutation for a registry.

## 6. Security and governance posture

| Concern | Approach |
|---|---|
| Integrity | Hash-chained append-only ledger; `verify()` names the exact index of any break; daily Merkle root suitable for external anchoring (gazette publication or a notary service) |
| Auditability | Every resolution names the claims it supersedes and carries a plain-language rationale, including the statutory basis where a rule fired |
| Access control | RBAC by department with ABAC on AOI; PII behind a purpose-bound, logged endpoint |
| Data residency | Runs entirely on-premises; no external service dependency at inference time |
| Reproducibility | Deterministic pipeline with per-stage checkpoints; fixed random seed; AOI manifest with SHA-256 per input |
| Model governance | Feature importance and per-prediction attribution reported for every matcher run; expected calibration error measured; the deterministic fallback scorer is used and *declared* when there is too little signal to learn |
| Failure transparency | Every module reports what it refused to do and why, rather than silently degrading |
