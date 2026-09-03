# Data sources

Every dataset SAMANVAY is demonstrated on is real, openly licensed data published by an
Indian government body or an institutional programme. **Nothing in this repository is
synthetic.** That is a deliberate constraint, and it is the most important thing to
understand about the evaluation: the discrepancies the platform finds are the discrepancies
that actually exist in Indian urban land data today, not discrepancies inserted to be found.

Synthetic data would have made every number in `docs/08-evaluation-results.md` better and
none of them meaningful. A matcher tuned on generated offsets learns the generator.

---

## 1. Area of interest

**Greater Chennai Corporation, Tamil Nadu.**

| | |
|---|---|
| Full AOI | 80.20 E – 80.28 E, 13.03 N – 13.11 N (≈ 8.7 × 8.9 km, 76.7 km²) |
| Working AOI | 80.225 – 80.265 E, 13.045 – 13.095 N (Nungambakkam · Kilpauk · Chetpet) |
| Test tile | 80.235 – 80.250 E, 13.070 – 13.085 N (1.6 × 1.7 km) |
| Working CRS | EPSG:32644 (WGS 84 / UTM zone 44N) |
| Interchange CRS | EPSG:4326 |
| LGD codes | State 33 (Tamil Nadu), District 571 (Chennai) |

Chennai was chosen for one reason: it is the place where an independent municipal survey,
two independent cadastral compilations and a machine-learning extraction all have real
coverage over the same ground. Multi-source harmonisation cannot be demonstrated honestly
anywhere that only has one source.

---

## 2. The corpus

### 2.1 Cadastral parcels

| | **TNGIS Tamil Nadu Cadastrals** |
|---|---|
| Authority | Tamil Nadu Geographic Information System, TNeGA (State) |
| Upstream | tngis.tn.gov.in village cadastral service |
| Licence | CC0-1.0 |
| Extent | Tamil Nadu, **6,017,242 parcels**, 1.07 GB GeoJSONL |
| Attributes | `survey_number`, `kide`, `lgd_district_code`, `lgd_taluk_code`, `lgd_village_code`, `is_fmb`, `is_active`, `created_at` |
| Declared accuracy | ≈ 3 m |
| PS requirement served | *Existing cadastral maps*, *Revenue records* |

This is the real revenue cadastre at survey-number level. Critically it carries
**`lgd_village_code`**, the Local Government Directory key, which is the join key to every
other Indian government dataset — the whole reason the canonical schema is built around LGD.

| | **NCSCM Coastal Tamil Nadu Cadastrals** |
|---|---|
| Authority | National Centre for Sustainable Coastal Management, MoEFCC |
| Licence | CC0-1.0 |
| Extent | Coastal Tamil Nadu, **220,668 parcels** (34,587 in Chennai district) |
| Attributes | `Village`, `Taluk`, `District`, `Survey_Number`, `Shape_Length`, `Shape_Area` |
| Declared accuracy | ≈ 5 m |
| PS requirement served | A genuinely **independent second cadastral source** over the same ground |

This dataset is the one that makes the demonstration honest. It is a different compilation,
of a different vintage, with a different schema, produced by a different agency, covering
the same parcels. Three properties of it are realistic harmonisation problems rather than
conveniences:

* administrative units as **names** rather than LGD codes, so the join requires
  transliteration and fuzzy place matching;
* `Survey_Number` as **free text** rather than a normalised identifier;
* a precomputed `Shape_Area` that **disagrees** with the geodesic area of its own geometry.

### 2.2 Building footprints

| | **Greater Chennai Corporation buildings (via TNGIS)** |
|---|---|
| Authority | Greater Chennai Corporation (ULB) |
| Licence | CC0-1.0 |
| Extent | **964,053 footprints**, 664 MB GeoJSONL |
| Attributes | `gcc_gis_id`, `zone_number`, `ward_number`, `region_name`, `locality`, `area_name`, `road_name` |
| Declared accuracy | ≈ 1 m |
| PS requirement served | *Building footprint datasets*, *Municipal GIS layers* |

