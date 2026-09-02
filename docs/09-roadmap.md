# 09 · Roadmap

What exists, what is designed but not built, and what a real deployment would need next.
Kept honest: the middle column is the one an evaluator should read first, because a claim
about the future is worth nothing without an admission about the present.

---

## Built and measured

Everything in `docs/08-evaluation-results.md` ran on real Indian government data, end to end,
in this repository, with 81 tests passing:

- 12-stage harmonisation DAG with checkpointing, 22 km² of central Chennai, ~17 min on 2 vCPU
- Ingestion of 6.02 M TNGIS parcels, 220 k NCSCM parcels, 964 k GCC footprints, Google Open
  Buildings v3, AMRUT/Bhuvan footprints, real UAV imagery, a calibrated float DSM
- CRS engine over the Indian datum family, GCP model ladder with Baarda blunder detection
- Topology validation (15 rules) and bounded, refusable repair
- Weak-supervised and self-trained spatial matcher, global assignment, 99.988 % blocking
  reduction, 1.51 m @ 073° systematic inter-departmental offset recovered
- Dempster-Shafer conflict resolution with statutory rules, 43.4 % auto-resolved, the rest
  queued and ranked by decision value
- Six-dimension explainable confidence, graded A–E
- ULPIN minting with genealogy; hash-chained ledger with Merkle inclusion proofs
- Vector and raster change detection, cross-source-aware typing
- LOD1 CityJSON model with FSI / ground-coverage checks
- OGC API - Features + platform API + operator console

---

## Designed, not built

Stated plainly because these are the honest gaps.

| Capability | State | Why it is not built here |
|---|---|---|
| **STAC catalogue** for the raster tier | Schema and item structure designed; COG output exists | Needs a catalogue service to be meaningful; a single-AOI demonstration has nothing to catalogue |
| **Subscription bus** | `subscription` and `delivery_log` tables exist in the PostGIS schema with AOI, feature class, change type and confidence filters | Webhook delivery needs a real second department at the other end to be more than a loop-back test |
| **Adjudication write path** | Queue, batching, priority and the Beta reliability update are implemented | Recording a binding decision belongs behind the department's identity provider; a home-grown auth system would be worse than an integration point |
| **PostGIS as the live store** | Full canonical schema, indexes and views in `db/schema.sql`; loaders exist | The evaluation runs file-backed so a reviewer can reproduce it without standing up a database |
| **Multi-tenant ULB isolation** | LGD hierarchy is the join key throughout | Single-AOI demonstration |

---

## Next, in the order a deployment would actually need it

**1 · Ground truth, then recalibrate.** The largest gap in the evaluation is that there is no
independent ground truth: no GNSS campaign, no labelled matching truth set. A pilot's first
week should be a control survey of 200–300 points across the AOI. Everything downstream —
declared accuracies, the reliability priors, the confidence calibration — becomes empirical
instead of declared. Nothing else on this list produces as much value per rupee.

**2 · NAKSHA drone campaign integration on live data.** The platform's terrain and ORI chain
is demonstrated on real but foreign calibrated DSM data, because no Indian float DSM was
openly available at the required precision. Wiring it to an actual NAKSHA campaign output is
a configuration change, not a code change, but it must be *proved* on real campaign products.

**3 · Adjudication in the hands of real officers.** Two weeks of a real tahsildar working the
queue would settle questions no synthetic evaluation can: whether the batching matches how
decisions are actually made, whether the plain-language question is the right question, and
whether the priority ranking matches an officer's own sense of urgency. Expect the priority
function to change.

**4 · Devanagari and Tamil script attribute matching at scale.** Transliteration and record
linkage exist; they have not been evaluated against a large bilingual revenue corpus, which
is where the real errors live (the same village name in three spellings across two scripts).

**5 · Incremental runs.** The pipeline is currently full-AOI. A production cadence is nightly
and incremental: only claims touched since the last run need rematching. The claim model
already supports it — the DAG does not yet exploit it.

**6 · Deployment as a state service.** Sizing, offline operation and monitoring are covered in
`docs/06-deployment.md`; what remains is the institutional work — an MoU on data sharing
between the corporation and the revenue department, and a decision on who owns the queue.

---

## What would make this fail in production

The useful version of a roadmap names its own failure modes.

**Declared accuracies that are fiction.** Every weight in the evidence model is anchored to
what a department says its data is worth. If those numbers are aspirational, the fusion is
confidently wrong. Item 1 above is the mitigation, and it is first for this reason.

**A queue nobody works.** 56.6 % of conflicts need a human. If the institution does not staff
that, the platform degrades into a very well-documented backlog. The batching exists to make
the workload tractable — one decision generalising to hundreds — but it cannot make the
workload zero, and any vendor claiming it can is not describing land records.

**Treating grade A as truth.** Grade A means the sources agree and the best of them is
accurate enough for the specification. It does not mean the boundary is legally correct; a
unanimous set of sources can be unanimously wrong about a 1970s subdivision. The grade is a
statement about evidence, not about title.

**Scope creep into title.** This platform harmonises *spatial* records. Title, encumbrance
and succession are a different problem with a different legal standard, and a system that
blurs the two would let a geometric confidence score leak into a question about ownership.
The ULPIN genealogy exists precisely so that spatial mutation can be tracked *without* the
platform ever asserting anything about who owns the land.
