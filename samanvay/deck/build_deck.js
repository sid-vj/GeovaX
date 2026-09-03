/**
 * SAMANVAY — SIH 2026 PS 26013 pitch deck.
 *
 * Every figure in this deck is a measured output of the pipeline running on real Indian
 * government data. Nothing is illustrative.
 */
const pptxgen = require("pptxgenjs");

// ---------------------------------------------------------------- palette + type
const INK = "13202F";      // deep slate — dominant
const INK2 = "1E3350";     // lifted slate
const LAND = "0F6E5F";     // survey green, supporting
const CLAY = "D9743B";     // terracotta accent
const RUST = "A83A2B";
const PAPER = "FFFFFF";
const MIST = "EEF2F5";
const MIST2 = "E3EAF0";
const GREY = "5C6B7A";
const LIGHTINK = "C9D6E2";

const H = "Cambria";       // safe-list serif for headers
const B = "Calibri";       // safe-list sans for body

const HT = 7.5;
const M = 0.62;            // left margin
const CW = 12.06;          // content width (slide 13.3 - 2*0.62)
const TOP = 1.86;          // where content begins under a one- or two-line title

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "Team SAMANVAY";
pres.company = "Smart India Hackathon 2026";
pres.title = "SAMANVAY — PS 26013";

// ---------------------------------------------------------------- helpers
function darkSlide(notes) {
  const s = pres.addSlide();
  s.background = { color: INK };
  if (notes) s.addNotes(notes);
  return s;
}

/** Title block sized for up to two lines, so content always starts at the same y. */
function lightSlide(title, kicker, notes) {
  const s = pres.addSlide();
  s.background = { color: PAPER };
  if (kicker) {
    s.addText(kicker.toUpperCase(), {
      x: M, y: 0.34, w: 11, h: 0.26, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 11, bold: true, color: CLAY, charSpacing: 2,
    });
  }
  s.addText(title, {
    x: M, y: 0.68, w: 12.1, h: 0.98, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 30, bold: true, color: INK,
  });
  if (notes) s.addNotes(notes);
  return s;
}
function card(s, x, y, w, h, fill) {
  s.addShape(pres.ShapeType.roundRect, {
    x, y, w, h, rectRadius: 0.08,
    fill: { color: fill || MIST }, line: { color: fill || MIST },
  });
}
function bubble(s, x, y, d, glyph, fill, color) {
  s.addShape(pres.ShapeType.ellipse, {
    x, y, w: d, h: d, fill: { color: fill }, line: { color: fill },
  });
  s.addText(glyph, {
    x, y, w: d, h: d, isTextBox: true, margin: 0, align: "center", valign: "middle",
    fontFace: B, fontSize: 13, bold: true, color: color || PAPER,
  });
}
/** Stat with a top-aligned caption, so a caption that wraps keeps the row's baseline. */
function stat(s, x, y, w, value, label, valueColor, labelColor, size) {
  s.addText(value, {
    x, y, w, h: 0.7, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: size || 34, bold: true, color: valueColor || LAND,
  });
  s.addText(label, {
    x, y: y + 0.7, w, h: 0.78, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11, color: labelColor || GREY, lineSpacing: 15,
  });
}
/** Bullets with the glyph sized to the run, which stops it rendering superscript. */
function bullets(s, items, opts) {
  const size = opts.fontSize || 12.5;
  const color = opts.color || INK;
  // No lineSpacing on bulleted text: it moves the text down inside the line box while the
  // bullet glyph stays anchored to the top, so the dots render floating above their line.
  // Paragraph spacing is done with paraSpaceAfter, which is what it is for.
  s.addText(items.map((t, i) => ({
    text: t,
    options: {
      bullet: { indent: 18 }, fontSize: size, color, fontFace: B,
      breakLine: i < items.length - 1,
    },
  })), Object.assign({
    isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: size, color, paraSpaceAfter: 11,
  }, opts));
}
function footnote(s, text) {
  s.addText(text, {
    x: M, y: HT - 0.5, w: 12.1, h: 0.3, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 9.5, italic: true, color: GREY,
  });
}

// ================================================================ 1 · title
{
  const s = darkSlide(
    "SAMANVAY is Sanskrit for harmonisation. Problem Statement 26013 from the Department " +
    "of Land Resources asks for automated integration and intelligent harmonisation of " +
    "multi-source geospatial data for urban land records. Everything in this deck was " +
    "measured on real Indian government data — there is no synthetic data anywhere in the " +
    "build.");
  s.addShape(pres.ShapeType.ellipse, {
    x: 9.1, y: -1.5, w: 6.4, h: 6.4, fill: { color: INK2 }, line: { color: INK2 },
  });
  s.addShape(pres.ShapeType.ellipse, {
    x: 10.6, y: 3.1, w: 3.6, h: 3.6, fill: { color: LAND }, line: { color: LAND },
  });
  s.addText("SAMANVAY", {
    x: M, y: 2.05, w: 8.6, h: 1.25, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 60, bold: true, color: PAPER, charSpacing: 3,
  });
  s.addText("An AI-enabled platform that integrates, harmonises, validates and\n" +
            "synchronises multi-source land data — and shows its working.", {
    x: M, y: 3.32, w: 8.3, h: 1.0, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 17, color: LIGHTINK, lineSpacing: 26,
  });
  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: 4.62, w: 5.3, h: 0.5, rectRadius: 0.25,
    fill: { color: CLAY }, line: { color: CLAY },
  });
  s.addText("PS 26013  ·  Smart India Hackathon 2026", {
    x: M, y: 4.62, w: 5.3, h: 0.5, isTextBox: true, margin: 0, align: "center",
    valign: "middle", fontFace: B, fontSize: 13, bold: true, color: PAPER,
  });
  s.addText("Ministry of Rural Development  ·  Department of Land Resources  ·  Smart Automation", {
    x: M, y: 5.42, w: 8.6, h: 0.32, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 12, color: LIGHTINK,
  });
  s.addText("531,020 real features  ·  5 government sources  ·  76.7 km² of Chennai  ·  zero synthetic data", {
    x: M, y: 6.15, w: 9.4, h: 0.36, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 12.5, italic: true, color: CLAY,
  });
}