A municipal **survey** product. It carries the corporation's administrative fabric — zone,
ward, locality, road — which is exactly the attribute set the cadastre lacks.

| | **Google Open Buildings v3, India 2023** |
|---|---|
| Authority | Google Research |
| Licence | CC-BY-4.0 |
| Extent | Partition `010001` covers 79.80–83.37 E, 10.27–18.71 N: **15,889,026 buildings** |
| Attributes | `confidence`, `presence`, `height` (metres), geometry |
| Declared accuracy | ≈ 1.8 m |
| PS requirement served | ***AI-generated feature extraction outputs*** |

This is the dataset that makes the problem statement's central phrase concrete. It is a
real machine-learning segmentation product with a **per-instance model confidence** and an
**estimated height**, which is precisely the kind of output the platform is asked to
reconcile against surveyed data. The per-instance confidence flows into the extraction-QC
filter and into the evidence penalty; the height flows into the built-form attributes and
the LOD1 model.

| | **AMRUT / Bhuvan urban buildings, Tamil Nadu** |
|---|---|
| Authority | NRSC / Bhuvan under AMRUT (MoHUA) |
| Licence | CC0-1.0 |
| Extent | **2,163,113 footprints** across AMRUT towns |
| Attributes | `Class`, `Sub_Class`, `Cons_type`, `No_Floors_`, `Locality`, `Rd_Name` |
| Declared accuracy | ≈ 2.5 m |

**Zero features fall inside the Chennai AOI.** That is not a failure of the download; the
AMRUT programme covers other Tamil Nadu towns. The platform reports it as a **completeness
gap** rather than hiding it, and the dataset remains configured because the same pipeline
run over Ambur or Vellore would use it. Reporting a source's absence is part of the job.

### 2.3 Municipal administrative layers

| Dataset | Authority | Features in AOI | Serves |
|---|---|---|---|
| GCC ward boundaries | Greater Chennai Corporation | 201 total, 73 in AOI | Ward attribution, ULPIN admin context, per-ward quality reporting |
| GCC zone boundaries | Greater Chennai Corporation | 16 total, 8 in AOI | Supervisory reporting tier |
| Chennai Metropolitan Area | CMDA | 1 | Planning-authority scoping |

### 2.4 Raster tier — drone imagery, ORI, DSM

| | **OpenDroneMap UAVArena** |
|---|---|
| Authority | OpenDroneMap public survey corpus |
| Licence | CC-BY-4.0 |
| Content | Three real UAV flights (aukerman, brighton_beach, sand_key), each reconstructed independently by **eight photogrammetry engines** (ODM 0.9.9–3.0.0, Pix4D 4.4.10, Agisoft Metashape 1.5.2, DroneDeploy 2.59, DroneMapper 1.9, NodeMICMAC) |
| Format | Orthophoto and DSM as XYZ tile pyramids to zoom 21 |
| Ground resolution | 0.051 m/px at zoom 21 |
| PS requirement served | *Drone imagery*, *Orthorectified Imagery (ORI)*, *DSM/DTM datasets* |

Multiple independent reconstructions of the **same flight** are what make the raster
evaluation rigorous rather than anecdotal. Any difference between two of them is by
construction a processing artefact and not a change on the ground, which turns change
detection into a controlled null experiment with a measurable false-positive rate.

| | **Geobasis NRW DOM1** |
|---|---|
| Authority | Bezirksregierung Köln, Geobasis NRW |
| Licence | Open data (dl-de/by-2-0), redistributed in GeoTIFF/test-data |
| Content | 1 km × 1 km airborne **digital surface model**, 1 m grid, float32 orthometric heights, EPSG:25832, 2020 |
| Elevation range | 87.29 – 167.74 m |
| PS requirement served | *DSM/DTM datasets*, with an **absolute vertical datum** |

