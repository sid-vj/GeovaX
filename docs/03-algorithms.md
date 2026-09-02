# Algorithms

This document states, for each algorithm, *what problem it solves*, *why the obvious
alternative fails*, and *how it is validated*. The order follows the pipeline.

---

## 1. Coordinate transformation and georeferencing

### 1.1 The Indian datum problem

Legacy Indian cadastral material sits on the Everest 1830 spheroid referenced to the Indian
Datum at Kalianpur, in one of several definitions (1937 Adjustment, 1956, 1962, 1975). The
datum shift to WGS 84 is **150–400 m** depending on zone. A file read as WGS 84 when it is
actually Kalianpur is displaced by up to four hundred metres — and because everything in the
file moves together, nothing looks obviously wrong until it is overlaid on something else.

`crs.engine` therefore refuses to guess. It carries an explicit catalogue of the CRSs that
appear in Indian land data, infers the CRS *family* from coordinate magnitudes at ingest,
and warns when the declared CRS disagrees with the implied one. That single check catches
the most expensive class of error in the domain for the cost of four comparisons.

### 1.2 Measurement CRS

Areas and lengths are never computed in EPSG:4326 (degrees are not metres) nor in EPSG:3857.
Web Mercator's area distortion at Chennai's latitude of 13°N is a factor of
1/cos²(13°) ≈ **1.054** — a 5.4% error, applied to a tax base. `metric_crs_for(lon, lat)`
returns the appropriate UTM zone; legal areas additionally use the geodesic formula on the
WGS 84 ellipsoid.

### 1.3 Ground control: a ladder, not a spline

Four estimators, in increasing flexibility:

| Model | Parameters | Minimum GCPs | Correct when |
|---|---|---|---|
| Helmert similarity | 4 | 2 | The sheet is geometrically sound, only misplaced |
| Affine | 6 | 3 | The sheet dried anisotropically |
| Polynomial 2 / 3 | 12 / 20 | 6 / 10 | Smooth regional distortion |
| Thin-plate spline | 2n+6 | 3 | Genuinely local distortion |

The design point is that **the ladder must not be climbed casually**. A thin-plate spline
interpolates exactly, so its in-sample residuals are zero by construction, and reporting
that zero as "accuracy" is the most common self-deception in georeferencing. `choose_model`
therefore selects on **leave-one-out RMSE** with a 15% parsimony margin: a more flexible
model must beat the simpler one by more than 15% to be chosen.

Blunder detection uses iterative data snooping (Baarda): fit, find the largest residual,
drop it if it exceeds *k*·σ, refit. Without it a single mis-identified control point
silently contorts an entire sheet and is invisible in the RMSE once the model has enough
freedom to absorb it.

**Validation.** Round-trip error (`round_trip_error`) must be sub-millimetre; a larger value
means a missing grid-shift file. LOO RMSE is reported alongside in-sample RMSE, always.

---

## 2. Topology

### 2.1 Rules

Fifteen rules across three groups: per-geometry (validity, self-intersection, duplicate
vertices, spikes, ring orientation, micro-area, multipart), pairwise (overlap, sliver),
and layer-level (gap, planar-partition error). Network layers add dangle, undershoot and
disconnected-component checks.

The headline metric is **planar-partition error**: the fraction of total area that is either
double-covered or uncovered. It is a single number that means something operationally —
above about 1% the cause is almost always a CRS or datum mismatch between contributing
layers, not many small digitising errors, and the fix is completely different.

### 2.2 Repair order, and why it is fixed

```
make valid → clean vertices → snap → absorb slivers → close gaps → finalise
```

Snapping comes before sliver absorption because **almost every sliver in a multi-source
cadastre exists because two agencies digitised the same wall and their vertices missed each
other by centimetres**. Snapping removes the cause; absorbing slivers only removes the
symptom, and does so by moving a boundary — which is a far more invasive act.

Both sliver absorption and gap closure need a rule for *which* neighbour receives the
disputed land. The platform uses **longest shared boundary**, because it is the rule a
surveyor uses in the field, and because it is order-independent: it gives the same answer
regardless of the sequence features arrive in, which a "first writer wins" rule does not.

### 2.3 Bounded repair

Every operation has an explicit tolerance and a hard ceiling. A snap that would move a
vertex more than 0.5 m is refused and escalated with the message that this is *a survey
discrepancy, not a digitising error*. A repair that changes a footprint's area by more than
12% is refused, because footprint area is a tax base.

**Validation.** `RepairReport` records, per action, the area moved and the maximum vertex
shift. The before/after planar-partition error is the headline measure of whether repair
helped.

---

## 3. Schema matching

Four signals, combined with fixed weights and a conservative threshold.