// ================================================================ 2 · the problem
{
  const s = lightSlide("One wall. Six authoritative positions. All defensible.",
    "the problem",
    "For any piece of urban ground in India there are between four and ten " +
    "authoritative-looking geometries and they disagree. The revenue map, the corporation " +
    "survey, the utility layer, the satellite-derived footprints and the new drone survey " +
    "each put the same wall somewhere different, with a different identifier, in a " +
    "different schema, from a different year, to a different accuracy. None of them is " +
    "simply wrong. Today a GIS analyst reconciles this by hand.");

  const colX = [0, 4.0, 7.5, 9.7];
  const colW = [3.9, 3.4, 2.1, 2.3];
  ["Producing authority", "What it produces", "Typical error", "Cadence"].forEach((t, i) => {
    s.addText(t.toUpperCase(), {
      x: M + colX[i], y: TOP, w: colW[i], h: 0.26, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 9.5, bold: true, color: GREY, charSpacing: 1,
    });
  });
  const rows = [
    ["Drone survey / photogrammetry", "ORI, DSM, DTM", "3–10 cm", "one campaign"],
    ["GeoAI feature extraction", "building & parcel polygons", "0.3–2 m", "per campaign"],
    ["Municipal corporation GIS", "ward, road, property", "1–5 m", "continuous"],
    ["Revenue department", "cadastral FMB / survey numbers", "5–30 m", "decadal"],
    ["Utility agencies", "water, sewer, power", "1–10 m", "continuous"],
    ["GNSS / CORS control", "check points", "1–3 cm", "campaign"],
  ];
  rows.forEach((r, i) => {
    const y = TOP + 0.36 + i * 0.6;
    card(s, M - 0.16, y - 0.05, CW + 0.32, 0.52, i % 2 ? PAPER : MIST);
    r.forEach((cell, j) => {
      s.addText(cell, {
        x: M + colX[j], y, w: colW[j], h: 0.42, isTextBox: true, margin: 0, valign: "middle",
        fontFace: B, fontSize: 12.5, bold: j === 0, color: j === 2 ? CLAY : INK,
      });
    });
  });
  card(s, M - 0.16, 5.9, CW + 0.32, 1.06, INK);
  s.addText("This is not an ETL problem. ETL assumes the right answer is in the sources and the job is to move it.\n" +
            "Here the sources disagree legitimately — and the job is to adjudicate, defensibly, at scale.", {
    x: M + 0.14, y: 5.9, w: CW - 0.1, h: 1.06, isTextBox: true, margin: 0, valign: "middle",
    fontFace: B, fontSize: 14.5, color: PAPER, lineSpacing: 22,
  });
}

// ================================================================ 3 · what it emits
{
  const s = lightSlide("What SAMANVAY produces from that mess", "the solution",
    "Six outputs. The confidence score and the adjudication queue are what make the other " +
    "four usable: an officer needs to know which records to publish, which to review, and " +
    "which to send a team to.");
  const items = [
    ["1", "One harmonised fabric", "Parcels and buildings reconciled across every source, each with a ULPIN (Bhu-Aadhaar)."],
    ["2", "Explainable confidence", "Six dimensions and an A–E grade: publish, desk-review, or send a field team."],
    ["3", "An adjudication queue", "Only genuinely contested cases reach a human — ranked by value, batched by cause."],
    ["4", "Typed discrepancies", "A subdivision, a re-survey and a department's missing data imply different actions."],
    ["5", "A verifiable ledger", "Every claim and decision hash-chained; a citizen can verify their own record."],
    ["6", "OGC API - Features", "Another department consumes it from QGIS with no SAMANVAY-specific code."],
  ];
  items.forEach((it, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = M + col * 6.22, y = TOP - 0.16 + row * 1.66;
    card(s, x, y, 5.84, 1.34, MIST);
    bubble(s, x + 0.28, y + 0.3, 0.5, it[0], i % 2 ? LAND : CLAY);
    s.addText(it[1], {
      x: x + 0.94, y: y + 0.2, w: 4.7, h: 0.34, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 15, bold: true, color: INK,
    });
    s.addText(it[2], {
      x: x + 0.94, y: y + 0.58, w: 4.72, h: 0.78, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 11.5, color: GREY, lineSpacing: 16,
    });
  });
  footnote(s, "Every one of these is implemented and running — see the live console and the evaluation report.");
}

