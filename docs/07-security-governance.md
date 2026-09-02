# 07 · Security, privacy and governance

A harmonisation platform for urban land is, by construction, the most attractive single
target in a state's IT estate. It ends up holding a complete name-to-land mapping for a city,
a machine-readable list of every parcel whose boundary is contested, and the ability to make
a boundary look official. The governance problem is therefore not an afterthought to the
engineering — several engineering decisions in this system exist only because of it.

---

## 1 · The threat model, stated plainly

| Adversary | What they want | What stops them here |
|---|---|---|
| Data broker / scraper | The full owner ↔ land mapping for a city | PII absent from every feature path by construction; the only owner endpoint is purpose-bound and ledger-logged |
| Insider with DB access | Quietly move one boundary | Hash-chained ledger: an altered claim breaks the chain at that index, and the Merkle root no longer matches the published one |
| Litigant | A boundary the platform "decided" that suits them | The platform never invents a boundary; it selects an observed one and names the survey it came from |
| Careless integrator | Publishes low-confidence geometry as authoritative | Confidence and grade on every feature; grade A/B is the only thing the API markets as publishable |
| Automated pipeline itself | Silently corrupts geometry while "repairing" it | Topology repair is bounded by `max_vertex_shift_m` and refuses rather than exceeding it |

The last row matters most and is the least intuitive. The single most likely cause of a
wrong land record in an automated system is not an attacker — it is the automation being
confidently wrong at scale.

---

## 2 · Personal data under the DPDP Act 2023

Owner names in a cadastre are personal data, and the aggregation of them across a whole city
is precisely the harm the Act is concerned with.

**Data minimisation is enforced in code, not in policy.** `redact_pii()` runs on every
outbound feature on every path — collection listings, single-item reads, the console. Fields
carrying `pii=True` in the canonical schema come back as `"[redacted:dpdp]"`. There is no
query parameter, header or configuration flag that disables it.

**Purpose limitation** is the only door. `/api/owner/{ulpin}` requires a declared `purpose`
and an identified `requester`, and writes the access to the provenance ledger *before*
composing the response — so an access that is later denied or that errors still leaves an
audit record.

**Storage limitation.** The demonstration corpus contains no owner names at all. A production
deployment holds them in `harmonised_parcel` behind PostgreSQL column-level grants, with the
feature API's role holding no `SELECT` on those columns — so redaction is enforced twice,
once in the application and once by the database, and a bug in the first does not become a
breach.

**Purpose-bound rather than role-bound.** A tahsildar has a legitimate need for owner data
for the parcel under mutation and no legitimate need for the other 14,000. Roles cannot
express that distinction; a declared purpose per access, retained, can be audited for it.

---

## 3 · Integrity: why the ledger is hash-chained

Every consequential act — a claim ingested, a conflict resolved, a ULPIN minted, a mutation
recorded, a PII access — appends an entry to an append-only ledger. Each entry hashes
`index | timestamp | entity_id | operation | actor | canonical(payload) | prev_hash`.

Three properties follow, and each answers a specific institutional objection:

**Tamper evidence.** Changing any historical entry changes its hash, which breaks the
`prev_hash` of the next entry and every entry after it. `samanvay verify` reports the exact
index where a chain first breaks. An insider with write access to the database can still
change data; they cannot change it *quietly*, which is the property that matters for a
record that will be produced in court.

**Independent verification.** The Merkle root is designed to be published — in a gazette, on
a website, in a press note. A citizen holding their own record and an inclusion proof can
verify their parcel against that root using `sha256` alone, without trusting the department's
server, its database, or its staff. `/api/verify` returns the recomputation recipe precisely
so that the check can be done by someone who does not trust the endpoint.

**Replayability.** Because claims are never deleted, only superseded by a resolution that
names them, a policy change ("weight the 2024 municipal survey above the 2016 cadastral")
can be replayed over the same claims and the difference audited, without re-ingesting a byte.