| Signal | Weight | What it catches | Where it fails |
|---|---|---|---|
| Lexical (name + alias + token overlap) | 0.34 | `lgd_village_code → village_lgd` | `kide` |
| Instance-based value profiling | 0.36 | `kide → survey_number` from value shape | Two columns of the same shape |
| Distributional (Jensen-Shannon over character bigrams) | 0.18 | Same domain, different format | No reference profile available |
| Structural (functional dependency) | 0.12 | `code → name` pairs | Single-column matches |

The instance signal is what actually solves the hard cases. A column whose values are
`437`, `437/2A`, `12` is a survey number no matter what it is called, and the regex family
plus cardinality ratio plus length distribution identify it in one pass.

Assignment is greedy on score with a **one-to-one constraint in both directions**. Without
the constraint two source columns can both claim `district_name`, which is how a pipeline
writes the taluk name into the district field for half a district.

**Validation.** `describe_crosswalk` prints the accepted mappings with per-signal scores and
the rejected candidates. A mapping below the accept threshold is proposed for review, never
applied silently.

---

## 4. Spatial matching

### 4.1 Blocking

R-tree query on each feature buffered by a search radius derived from the two layers'
declared positional accuracies:

```
radius = max(2 m, k · √(σ_left² + σ_right²)),  k = 3
```

Not a magic constant: it is the distance a true match can actually be displaced by. On the
Chennai demonstration this reduces 5,452 × 7,183 ≈ 39 million possible pairs to **32,681
candidates — a 99.92% reduction** — with a per-feature cap of 8 to bound the tail.

### 4.2 Pair features

Twenty-two features in six families, chosen so that each captures a different *kind* of
sameness and none is a proxy for another:

* **Overlap** — IoU, containment both ways. Primary signal; fails exactly when the layers
  are systematically offset.
* **Position** — centroid distance, and the same normalised by the equivalent-circle radius
  so that a 2 m error means something different for a shed and a stadium.
* **Shape** — area and perimeter ratios, compactness difference, **turning-function
  distance** (rotation- and translation-invariant), **Hu invariant moments**, orientation
  and elongation from the minimum rotated rectangle. These survive translation entirely,
  which is what lets them match a shifted layer after IoU has given up.
* **Boundary** — Hausdorff and mean boundary distance, vertex-count ratio. Mean is preferred
  to Hausdorff because Hausdorff is set by a single worst vertex, and a single worst vertex
  is usually a spike artefact.
* **Semantic** — survey-number agreement after normalisation, administrative agreement,
  transliterated name similarity.
* **Context** — neighbourhood offset agreement, and candidate rank at each endpoint.

The **context** feature deserves a note. Individual small buildings on a dense street are
close to indistinguishable. The row they sit in is not: if a feature's neighbours all have
plausible counterparts at roughly the same offset, that offset is a real registration
difference and the pair is very likely correct. This is a cheap approximation of graph
matching and it lifts precision materially on dense urban fabric.

### 4.3 Registration offset removal

Two layers of the same city from different agencies are almost always *systematically*
offset. If that offset is not removed before matching, IoU collapses for every pair
simultaneously and the most informative feature becomes noise.

`estimate_offset` takes the **median** of centroid differences over confident pairs — not
the mean, because the sample is contaminated by genuine change and the median is unmoved by
up to half the sample being wrong. The estimate is reported, because it is itself a finding
worth telling the department about.

> On the real Chennai data the platform measures a **1.08 m systematic offset on a bearing
> of 070°** between the Greater Chennai Corporation survey and the Google Open Buildings
> extraction, from 522 confident pairs, with a residual RMS of 2.46 m.

### 4.4 Learning without labels

There is no labelled correspondence set for "is this TNGIS parcel the same as that NCSCM
parcel", anywhere. The matcher is therefore trained by **programmatic weak supervision**:

*Positive labelling functions*
- `overlap_anchor` — mutual nearest neighbours with IoU ≥ 0.62
- `containment_anchor` — one footprint inside the other, mutually best, consistent scale.
  This is the case that matters most in practice: the ML extractor traces the *roof*, the
  municipal survey traces the *plinth*, so one is reliably inside the other at IoU 0.5–0.7.
  Treating those as non-matches under-reports agreement by roughly half.
- `identity_anchor` — identical normalised survey number with real overlap

*Negative labelling functions*
- `disjoint_negative` — no overlap and more than five radii apart
- `scale_negative` — order-of-magnitude size gap without containment
- `competitor_negative` — both endpoints have a far better partner
- `shadow_negative` — a candidate with real overlap living in the shadow of a decisively
  better one. This deliberately generates **hard** negatives; a training set whose negatives
  all have zero IoU teaches the model nothing except to threshold IoU.