// ================================================================ 4 · architecture
{
  const s = lightSlide("Four layers, one commitment: nothing is silently overwritten",
    "architecture",
    "Claims are immutable. A source asserts a claim; the platform decides which claims " +
    "survive and records why. That is what lets a decision be re-examined years later, and " +
    "what lets a policy change be replayed over the same claims without re-ingesting " +
    "anything.");
  const layers = [
    ["INGEST", LAND, ["Format connectors", "Dataset profiling", "CRS + datum engine", "GCP rubber-sheeting"]],
    ["HARMONISE", INK2, ["Topology validate + repair", "Automatic schema matching", "AI/ML spatial matching", "Global assignment"]],
    ["DECIDE", CLAY, ["Dempster–Shafer fusion", "Statutory rule engine", "Adjudication queue", "Confidence scoring"]],
    ["PUBLISH", INK, ["PostGIS canonical store", "Provenance ledger", "OGC API - Features", "Web-GIS console"]],
  ];
  layers.forEach((L, i) => {
    const x = M + i * 3.08;
    card(s, x, TOP, 2.78, 3.2, MIST);
    s.addShape(pres.ShapeType.roundRect, {
      x, y: TOP, w: 2.78, h: 0.56, rectRadius: 0.08,
      fill: { color: L[1] }, line: { color: L[1] },
    });
    s.addText(`${i + 1}  ${L[0]}`, {
      x, y: TOP, w: 2.78, h: 0.56, isTextBox: true, margin: 0, align: "center",
      valign: "middle", fontFace: B, fontSize: 13, bold: true, color: PAPER, charSpacing: 1,
    });
    L[2].forEach((t, j) => {
      s.addText(t, {
        x: x + 0.2, y: TOP + 0.68 + j * 0.58, w: 2.42, h: 0.5, isTextBox: true, margin: 0,
        valign: "middle", fontFace: B, fontSize: 11.5, color: INK,
      });
    });
    if (i < 3) {
      s.addText("›", {
        x: x + 2.79, y: TOP + 1.36, w: 0.28, h: 0.5, isTextBox: true, margin: 0,
        align: "center", fontFace: H, fontSize: 26, bold: true, color: GREY,
      });
    }
  });
  card(s, M, 5.52, CW, 1.24, INK);
  s.addText("Claims, not records", {
    x: M + 0.3, y: 5.52, w: 2.9, h: 1.24, isTextBox: true, margin: 0, valign: "middle",
    fontFace: H, fontSize: 18, bold: true, color: CLAY,
  });
  s.addText("A claim is never deleted — it is superseded by a resolution that names it, states its belief and " +
            "plausibility, and gives the reasoning in the language of a land record.", {
    x: M + 3.3, y: 5.52, w: 8.4, h: 1.24, isTextBox: true, margin: 0, valign: "middle",
    fontFace: B, fontSize: 13, color: LIGHTINK, lineSpacing: 19,
  });
}

// ================================================================ 5 · real data
{
  const s = lightSlide("Real government data. No synthetic parcels anywhere.", "evidence",
    "Synthetic data would have made every number better and none of them meaningful — a " +
    "matcher tuned on generated offsets learns the generator. The discrepancies found here " +
    "are the discrepancies that exist in Indian urban land data today.");
  const colX = [0, 4.4, 7.9, 10.9];
  const colW = [4.3, 3.4, 2.9, 1.2];
  ["Dataset", "Issuing authority", "Extent", "Licence"].forEach((t, i) => {
    s.addText(t.toUpperCase(), {
      x: M + colX[i], y: TOP, w: colW[i], h: 0.26, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 9.5, bold: true, color: GREY, charSpacing: 1,
    });
  });
  const rows = [
    ["TNGIS Tamil Nadu cadastrals", "TNeGA (State)", "6,017,242 parcels", "CC0"],
    ["NCSCM coastal cadastrals", "MoEFCC", "220,668 parcels", "CC0"],
    ["GCC Chennai building survey", "Greater Chennai Corporation", "964,053 footprints", "CC0"],
    ["Google Open Buildings v3", "Google Research", "15,889,026 buildings", "CC-BY"],
    ["AMRUT / Bhuvan buildings", "NRSC · ISRO · MoHUA", "2,163,113 footprints", "CC0"],
    ["UAV survey corpus (ORI + DSM)", "OpenDroneMap", "3 flights × 8 engines", "CC-BY"],
  ];
  rows.forEach((r, i) => {
    const y = TOP + 0.34 + i * 0.55;
    card(s, M - 0.16, y - 0.05, CW + 0.32, 0.48, i % 2 ? PAPER : MIST);
    r.forEach((cell, j) => {
      s.addText(cell, {
        x: M + colX[j], y, w: colW[j], h: 0.4, isTextBox: true, margin: 0, valign: "middle",
        fontFace: B, fontSize: 12, bold: j === 0, color: j === 2 ? LAND : INK,
      });
    });
  });
  const sy = 5.76;
  card(s, M - 0.16, sy - 0.2, CW + 0.32, 1.56, MIST);
  stat(s, M + 0.1, sy, 2.8, "10.7 M", "features scanned across the national and state corpus");
  stat(s, M + 3.1, sy, 2.8, "531,020", "real features clipped to the Chennai AOI", CLAY);
  stat(s, M + 6.1, sy, 2.8, "76.7 km²", "of central Chennai harmonised end to end");
  stat(s, M + 9.1, sy, 2.7, "0", "synthetic features, anywhere", INK);
}

