# 04 · API reference

Two surfaces sit on the same store, and the split is deliberate.

**OGC API - Features** is the standards surface. A department's existing QGIS, ArcGIS or
Leaflet client should be able to consume harmonised land data without anyone writing an
adapter, so the feature endpoints follow OGC API - Features Part 1: Core with GeoJSON
(RFC 7946) representations and nothing invented on top.

**The platform API** (`/api/*`) is everything the standard has no opinion about: why a
geometry was chosen, how confident the platform is, what changed, what a human still has
to decide, and how a citizen verifies a record without trusting the server.

Interactive documentation is generated from the code at `/api/docs`; the OpenAPI 3 document
is at `/api/openapi.json`.

```bash
make api           # uvicorn on :8000, console at /map
geovax serve --out out/chennai_metro --port 8000
```

---

## OGC API - Features

### `GET /`

Landing page. Links to `conformance`, `collections`, and the OpenAPI service description.

### `GET /conformance`

```json
{"conformsTo": [
  "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
  "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/oas30",
  "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
  "http://www.opengis.net/spec/ogcapi-features-3/1.0/conf/filter"
]}
```

### `GET /collections`

Three collections, each with `extent`, `itemCount` and an `items` link:

| Collection | Contents |
|---|---|
| `parcels` | Harmonised cadastral parcels with ULPIN and confidence |
| `buildings` | Harmonised building footprints with parcel linkage |
| `adjudication` | Open conflicts awaiting a human decision, as a map layer |

### `GET /collections/{id}/items`

| Parameter | Type | Meaning |
|---|---|---|
| `bbox` | `minx,miny,maxx,maxy` | CRS84 bounding box |
| `limit` | 1–100000, default 1000 | Page size |
| `offset` | ≥ 0 | Page offset |
| `min_confidence` | 0.0–1.0 | Floor on the composite confidence score |
| `grade` | `A`–`E` | Exact confidence grade |
| `ward` | string | Ward, as the municipal source spells it |
| `ulpin` | string | Exact ULPIN |
| `survey_number` | string | Exact revenue survey number |
| `change_type` | string | Only features carrying that change |

Returns a `FeatureCollection` with `numberMatched`, `numberReturned`, `timeStamp` and a
`next` link when the result is truncated. Media type `application/geo+json`.

`min_confidence` and `grade` are the two parameters that make the platform useful rather
than merely standards-compliant. A department that will only act on data it can defend asks
for `?grade=A&grade=B`; a department planning a re-survey asks for `?grade=E`, which is the
list of places to send a drone.

```bash
curl 's:8000/collections/parcels/items?bbox=80.24,13.04,80.26,13.06&min_confidence=0.7&limit=50'
```

### `GET /collections/{id}/items/{feature_id}`

Accepts either the content-addressed `entity_id` or the `ulpin`.

**Every feature on every path passes through `redact_pii` before it is serialised.** Fields
flagged `pii` in the canonical schema — owner name, and the holder field of the patta — come
back as `"[redacted:dpdp]"`. There is no query parameter that turns this off; the only path
to personal data is `/api/owner/{ulpin}` below.

---

## Platform API

### `GET /api/run`

The run record: AOI, per-stage reports, per-stage wall-clock seconds, output counts, and the
ledger's verification state and Merkle root. This is the provenance of the whole dataset in
one document. 404 when no run has been published to the instance.

### `GET /api/quality`

Aggregate quality and the same figures per ward: parcel count, mean confidence, publishable
count (grades A–B), needs-check count (grades D–E), conflict count, total area. Sorted by
parcel count, capped at 60 wards.

This is the endpoint a commissioner actually looks at. It answers "which of my wards is the
data good enough to act on, and which needs a survey".

### `GET /api/adjudication?limit=&batch=`

Open cases, highest expected decision value first. Each case carries the question in plain
language, the competing options with their evidence weights and declared accuracies, the
platform's own proposal with its rationale and belief, and the ward and area at stake.