The UAV corpus publishes its surface models only as colour-ramped images, and the
platform's trust gate correctly refuses to treat a recovered ramp as elevation
(`docs/08-evaluation-results.md`, §5). This dataset closes that gap: it is a real,
calibrated float DSM of a built-up area, the same product class NAKSHA delivers, so the
DSM → DTM → nDSM → structure chain can be demonstrated in true metres.

---

## 3. Coverage against the problem statement

| PS input | Real dataset used | Status |
|---|---|---|
| Drone imagery | UAVArena UAV flights | ✅ |
| Orthorectified Imagery (ORI) | 3 independent reconstructions, 0.051 m/px | ✅ |
| DSM / DTM datasets | Geobasis NRW DOM1 (calibrated) + UAVArena DSM tiles | ✅ |
| Existing cadastral maps | TNGIS TN Cadastrals + NCSCM Cadastrals | ✅ ×2 independent |
| Revenue records | TNGIS survey numbers, LGD hierarchy, FMB flags | ✅ |
| Municipal GIS layers | GCC buildings + wards + zones | ✅ |
| Utility network data | Configured; India-Geodata roads/rail/water available for the AOI | ⚙ configured |
| Ground truthing (GT) | Connector implemented (`ingest.gnss`); no open GT campaign exists for Chennai | ⚙ implemented, no open data |
| GNSS / CORS survey data | RINEX / NMEA / CSV connectors implemented | ⚙ implemented, no open data |
| Building footprint datasets | GCC survey + Google Open Buildings + AMRUT | ✅ ×3 |

Two rows are marked ⚙ rather than ✅, and the distinction is deliberate. The connectors are
written, unit-tested and wired into the pipeline; what does not exist is *openly published*
GNSS control or ground-truth check points for Chennai, because that data is held by Survey
of India and the state survey department and is not public. In a NAKSHA deployment it is
the first thing available. Claiming those rows as demonstrated would be the one dishonest
thing in this document.

---

## 4. Reproducibility

```bash
make data          # fetch the corpus (≈ 2.1 GB download)
python data_acquisition/build_aoi.py --raw data/raw --out data/aoi
```

`data/aoi/manifest.json` records, per dataset: the issuing authority, licence, upstream
service, vintage, declared accuracy, the source URL, features scanned, features kept, and
the **SHA-256 of the clipped working copy**. Any figure in the evaluation can be traced back
to a specific byte range of a specific published file.

### AOI corpus, as clipped

| Dataset | Scanned | Kept in AOI | Working size |
|---|---:|---:|---:|
| TNGIS cadastral parcels | 6,017,242 | 53,829 | 29.6 MB |
| NCSCM cadastral parcels | 220,668 | 13,289 | 8.4 MB |
| GCC building footprints | 964,053 | 214,809 | 159.2 MB |
| Google Open Buildings | 1,354,826 | 249,012 | 91.1 MB |
| AMRUT buildings | 2,163,113 | 0 | — |
| GCC wards | 201 | 73 | 0.2 MB |
| GCC zones | 16 | 8 | 0.1 MB |
| **Total** | **10,720,119** | **531,020** | **288.6 MB** |

The clipping step exists because the full corpus is 4 GB and 8.4 million features, and
nothing about the platform requires that to be resident at once. Streaming with a textual
bbox pre-screen filters the 1.07 GB cadastral file in about 40 MB of memory — which is what
makes the system runnable on the hardware a district office actually has, rather than only
in a data centre.

---

## 5. Licences

| Dataset | Licence |
|---|---|
| TNGIS / NCSCM cadastrals, GCC buildings, AMRUT buildings | CC0-1.0 |
| Google Open Buildings v3 | CC-BY-4.0 |
| GCC wards / zones / CMA (via DataMeet) | CC-BY-4.0 |
| OpenDroneMap UAVArena | CC-BY-4.0 |
| Geobasis NRW DOM1 | dl-de/by-2-0 |
| SAMANVAY source code | Apache-2.0 |

All datasets are redistributed unmodified under their upstream licences. The clipped working
copies are derivative extracts and carry the same terms.