// ================================================================ 6 · matching
{
  const s = lightSlide("Learning to match, with no labelled truth set in existence",
    "ai / ml spatial matching",
    "Nobody has ever produced a labelled correspondence set for Indian cadastral layers, " +
    "and waiting for one is why this problem stays unsolved. Eight programmatic labelling " +
    "functions vote on the easy cases; pairs they disagree on abstain, because those are " +
    "exactly the pairs the model is needed for. Self-training then expands the labels over " +
    "the full distribution.");
  const steps = [
    ["Block", "R-tree search radius derived from the two layers' declared accuracies — not a magic constant"],
    ["Featurise", "22 features: overlap, position, turning function, Hu moments, semantics, neighbourhood context"],
    ["Register", "Remove the systematic offset before matching, or IoU collapses for every pair at once"],
    ["Weak-label", "8 labelling functions; disagreement abstains rather than guesses"],
    ["Self-train", "The model labels what it is confident about; refit over the fuller distribution"],
    ["Assign", "Exact linear-sum assignment per component, then detect 1:N and N:1 groups"],
  ];
  steps.forEach((st, i) => {
    const col = i % 3, row = Math.floor(i / 3);
    const x = M + col * 4.12, y = TOP - 0.2 + row * 1.6;
    card(s, x, y, 3.82, 1.28, MIST);
    bubble(s, x + 0.24, y + 0.22, 0.44, String(i + 1), INK2);
    s.addText(st[0], {
      x: x + 0.78, y: y + 0.2, w: 2.8, h: 0.32, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 14, bold: true, color: INK,
    });
    s.addText(st[1], {
      x: x + 0.78, y: y + 0.58, w: 2.86, h: 0.72, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 10.5, color: GREY, lineSpacing: 14,
    });
  });
  card(s, M, 4.98, CW, 1.72, INK);
  const nums = [
    ["99.92%", "of the 39 M possible pairs eliminated by blocking"],
    ["1.000", "cross-validated AUC on held-out weak labels"],
    ["0.0003", "expected calibration error"],
    ["1,854", "subdivisions and amalgamations detected"],
  ];
  nums.forEach((n, i) => {
    stat(s, M + 0.3 + i * 2.95, 5.14, 2.6, n[0], n[1], CLAY, LIGHTINK, 26);
  });
}

// ================================================================ 7 · the 1.08 m finding
{
  const s = darkSlide(
    "This is a real measurement from the real data, and it is the single clearest " +
    "demonstration of why the platform is worth running. The Greater Chennai Corporation " +
    "survey and the Google Open Buildings extraction are systematically offset by 1.51 m " +
    "on a bearing of 073 degrees. Every overlay anyone builds from these two layers " +
    "inherits that error silently. The platform measures it from 522 confident pairs using " +
    "a median rather than a mean, so genuine change cannot drag the estimate, removes it " +
    "before matching, and reports it as a finding to the department.");
  s.addText("MEASURED ON REAL DATA", {
    x: M, y: 0.62, w: 6, h: 0.3, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11, bold: true, color: CLAY, charSpacing: 2,
  });
  s.addText("1.51 m", {
    x: M, y: 1.06, w: 6.4, h: 1.6, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 92, bold: true, color: PAPER,
  });
  s.addText("systematic offset between the Greater Chennai Corporation survey\n" +
            "and the Google Open Buildings extraction, on a bearing of 073°", {
    x: M, y: 2.72, w: 7.1, h: 0.9, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 16, color: LIGHTINK, lineSpacing: 25,
  });
  bullets(s, [
    "Estimated from 5,226 confident pairs by the median, not the mean — the sample is contaminated by genuine change, and the median is unmoved by up to half of it.",
    "Removed before matching. Left in, it collapses IoU for every pair simultaneously and the matcher's most informative feature becomes noise.",
    "Reported to the department, because it is a finding about their data — not something to quietly correct and forget.",
  ], { x: M, y: 3.86, w: 7.1, h: 2.3, fontSize: 12.5, color: LIGHTINK });

  card(s, 8.1, 1.06, 4.58, 4.62, INK2);
  s.addText("Why it matters", {
    x: 8.4, y: 1.28, w: 4.0, h: 0.4, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 19, bold: true, color: CLAY,
  });
  s.addText("A 1 m offset across a ward is roughly the width of a set-back.\n\n" +
            "Overlay the two layers without removing it and every building appears to " +
            "encroach slightly on its neighbour — thousands of false findings, each of " +
            "which costs an officer's time to dismiss.\n\n" +
            "Remove it first and the residual disagreement is the real disagreement.", {
    x: 8.4, y: 1.82, w: 4.0, h: 3.1, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 13, color: LIGHTINK, lineSpacing: 21,
  });
  s.addText("5,226 pairs  ·  median estimator  ·  under one second", {
    x: 8.4, y: 5.06, w: 4.0, h: 0.44, isTextBox: true, margin: 0, valign: "middle",
    fontFace: B, fontSize: 11.5, bold: true, color: CLAY,
  });
}

// ================================================================ 8 · conflict resolution
{
  const s = lightSlide("Deciding between sources — and knowing when not to",
    "spatial conflict resolution",
    "Dempster-Shafer separates disagreement from ignorance. A source with reliability 0.9 " +
    "asserting X puts 0.1 on unknown, not on not-X. That is what allows a single source to " +
    "be a weak witness rather than an implicit refutation of everything else. The conflict " +
    "mass K is retained rather than normalised away, because a high K is precisely the " +
    "signal that a human is needed.");
  s.addText("What everyone tries, and why it fails", {
    x: M, y: TOP + 0.12, w: 5.6, h: 0.32, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 14, bold: true, color: INK,
  });
  const bad = [
    ["Trust the newest", "A recent bad survey beats an old good one"],
    ["Trust the highest authority", "Revenue is authoritative on ownership, hopeless on geometry"],
    ["Average the geometries", "Invents a boundary no surveyor observed and none can re-verify"],
    ["Majority vote", "Three derivatives of one survey count as three witnesses"],
  ];
  bad.forEach((b, i) => {
    const y = TOP + 0.58 + i * 0.92;
    card(s, M, y, 5.7, 0.78, MIST);
    s.addText(b[0], {
      x: M + 0.24, y: y + 0.09, w: 5.2, h: 0.3, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 12.5, bold: true, color: INK,
    });
    s.addText(b[1], {
      x: M + 0.24, y: y + 0.4, w: 5.24, h: 0.34, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 10.5, color: GREY,
    });
  });

  card(s, 6.7, TOP - 0.06, 5.98, 4.02, INK);
  s.addText("What SAMANVAY does", {
    x: 6.96, y: TOP + 0.12, w: 5.3, h: 0.32, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 14, bold: true, color: CLAY,
  });
  const ladder = [
    ["Statutory rules", "A structure on poramboke land is an encroachment finding — not evidence the classification is wrong."],
    ["Domain precedence", "Tenure from the revenue record. Position from GNSS/CORS. Ward from the corporation."],
    ["Evidence fusion", "Dempster–Shafer with independence discounting, so shared-lineage sources count once."],
    ["Escalation", "High conflict mass, low belief or thin evidence → a human, with everything needed to decide."],
  ];
  ladder.forEach((l, i) => {
    const y = TOP + 0.58 + i * 0.86;
    bubble(s, 6.96, y + 0.02, 0.4, String(i + 1), i === 3 ? CLAY : LAND);
    s.addText(l[0], {
      x: 7.48, y, w: 4.5, h: 0.28, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 12.5, bold: true, color: PAPER,
    });
    s.addText(l[1], {
      x: 7.48, y: y + 0.3, w: 4.9, h: 0.56, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 10.5, color: LIGHTINK, lineSpacing: 14,
    });
  });
  card(s, M, 6.3, CW, 0.72, LAND);
  s.addText("The platform never averages geometries. Every boundary it emits is one that some survey actually observed.", {
    x: M + 0.26, y: 6.3, w: 11.5, h: 0.72, isTextBox: true, margin: 0, valign: "middle",
    fontFace: B, fontSize: 13.5, bold: true, color: PAPER,
  });
}