`batch` filters to one batch key (`property|datasets|rule`). Cases in a batch share a cause,
so an officer who decides one has effectively decided the rest — the queue is designed to be
worked in batches, not case by case.

### `GET /api/changes?change_type=&actionable=&limit=`

Change records with a histogram of every type in the run. `actionable=true` restricts to
changes that imply a registry action (a mutation, a survey, a notice) rather than changes
that are merely differences of opinion between two departments.

Cross-source differences are typed `source_omission`, `source_commission`,
`geometric_disagreement` and `attribute_disagreement`, never as construction or demolition.
Conflating "the corporation has not mapped this" with "this building was demolished" would
have generated 22,531 false mutation notices from a single layer pair in the demonstration
run.

### `GET /api/lineage/{entity_id}`

Every ledger entry touching that entity, in order, with hashes; plus the chain verification
state and the current Merkle root.

### `GET /api/verify`

Verifies the hash chain and returns the Merkle root, plus — deliberately — the exact recipe
for recomputing it yourself:

> Recompute `sha256` over `index|timestamp|entity_id|operation|actor|canonical(payload)|prev_hash`
> for each line of `ledger.jsonl` and confirm it equals that line's `entry_hash` and the next
> line's `prev_hash`.

A verification endpoint that can only be checked by the server that produced it is
decoration. The point is that a citizen with the published root and their own record can
verify inclusion without trusting the department at all.

### `GET /api/schema`

The canonical schema as data: field name, kind, type, required, PII flag, unit, permitted
domain, known aliases from source systems, and description. This is what makes the attribute
crosswalk auditable — a department can see exactly which of its column names the platform
recognises, and propose more.

### `GET /api/owner/{ulpin}?purpose=&requester=`

Purpose-bound access to owner data. Both parameters are mandatory (`purpose` ≥ 8 chars,
`requester` ≥ 3), and the access is written to the provenance ledger *before* the response
is composed, so the audit record exists even if the response never arrives.

In this reference deployment it returns `owner_name: null`, because the demonstration corpus
is open government data and contains no owner names. The endpoint exists to show where the
DPDP boundary sits and that crossing it is logged, not open.

### `GET /api/fmb/{ulpin}?format=json|xml|svg`

Generative Draft Field Measurement Book (FMB). Translates harmonised polygon boundaries into traditional cadastral survey measurements: G-line baseline, perpendicular ladder offsets, and outer F-lines. Format `xml` returns National Informatics Centre (NIC) CollabLand 3.0 XML; `svg` returns a dimensioned visual sketch with customary units (cents/sq m).

### `GET /api/litigation/hotspots?min_risk=0.45`

Predictive Litigation Hotspot Mapping. Fuses Dempster-Shafer evidential conflict mass ($K$) with external judicial records (e-Courts Services / NJDG API) and Encumbrance Certificate court stays (State Registration TN STAR 2.0 API). Returns an OGC/MVT-compatible GeoJSON FeatureCollection of high-risk parcels for proactive drone survey targeting and fast-track tribunal routing.

### `GET /api/litigation/assess/{ulpin}`

Detailed multi-factor legal and spatial risk dossier for a single parcel: active CNR suit numbers, court stay status, lis pendens entries, and plain-language statutory guidance for the Tahsildar.

### `GET /health`

Liveness, API version, and per-collection counts.

### `GET /map`

The harmonisation console — the operator-facing UI, served from `frontend/index.html`.

---

## Errors

| Status | Meaning |
|---|---|
| 404 | Unknown collection, unknown feature, or no run published to this instance |
| 422 | Missing or invalid query parameter (FastAPI validation, with the offending field named) |

---

## What the API deliberately does not have

**No write endpoints for geometry.** The platform is not a system of record; the revenue
department is. Corrections enter through adjudication decisions and the next pipeline run,
so that every published geometry traces to a source claim rather than to an HTTP request.

**No `?include_pii=true`.** See above.

**No unauthenticated adjudication decisions.** Reading the queue is open in the reference
deployment because it demonstrates the reasoning; recording a decision belongs behind the
department's own identity provider, and is left as an integration point rather than a
half-built auth system.