- `identity_conflict_negative` — different survey numbers, little overlap, different ward

Pairs on which the functions disagree **abstain** — those are precisely the pairs the model
is needed for, so they are withheld from training and decided by the model.

**Self-training.** Weak labels cover only the easy slice. The model then labels the
abstained pairs it is confident about (p ≥ 0.92 or ≤ 0.08), those become half-weight
training data, and the model is refitted over the fuller distribution. Original weak labels
are never overwritten, which bounds the drift that makes naive self-training dangerous.

> Measured on the Chennai tile: 2,073 weak labels from 32,681 candidates, expanded by
> self-training to 20,558; five-fold cross-validated AUC 1.000, expected calibration error
> 0.0002.

### 4.5 Global assignment and cardinality

Per-feature argmax produces contradictions. The accepted set is chosen by **exact
rectangular linear-sum assignment (Jonker-Volgenant) on each connected component** of the
candidate graph. Working per component is what makes it tractable: a 250,000-feature layer
decomposes into tens of thousands of components of a handful of features each.

Before assignment, each component is tested for **group cardinality**:

* **1:N subdivision** — the union of the children covers ≥ 80% of the parent *and* their
  areas sum to within 25% of the parent's.
* **N:1 amalgamation** — the mirror image.
* **N:M reorganisation** — neither holds but total area is conserved.

**Area conservation is the discriminator that makes this trustworthy.** A parcel split into
three whose parts sum to 98% of it is a subdivision; one whose parts sum to 40% is a bad
match set dressed up as one.

---

## 5. Evidence fusion and conflict resolution

### 5.1 Why Dempster-Shafer

The naive options all fail in instructive ways:

| Rule | Failure |
|---|---|
| Trust the newest | A recent bad survey beats an old good one |
| Trust the highest authority | Revenue is authoritative on ownership, hopeless on geometry |
| Average the geometries | Invents a boundary no surveyor observed and nobody can be asked to re-verify |
| Majority vote | Three derivatives of one survey count as three witnesses |

Dempster-Shafer separates **disagreement** from **ignorance**. A source with reliability 0.9
asserting X puts 0.1 on *unknown*, not on not-X. That is what allows a single source to be a
weak witness rather than an implicit refutation of everything else.

**Conflict mass K is retained, not normalised away.** Dempster's rule divides it out, which
produces the famous Zadeh absurdity: two confident, contradictory sources yield certainty in
a third option neither believes. Here a high K is *the signal that a human is needed*, so
discarding it would throw away the most valuable output.

### 5.2 Independence discounting

Datasets sharing a declared lineage ancestor have their weight divided by √n. Three
derivatives of one 1970s village map are one witness. Naive fusion treats them as three and
manufactures confidence.

### 5.3 Mass construction

```
mass = reliability × (0.45 + 0.55·recency) × (0.40 + 0.60·accuracy)
       × (1 + 0.35·corroboration) × (1 − penalty)
```

Recency and accuracy **modulate** reliability rather than multiplying it away. Multiplying
three independent sub-unit factors drives an ordinary source — a municipal survey, two years
old, accurate to a metre — down to a mass of about 0.09, which makes every claim look like a
rumour and sends every decision to a human. Modulating keeps it near 0.5, which is what it
deserves, while still separating it clearly from a fresh centimetre-accurate observation.

### 5.4 Geometric fusion

Geometries are continuous, so the discrete machinery needs a bridge: geometries that agree
**within a tolerance** are one hypothesis. Agreement is judged by **mean boundary separation
in metres**, not by IoU, because IoU is not scale-honest — two surveys of a 90 m² house
differing by 40 cm score IoU ≈ 0.82 (a "disagreement"), while two surveys of a 4,000 m²
compound differing by 3 m score IoU ≈ 0.97 (an "agreement"). The tolerance is derived from
the sources' own declared accuracies rather than being a global constant.

**The platform never averages geometries.** The output is always one source's actually
observed boundary, chosen by evidence, because a boundary in a land record must be
attributable to a survey somebody performed and can be asked to repeat.

### 5.5 The resolution ladder

1. **Statutory rules** — declared, not learned. `R-POR-01`: a structure on land recorded as
   poramboke is an *encroachment finding*, not evidence that the classification is wrong.
2. **Domain precedence** — tenure from the revenue record, position from GNSS/CORS, ward
   from the corporation.
3. **Evidence fusion** — Dempster-Shafer as above.
4. **Escalation** — when conflict mass ≥ 0.55, or belief < 0.40, or plausibility − belief >
   0.45.

---

## 6. Confidence scoring

Six dimensions, because one number cannot tell an officer what to *do*:

| Dimension | Weight | Measures |
|---|---|---|
| Positional | 0.28 | Accuracy of the surviving sources, benchmarked against control where it exists |
| Source agreement | 0.22 | How many *independent* sources contributed, and how much they agreed |
| Topological | 0.18 | Validity, contested area share, invasiveness of repair |
| Attribute completeness | 0.14 | Schema coverage weighted by legal significance of each field |
| Temporal currency | 0.10 | Freshness of the best surviving evidence, per source-type half-life |
| Lineage integrity | 0.08 | Whether provenance verifies and every claim is attributable |

Positional score is a **logistic on the log-ratio** of achieved to target accuracy: at
specification 0.70, twice as good 0.85, twice as bad 0.50, ten times as bad 0.20. A plain
`target/(target+error)` ratio decays far too fast and grades a metre-accurate municipal
survey the same as something an order of magnitude worse.

Grades: A ≥ 0.90 publishable; B ≥ 0.75 publishable flagged; C ≥ 0.60 desk review;
D ≥ 0.40 field verification; E reject.

---

## 7. Raster tier

### 7.1 DSM → DTM

**Progressive morphological filter** (Zhang et al. 2003, adapted for gridded surfaces).
Open the surface with a small structuring element; anything removed was a local maximum
smaller than the element. Grow the element and repeat. Accept a point as ground only if the
drop is within an elevation threshold that **scales with window size and slope tolerance** —
a fixed threshold either shaves hilltops or leaves buildings standing, and Indian urban
ground is rarely flat.

Two details matter. Nodata holes (water, shadow, featureless roofs) are filled by iterative
harmonic diffusion **before** filtering, because filtering across holes produces artefacts
that look exactly like buildings. And the final DTM is clamped to `min(DTM, DSM)`, because
an interpolation artefact above the surface produces negative object heights, which read
downstream as demolitions.

`nDSM = DSM − DTM` is then the direct measurement of built form, and `extract_structures`
segments it with no imagery and no training data — the honest baseline any learned extractor
has to beat.

### 7.2 Co-registration

**Phase correlation**: the inverse transform of the cross-power spectrum peaks at the
translation between two images. Chosen over feature matching because it is global, robust
to illumination difference (phase carries structure, magnitude carries brightness), and
sub-pixel by parabolic refinement.

Two guards make it honest. A **Hann window** is applied first — without it the FFT sees the
image border as an enormous step, producing a cross artefact through the origin that biases
the peak *towards zero shift*, i.e. it reports "no misalignment" precisely when it matters.
And **peak sharpness** (peak ÷ best value outside an exclusion disc) is reported: a broad
peak means the estimate is untrustworthy, and the module refuses rather than returning a
confident-looking number.

`coregister_tiled` estimates the shift tile by tile and reports its spread. A constant shift
is a translation and is fully correctable; a varying shift is a scale, rotation or terrain
effect, and a single translation would leave the worst areas untouched while degrading the
best.

### 7.3 Raster change detection

Three signals, flagged only where at least two agree:

* **Spectral** — Change Vector Analysis with radiometric normalisation, thresholded at
  *k* robust standard deviations above the scene median.
* **Structural** — moving-window normalised cross-correlation; survives illumination
  differences that defeat differencing.
* **Geometric** — nDSM differencing. **This is the decisive signal**: a new building adds
  height, a repainted roof does not. Without height, distinguishing a new building from a
  new car park is guesswork.

A shadow guard suppresses pixels that darkened in all bands while retaining texture.

**The null experiment.** Two independent photogrammetric reconstructions of the *same*
flight depict the same ground at the same instant, so every region the detector flags is a
false positive by construction. `validate_against_null` runs exactly that and reports a
measured false-positive rate — the detector's noise floor on this sensor and terrain. A
change detector with an asserted false-positive rate is not evidence; one with a measured
noise floor is.

---

## 8. Change typing

The distinction that matters most: **a re-survey that moves every boundary in a village by
1.2 m is not two thousand mutations.** The detector estimates the layer-wide systematic
offset (median over 1:1 matches), subtracts it, and classifies on the *residual*. Movement
explained by the systematic component is typed `POSITIONAL_ONLY` and marked non-actionable.

Equally important: **contemporaneous sources are not epochs.** Running a cross-source
comparison in temporal mode would raise a demolition notice for every building one
department happens not to hold. The detector has an explicit mode, and cross-source
differences are typed as `SOURCE_OMISSION` / `SOURCE_COMMISSION` /
`GEOMETRIC_DISAGREEMENT` — completeness findings for a custodian, not mutations for a
registry.

Each type carries the registry action it implies, in the language a revenue officer uses.