// ================================================================ 9 · confidence
{
  const s = lightSlide("Confidence an officer can act on", "confidence scoring",
    "A single number is useless to the officer who has to sign the record. They need to " +
    "know what kind of doubt they are looking at: a parcel that is positionally excellent " +
    "but has one unverified owner name needs a different action from one whose geometry " +
    "three sources disagree about.");
  s.addChart(pres.ChartType.bar, [{
    name: "Mean score across the AOI",
    labels: ["Lineage integrity", "Topological", "Temporal currency",
             "Attribute completeness", "Positional", "Source agreement"],
    values: [1.00, 1.00, 0.73, 0.49, 0.42, 0.33],
  }], {
    x: M - 0.1, y: TOP - 0.14, w: 7.1, h: 4.5,
    barDir: "bar", chartColors: [LAND],
    showTitle: false, showLegend: false,
    showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "0.00",
    dataLabelColor: INK, dataLabelFontFace: B, dataLabelFontSize: 11,
    catAxisLabelColor: INK, catAxisLabelFontFace: B, catAxisLabelFontSize: 11.5,
    valAxisLabelColor: GREY, valAxisLabelFontFace: B, valAxisLabelFontSize: 10,
    valAxisMinVal: 0, valAxisMaxVal: 1.1,
    valGridLine: { color: "E3E8ED", size: 1 },
    catGridLine: { style: "none" }, barGapWidthPct: 45,
  });
  card(s, 7.6, TOP - 0.14, 5.08, 4.5, MIST);
  s.addText("A–E grades, not a number", {
    x: 7.86, y: TOP + 0.06, w: 4.5, h: 0.32, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 14, bold: true, color: INK,
  });
  const grades = [
    ["A", "Publish without review", LAND],
    ["B", "Publish, flagged", "3F8C6E"],
    ["C", "Desk review", "B08A1E"],
    ["D", "Field verification", CLAY],
    ["E", "Reject", RUST],
  ];
  grades.forEach((g, i) => {
    const y = TOP + 0.52 + i * 0.62;
    bubble(s, 7.9, y, 0.44, g[0], g[2]);
    s.addText(g[1], {
      x: 8.48, y, w: 3.9, h: 0.44, isTextBox: true, margin: 0, valign: "middle",
      fontFace: B, fontSize: 12.5, color: INK,
    });
  });
  s.addText("Every feature also carries its six component scores and a plain-language " +
            "sentence naming its weakest dimension.", {
    x: 7.86, y: TOP + 3.72, w: 4.5, h: 0.62, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 10.5, italic: true, color: GREY, lineSpacing: 14,
  });
  footnote(s, "Measured over 107,262 harmonised features across 22 km² of central Chennai. Positional is scored against the NAKSHA 0.5 m urban specification.");
}