---

## 4 · The human is in the loop by design, not by permission

The platform auto-resolves 43.4% of conflicts in the demonstration run. The other 56.6% do
not get resolved with a lower-confidence guess — they go to a queue.

Three design consequences:

**Uncertainty is not normalised away.** Dempster-Shafer conflict mass `K` is retained rather
than divided out. High conflict is the signal that the sources genuinely disagree; dividing
it out would turn "two departments disagree entirely" into a confident wrong answer.

**The queue is ranked by decision value**, not severity — uncertainty × severity × log(area
at stake) × batch size. An officer with two hours should spend them where the decisions
matter and generalise furthest.

**Decisions feed back.** Every adjudication becomes a training example for the matcher and a
Beta posterior update to that source's empirical reliability. A department whose data proves
better than its declared accuracy earns weight; one whose data proves worse loses it, from
evidence rather than from politics.

---

## 5 · What the platform will not assert

These are refusals encoded in the software, not caveats in a document.

**It will not invent a boundary.** Where sources disagree it selects one source's actually
observed geometry. An averaged boundary is attributable to no survey and can be asked of no
surveyor; it is indefensible in exactly the dispute where it would be relied on.

**It will not declare an encroachment.** Change detected on poramboke or government land is
reported as a *finding for verification*, with the evidence and the confidence attached.
Encroachment is a legal determination made by an officer after notice and hearing. A
platform that outputs "encroachment: true" invites that determination to be skipped.

**It will not declare a development-control violation.** Ground coverage and floor space
index findings are computed against parameters — the Tamil Nadu CDBR defaults — that vary by
ULB and land-use zone. A finding means "verify against the sanctioned plan", never "a
violation has occurred".

**It will not repair beyond its tolerance.** Topology repair that would move a vertex more
than `max_vertex_shift_m` (0.5 m by default) refuses and escalates. A gap that wide is not a
digitising artefact; it is a disagreement about where the boundary is, and it belongs to a
surveyor.

**It will not claim a grade the data does not support.** In the demonstration run no feature
in central Chennai reaches grade A or B against NAKSHA's 0.5 m urban specification. The
platform reports that rather than relaxing the specification.

---

## 6 · Deployment security

| Control | Position |
|---|---|
| Network | Designed for NIC/state data-centre deployment on the government network; no dependency on public internet at run time |
| Transport | TLS terminated at the department's gateway; the service binds locally |
| AuthN/AuthZ | Integration point, not a home-grown implementation — the department's existing IdP (Parichay / state SSO) issues identity; the platform consumes it |
| Database | Per-role grants; the feature API role has no `SELECT` on PII columns and no `UPDATE` on `provenance_ledger` |
| Ledger | Append-only by grant; periodic root publication makes retrospective edits detectable even by a DBA |
| Secrets | Environment-injected; nothing in the image or the repository |
| Supply chain | Pinned dependencies, Apache-2.0, no runtime call to any external service |
| Data residency | All processing on-premise; no cloud inference, no data leaves the department |
| Audit | Every PII access, adjudication decision and pipeline run is a ledger entry with an actor |

The last point is the one to press in an evaluation: the platform runs entirely inside the
department's own infrastructure. There is no external model API, no telemetry, and no step
that requires land data to leave the state's network.

---

## 7 · Governance questions this platform makes answerable

Not because it answers them itself — because it produces the evidence an official needs to.

- *Which wards can we publish today, and which need a survey first?* → `/api/quality` by ward.
- *Where do we send the drones first, for the most benefit per rupee?* → grade E, ranked by area.
- *Why does this parcel's boundary differ from the FMB sheet?* → `/api/lineage/{id}`.
- *Who looked at this citizen's owner record, when and why?* → ledger `pii_access` entries.
- *If we change how we weight the 2016 cadastral, what happens?* → replay the claims.
- *Can a citizen check their own record without trusting us?* → published Merkle root plus inclusion proof.