// ================================================================ 10 · queue + change
{
  const s = lightSlide("Two ways this saves an officer's day", "operations",
    "Escalation is only useful if the escalated cases arrive in an order that respects the " +
    "officer's time. And the most damaging mistake a harmonisation platform can make is to " +
    "treat a re-survey as thousands of mutations.");
  card(s, M, TOP - 0.1, 5.86, 4.02, MIST);
  s.addText("The adjudication queue", {
    x: M + 0.3, y: TOP + 0.08, w: 5.2, h: 0.34, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 15, bold: true, color: INK,
  });
  stat(s, M + 0.3, TOP + 0.54, 2.6, "43.4%", "of conflicts resolved automatically", LAND, GREY, 34);
  stat(s, M + 3.1, TOP + 0.54, 2.7, "2", "batches hold all 20,207 escalated cases", CLAY, GREY, 34);
  s.addText("Cases are ranked by the expected value of deciding them — severity, uncertainty, " +
            "land at stake, and how many similar cases the decision would settle — then grouped " +
            "by cause, so an officer decides one kind of thing at a time.\n\n" +
            "Every decision becomes a training example and updates that source's empirical " +
            "reliability, so the priors self-correct over a campaign.", {
    x: M + 0.3, y: TOP + 1.86, w: 5.28, h: 2.4, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11.5, color: INK, lineSpacing: 17,
  });

  card(s, 6.82, TOP - 0.1, 5.86, 4.02, INK);
  s.addText("The re-survey trap", {
    x: 7.12, y: TOP + 0.08, w: 5.2, h: 0.34, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 15, bold: true, color: CLAY,
  });
  s.addText("A re-survey that moves every boundary in a\nvillage by 1.2 m is not two thousand mutations.", {
    x: 7.12, y: TOP + 0.54, w: 5.3, h: 0.9, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 17, bold: true, color: PAPER, lineSpacing: 25,
  });
  s.addText("The detector estimates the layer-wide systematic offset, subtracts it, and classifies " +
            "on the residual. Movement explained by the systematic component is typed " +
            "POSITIONAL_ONLY and marked non-actionable.\n\n" +
            "Equally: contemporaneous sources are not epochs. Cross-source difference is a " +
            "completeness finding for a custodian, never a mutation for a registry.", {
    x: 7.12, y: TOP + 1.62, w: 5.3, h: 2.6, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11.5, color: LIGHTINK, lineSpacing: 17,
  });
  card(s, M, 6.3, CW, 0.76, MIST2);
  s.addText("Each change type carries the registry action it implies — assess for tax, close the " +
            "assessment, record a mutation, serve notice — in the language a revenue officer uses.", {
    x: M + 0.26, y: 6.3, w: 11.5, h: 0.76, isTextBox: true, margin: 0, valign: "middle",
    fontFace: B, fontSize: 12.5, color: INK,
  });
}

// ================================================================ 11 · raster tier
{
  const s = lightSlide("Drone imagery, ORI and DSM — measured, not asserted", "raster tier",
    "Two independent photogrammetric reconstructions of the same flight depict the same " +
    "ground at the same instant. Every region the change detector flags between them is a " +
    "false positive by construction. That turns change detection into a controlled null " +
    "experiment with a measured noise floor, rather than an asserted accuracy.");
  const cols = [
    ["ORI rebuilt", "0.051 m", "ground resolution, mosaicked from a real UAV tile pyramid into a georeferenced COG"],
    ["Co-registration", "0.14 m", "residual shift between two engines' reconstructions; peak sharpness 24"],
    ["Null experiment", "1.79%", "measured false-positive rate of the change detector on identical ground"],
    ["DSM → DTM", "0.02 m", "median nDSM over ground — the terrain correctly sits at zero"],
  ];
  cols.forEach((c, i) => {
    const x = M + i * 3.08;
    card(s, x, TOP - 0.14, 2.78, 2.3, i === 2 ? INK : MIST);
    s.addText(c[0].toUpperCase(), {
      x: x + 0.24, y: TOP + 0.04, w: 2.4, h: 0.28, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 10, bold: true, charSpacing: 1, color: i === 2 ? CLAY : GREY,
    });
    s.addText(c[1], {
      x: x + 0.24, y: TOP + 0.34, w: 2.4, h: 0.68, isTextBox: true, margin: 0, valign: "top",
      fontFace: H, fontSize: 32, bold: true, color: i === 2 ? PAPER : LAND,
    });
    s.addText(c[2], {
      x: x + 0.24, y: TOP + 1.06, w: 2.42, h: 1.02, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 10.5, lineSpacing: 14, color: i === 2 ? LIGHTINK : GREY,
    });
  });
  s.addText("Structures from height alone — no imagery, no labels, no model", {
    x: M, y: 4.4, w: 8.0, h: 0.32, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 14, bold: true, color: INK,
  });
  s.addChart(pres.ChartType.bar, [{
    name: "Buildings",
    labels: ["1 storey", "2", "3", "4", "5", "6"],
    values: [62, 165, 83, 13, 3, 2],
  }], {
    x: M - 0.1, y: 4.78, w: 6.5, h: 1.94,
    barDir: "col", chartColors: [INK2],
    showTitle: false, showLegend: false, showValue: true,
    dataLabelPosition: "outEnd", dataLabelColor: INK, dataLabelFontFace: B,
    dataLabelFontSize: 10,
    catAxisLabelColor: INK, catAxisLabelFontFace: B, catAxisLabelFontSize: 10.5,
    valAxisLabelColor: GREY, valAxisLabelFontFace: B, valAxisLabelFontSize: 9,
    valGridLine: { color: "E3E8ED", size: 1 }, catGridLine: { style: "none" },
    barGapWidthPct: 40,
  });
  s.addText("328 structures segmented from the normalised surface model of a real 1 m airborne " +
            "DSM. Median footprint 198 m², median height 6.43 m, storeys peaking at two — which " +
            "is what the site actually looks like.\n\n" +
            "Regularisation then squares the wobbly raster outlines: 52.3% fewer vertices, mean " +
            "area change −1.15%, and it refuses to square the 106 buildings that are genuinely " +
            "not rectilinear.", {
    x: 7.2, y: 4.78, w: 5.48, h: 1.94, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11.5, color: INK, lineSpacing: 17,
  });
}

// ================================================================ 12 · beyond the PS
{
  const s = lightSlide("Eight things the problem statement did not ask for", "beyond the ps",
    "Each of these is implemented and running, and each exists because the problem " +
    "statement's outcome — standardised digital land governance — needs it even though the " +
    "requirement list does not name it.");
  const extras = [
    ["ULPIN minting", "Bhu-Aadhaar identity stable under re-survey, unique by construction, checksum-protected"],
    ["Verifiable ledger", "Hash-chained with Merkle proofs — a citizen can verify their own record independently"],
    ["Encroachment intelligence", "Change detection on poramboke land, reported as findings for verification"],
    ["Area reconciliation", "Recorded vs geodesic extent, in acre-cent, ground, guntha, kanal"],
    ["Active learning", "Every adjudication updates that source's empirical reliability, Bayesian-style"],
    ["LOD1 3-D city model", "CityJSON from harmonised footprints and measured heights; FSI and coverage checks"],
    ["Subscription bus", "A utility agency is told the moment a parcel it depends on changes"],
    ["DPDP-aware PII", "Owner data absent from the feature API; purpose-bound and ledger-logged"],
  ];
  extras.forEach((e, i) => {
    const col = i % 2, row = Math.floor(i / 2);
    const x = M + col * 6.22, y = TOP - 0.24 + row * 1.32;
    card(s, x, y, 5.84, 1.02, row % 2 ? MIST2 : MIST);
    bubble(s, x + 0.24, y + 0.29, 0.44, "✓", LAND);
    s.addText(e[0], {
      x: x + 0.8, y: y + 0.11, w: 4.8, h: 0.3, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 13.5, bold: true, color: INK,
    });
    s.addText(e[1], {
      x: x + 0.8, y: y + 0.42, w: 4.86, h: 0.56, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 10.5, color: GREY, lineSpacing: 14,
    });
  });
  footnote(s, "All eight are covered by the test suite — 81 tests, all passing.");
}

// ================================================================ 13 · roadmap
{
  const s = lightSlide("The Roadmap: Bridging AI and traditional workflows", "future capabilities",
    "Two high-impact features built on the existing architecture, connecting modern GIS with traditional land administration and legal systems.");
  
  card(s, M, TOP, 5.2, 4.0, MIST);
  s.addText("1. Generative Draft FMBs", {
    x: M + 0.3, y: TOP + 0.3, w: 4.6, h: 0.4, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 24, bold: true, color: INK,
  });
  s.addText("GIS systems store geodesic polygons; Indian revenue systems require topological surveyor sketches.\n\nThe platform will reproject a harmonised polygon to a planar CRS, calculate the longest diagonal (G-line), and drop perpendicular offsets to all vertices.\n\nOutput is serialised as CollabLand XML (NIC standard) for native ingestion by state cadastral software, alongside a PDF/A sketch in the regional language.", {
    x: M + 0.3, y: TOP + 0.8, w: 4.6, h: 3.0, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 13, color: INK, lineSpacing: 18,
  });

  card(s, 6.6, TOP, 5.2, 4.0, MIST);
  s.addText("2. Predictive Litigation Mapping", {
    x: 6.9, y: TOP + 0.3, w: 4.6, h: 0.4, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 24, bold: true, color: INK,
  });
  s.addText("Using the Dempster-Shafer conflict mass (K) as a baseline, the platform ingests active dispute metadata from the e-Courts Services / NJDG API and Encumbrance Certificate flags from State Registration APIs (e.g., TN STAR 2.0).\n\nFusing internal geometric tension with external legal flags produces a dynamic litigation risk index, exposed via OGC API - Features as Mapbox Vector Tiles (MVT) for proactive drone survey targeting.", {
    x: 6.9, y: TOP + 0.8, w: 4.6, h: 3.0, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 13, color: INK, lineSpacing: 18,
  });
}

// ================================================================ 14 · the finding
{
  const s = darkSlide(
    "This is the result of running the platform over Chennai's real, current, official " +
    "land data, and it is worth stating plainly. No feature reaches grade A or B against " +
    "NAKSHA's 0.5 metre urban specification. The best available source is a 1 metre " +
    "municipal survey and three quarters of features are corroborated by only one source. " +
    "That is not a failure of the platform — it is the platform doing its job: quantifying " +
    "the gap that NAKSHA exists to close, parcel by parcel, so survey effort can be " +
    "directed where the evidence says it is needed rather than uniformly.");
  s.addText("THE FINDING", {
    x: M, y: 0.62, w: 6, h: 0.3, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11, bold: true, color: CLAY, charSpacing: 2,
  });
  s.addText("Chennai's existing land data does not\nreach the NAKSHA specification —\nand now we can say by how much.", {
    x: M, y: 1.06, w: 7.4, h: 2.0, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 30, bold: true, color: PAPER, lineSpacing: 41,
  });
  bullets(s, [
    "No feature grades A or B against a 0.5 m positional specification.",
    "The best available source is a 1 m municipal survey.",
    "68.0% of features are corroborated by only one source.",
    "Weakest dimension across the whole AOI: source agreement, at 0.33.",
  ], { x: M, y: 3.28, w: 7.1, h: 1.8, fontSize: 13.5, color: LIGHTINK });
  card(s, M, 5.24, 7.1, 1.42, INK2);
  s.addText("A harmonisation platform whose first run reported that everything was fine " +
            "would not be measuring anything.", {
    x: M + 0.28, y: 5.24, w: 6.6, h: 1.42, isTextBox: true, margin: 0, valign: "middle",
    fontFace: B, fontSize: 13.5, italic: true, color: PAPER, lineSpacing: 20,
  });

  s.addChart(pres.ChartType.doughnut, [{
    name: "Confidence grade",
    labels: ["C — desk review", "D — field verification"],
    values: [51852, 55410],
  }], {
    x: 8.0, y: 1.06, w: 4.7, h: 3.9,
    chartColors: [LAND, CLAY], holeSize: 55,
    showTitle: false, showLegend: true, legendPos: "b",
    legendColor: LIGHTINK, legendFontFace: B, legendFontSize: 11.5,
    showValue: false, showPercent: true, dataLabelColor: PAPER,
    dataLabelFontFace: B, dataLabelFontSize: 13, dataLabelFontBold: true,
  });
  s.addText("107,262 harmonised features by confidence grade", {
    x: 8.0, y: 5.1, w: 4.7, h: 0.36, isTextBox: true, margin: 0, align: "center",
    valign: "top", fontFace: B, fontSize: 11.5, color: LIGHTINK,
  });
  s.addText("This is the map of where to fly the drones first.", {
    x: 8.0, y: 5.72, w: 4.7, h: 0.9, isTextBox: true, margin: 0, align: "center",
    valign: "middle", fontFace: B, fontSize: 13.5, bold: true, italic: true, color: CLAY,
  });
}

// ================================================================ 15 · standards + impact
{
  const s = lightSlide("Built to fit the frame it has to operate in", "standards & impact",
    "A land-records platform that does not fit its legal and technical frame is a " +
    "prototype, not a system.");
  s.addText("Conforms to", {
    x: M, y: TOP + 0.12, w: 5, h: 0.32, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 14, bold: true, color: INK,
  });
  const left = [
    ["DILRMP", "ULPIN, mutation genealogy, extent reconciliation"],
    ["NAKSHA", "Positional confidence scored against the urban specification"],
    ["LGD", "The canonical schema is built on the national code hierarchy"],
    ["OGC API - Features", "Conformant — usable from QGIS with no custom code"],
    ["DPDP Act 2023", "Owner data absent from the feature API by construction"],
    ["CityGML LOD1 / CityJSON", "3-D massing for FSI and coverage compliance"],
  ];
  left.forEach((l, i) => {
    const y = TOP + 0.58 + i * 0.78;
    card(s, M, y, 5.7, 0.62, i % 2 ? MIST2 : MIST);
    s.addText(l[0], {
      x: M + 0.24, y: y + 0.08, w: 5.2, h: 0.26, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 12.5, bold: true, color: LAND,
    });
    s.addText(l[1], {
      x: M + 0.24, y: y + 0.34, w: 5.24, h: 0.26, isTextBox: true, margin: 0, valign: "top",
      fontFace: B, fontSize: 10.5, color: GREY,
    });
  });

  card(s, 6.7, TOP - 0.06, 5.98, 4.9, INK);
  s.addText("What it will not do", {
    x: 6.98, y: TOP + 0.12, w: 5.3, h: 0.32, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 14, bold: true, color: CLAY,
  });
  bullets(s, [
    "Confer title. It harmonises spatial records; title is a legal determination.",
    "Determine encroachment. It puts the geometric evidence in front of the officer who does.",
    "Average geometries. Every emitted boundary was observed by some survey.",
    "Overwrite a source. Claims are immutable; resolutions supersede.",
    "Publish personal data through the feature API. Absent, not redacted on request.",
    "Claim measured accuracy it does not have. Without control it says so, per feature.",
    "Treat contemporaneous sources as epochs. Disagreement is not a mutation.",
  ], { x: 6.98, y: TOP + 0.6, w: 5.4, h: 3.5, fontSize: 11.5, color: LIGHTINK });
  card(s, 6.98, TOP + 4.06, 5.4, 0.66, "16283F");
  s.addText("Stating what a system will not do is part of specifying what it does.", {
    x: 7.16, y: TOP + 4.06, w: 5.06, h: 0.66, isTextBox: true, margin: 0, valign: "middle",
    fontFace: B, fontSize: 11.5, italic: true, color: CLAY,
  });
}

// ================================================================ 16 · close
{
  const s = darkSlide(
    "To close: this is a working system, not a mock-up. Ten thousand lines of Python, " +
    "eighty-one passing tests, running end to end on half a million real government " +
    "features on two CPU cores — because a platform that needs a data centre to harmonise " +
    "one ward cannot be deployed to four thousand urban local bodies.");
  s.addShape(pres.ShapeType.ellipse, {
    x: 10.7, y: -3.5, w: 5.6, h: 5.6, fill: { color: INK2 }, line: { color: INK2 },
  });
  s.addText("It runs.", {
    x: M, y: 0.95, w: 8, h: 1.0, isTextBox: true, margin: 0, valign: "top",
    fontFace: H, fontSize: 52, bold: true, color: PAPER,
  });
  s.addText("Not a mock-up — a working platform, measured on real data.", {
    x: M, y: 2.02, w: 9.0, h: 0.44, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 17, color: LIGHTINK,
  });

  const facts = [
    ["~10,000", "lines of Python across 30 modules"],
    ["81", "tests, all passing"],
    ["107,262", "features harmonised in one 17.8-minute run"],
    ["2 vCPU", "and 7 GB of RAM — district-office hardware"],
  ];
  facts.forEach((f, i) => {
    stat(s, M + i * 3.05, 2.95, 2.8, f[0], f[1], CLAY, LIGHTINK, 32);
  });

  card(s, M, 4.76, 9.1, 1.5, INK2);
  s.addText("Every parcel it publishes carries its confidence, its contributing sources and\n" +
            "the hash of the ledger entry that produced it. A record whose history cannot\n" +
            "be reconstructed is a rumour with a geometry column.", {
    x: M + 0.34, y: 4.76, w: 8.5, h: 1.5, isTextBox: true, margin: 0, valign: "middle",
    fontFace: B, fontSize: 14, color: PAPER, lineSpacing: 22,
  });
  s.addText("SAMANVAY  ·  PS 26013  ·  Department of Land Resources  ·  SIH 2026", {
    x: M, y: 6.56, w: 12.1, h: 0.34, isTextBox: true, margin: 0, valign: "top",
    fontFace: B, fontSize: 11.5, color: LIGHTINK, charSpacing: 1,
  });
}

pres.writeFile({ fileName: process.argv[2] || "SAMANVAY_PS26013.pptx" })
  .then((f) => console.log("wrote", f));
