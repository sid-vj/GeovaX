"""Indic transliteration and name normalisation for record linkage.

Owner names, village names and street names in Indian land records arrive in Tamil,
Devanagari, Telugu, Kannada, Malayalam, Bengali, Gujarati, Gurmukhi, Odia — and in a dozen
different romanisations of the same name. The revenue record says ``இராமசாமி``, the
municipal property tax roll says ``Ramaswamy``, the utility connection says ``Ramasami``,
and the encumbrance certificate says ``R. Swamy``. All four are one person, and until the
platform can say so the records cannot be linked.

This module does three things:

1. **Script detection** by Unicode block.
2. **Transliteration to Latin** using ISO 15919-style mappings, implemented directly so
   the platform carries no heavyweight dependency for a table lookup.
3. **Normalisation to a comparison key** that absorbs the specific ways Indian names vary
   in romanisation: aspirate doubling (th/t), retroflex/dental collapse, v/w, s/sh/z,
   ee/i, oo/u, silent h, and the honorific and initial soup that surrounds a name in a
   land record.

The output is deliberately *lossy*. It is a linkage key, not a display name; the original
is always preserved alongside it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# --------------------------------------------------------------------------------------
# script detection
# --------------------------------------------------------------------------------------

SCRIPT_RANGES: list[tuple[str, int, int]] = [
    ("devanagari", 0x0900, 0x097F),
    ("bengali", 0x0980, 0x09FF),
    ("gurmukhi", 0x0A00, 0x0A7F),
    ("gujarati", 0x0A80, 0x0AFF),
    ("oriya", 0x0B00, 0x0B7F),
    ("tamil", 0x0B80, 0x0BFF),
    ("telugu", 0x0C00, 0x0C7F),
    ("kannada", 0x0C80, 0x0CFF),
    ("malayalam", 0x0D00, 0x0D7F),
    ("sinhala", 0x0D80, 0x0DFF),
    ("arabic", 0x0600, 0x06FF),
    ("latin", 0x0041, 0x024F),
]


def detect_script(text: str) -> str:
    counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        for name, lo, hi in SCRIPT_RANGES:
            if lo <= cp <= hi:
                counts[name] = counts.get(name, 0) + 1
                break
    if not counts:
        return "unknown"
    return max(counts, key=counts.get)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# transliteration
# --------------------------------------------------------------------------------------

# Tamil is given in full because Tamil Nadu is the demonstration state; the other scripts
# use the shared Indic consonant/vowel ordering, which is regular enough to generate.
TAMIL_MAP = {
    "அ": "a", "ஆ": "aa", "இ": "i", "ஈ": "ii", "உ": "u", "ஊ": "uu",
    "எ": "e", "ஏ": "ee", "ஐ": "ai", "ஒ": "o", "ஓ": "oo", "ஔ": "au",
    "க": "ka", "ங": "nga", "ச": "cha", "ஞ": "nya", "ட": "ta", "ண": "na",
    "த": "tha", "ந": "na", "ப": "pa", "ம": "ma", "ய": "ya", "ர": "ra",
    "ல": "la", "வ": "va", "ழ": "zha", "ள": "la", "ற": "ra", "ன": "na",
    "ஜ": "ja", "ஷ": "sha", "ஸ": "sa", "ஹ": "ha", "க்ஷ": "ksha", "ஸ்ரீ": "sri",
    "ா": "aa", "ி": "i", "ீ": "ii", "ு": "u", "ூ": "uu", "ெ": "e",
    "ே": "ee", "ை": "ai", "ொ": "o", "ோ": "oo", "ௌ": "au", "்": "",
    "ஃ": "h",
}

DEVANAGARI_MAP = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ii", "उ": "u", "ऊ": "uu",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऋ": "ri",
    "क": "ka", "ख": "kha", "ग": "ga", "घ": "gha", "ङ": "nga",
    "च": "cha", "छ": "chha", "ज": "ja", "झ": "jha", "ञ": "nya",
    "ट": "ta", "ठ": "tha", "ड": "da", "ढ": "dha", "ण": "na",
    "त": "ta", "थ": "tha", "द": "da", "ध": "dha", "न": "na",
    "प": "pa", "फ": "pha", "ब": "ba", "भ": "bha", "म": "ma",
    "य": "ya", "र": "ra", "ल": "la", "व": "va", "श": "sha",
    "ष": "sha", "स": "sa", "ह": "ha", "ळ": "la",
    "ा": "aa", "ि": "i", "ी": "ii", "ु": "u", "ू": "uu", "े": "e",
    "ै": "ai", "ो": "o", "ौ": "au", "्": "", "ं": "n", "ः": "h", "ृ": "ri",
}

TELUGU_MAP = {
    "అ": "a", "ఆ": "aa", "ఇ": "i", "ఈ": "ii", "ఉ": "u", "ఊ": "uu",
    "ఎ": "e", "ఏ": "ee", "ఐ": "ai", "ఒ": "o", "ఓ": "oo", "ఔ": "au",
    "క": "ka", "ఖ": "kha", "గ": "ga", "ఘ": "gha", "చ": "cha", "జ": "ja",
    "ట": "ta", "డ": "da", "ణ": "na", "త": "ta", "థ": "tha", "ద": "da",
    "ధ": "dha", "న": "na", "ప": "pa", "ఫ": "pha", "బ": "ba", "భ": "bha",
    "మ": "ma", "య": "ya", "ర": "ra", "ల": "la", "వ": "va", "శ": "sha",
    "ష": "sha", "స": "sa", "హ": "ha", "ళ": "la",
    "ా": "aa", "ి": "i", "ీ": "ii", "ు": "u", "ూ": "uu", "ె": "e",
    "ే": "ee", "ై": "ai", "ొ": "o", "ో": "oo", "్": "", "ం": "n",
}

KANNADA_MAP = {
    "ಅ": "a", "ಆ": "aa", "ಇ": "i", "ಈ": "ii", "ಉ": "u", "ಊ": "uu",
    "ಎ": "e", "ಏ": "ee", "ಐ": "ai", "ಒ": "o", "ಓ": "oo",
    "ಕ": "ka", "ಖ": "kha", "ಗ": "ga", "ಘ": "gha", "ಚ": "cha", "ಜ": "ja",
    "ಟ": "ta", "ಡ": "da", "ಣ": "na", "ತ": "ta", "ಥ": "tha", "ದ": "da",
    "ಧ": "dha", "ನ": "na", "ಪ": "pa", "ಫ": "pha", "ಬ": "ba", "ಭ": "bha",
    "ಮ": "ma", "ಯ": "ya", "ರ": "ra", "ಲ": "la", "ವ": "va", "ಶ": "sha",
    "ಷ": "sha", "ಸ": "sa", "ಹ": "ha", "ಳ": "la",
    "ಾ": "aa", "ಿ": "i", "ೀ": "ii", "ು": "u", "ೂ": "uu", "ೆ": "e",
    "ೇ": "ee", "ೈ": "ai", "ೊ": "o", "ೋ": "oo", "್": "", "ಂ": "n",
}

MALAYALAM_MAP = {
    "അ": "a", "ആ": "aa", "ഇ": "i", "ഈ": "ii", "ഉ": "u", "ഊ": "uu",
    "എ": "e", "ഏ": "ee", "ഐ": "ai", "ഒ": "o", "ഓ": "oo",
    "ക": "ka", "ഖ": "kha", "ഗ": "ga", "ച": "cha", "ജ": "ja", "ട": "ta",
    "ഡ": "da", "ണ": "na", "ത": "tha", "ദ": "da", "ന": "na", "പ": "pa",
    "ബ": "ba", "മ": "ma", "യ": "ya", "ര": "ra", "ല": "la", "വ": "va",
    "ശ": "sha", "ഷ": "sha", "സ": "sa", "ഹ": "ha", "ള": "la", "ഴ": "zha", "റ": "ra",
    "ാ": "aa", "ി": "i", "ീ": "ii", "ു": "u", "ൂ": "uu", "െ": "e",
    "േ": "ee", "ൈ": "ai", "ൊ": "o", "ോ": "oo", "്": "", "ം": "m",
}

SCRIPT_MAPS = {
    "tamil": TAMIL_MAP,
    "devanagari": DEVANAGARI_MAP,
    "telugu": TELUGU_MAP,
    "kannada": KANNADA_MAP,
    "malayalam": MALAYALAM_MAP,
}


def transliterate(text: str, script: str | None = None) -> str:
    """Transliterate Indic text to Latin. Latin input is returned unchanged."""
    if not text:
        return ""
    script = script or detect_script(text)
    table = SCRIPT_MAPS.get(script)
    if table is None:
        return text
    out: list[str] = []
    i = 0
    # longest-match first so conjuncts like க்ஷ / ஸ்ரீ win over their parts
    keys = sorted(table, key=len, reverse=True)
    while i < len(text):
        for k in keys:
            if text.startswith(k, i):
                out.append(table[k])
                i += len(k)
                break
        else:
            out.append(text[i])
            i += 1
    result = "".join(out)
    # a virama zeroes the inherent vowel of the preceding consonant; the table already
    # emits "" for it, which leaves "kaa" style artefacts. Collapse them.
    result = re.sub(r"a(?=[aeiou])", "", result)
    return result


# --------------------------------------------------------------------------------------
# normalisation for linkage
# --------------------------------------------------------------------------------------

HONORIFICS = {
    "mr", "mrs", "ms", "miss", "shri", "sri", "smt", "srimathi", "srimati", "thiru",
    "thirumathi", "tmt", "selvi", "selvan", "dr", "prof", "er", "adv", "late", "m/s",
    "messrs", "sardar", "sardarni", "haji", "hajji", "syed", "sheikh", "mohd", "md",
    "kum", "kumari", "shrimati", "janab", "begum",
}

RELATION_MARKERS = {
    "s", "so", "son", "w", "wo", "wife", "d", "do", "daughter", "h", "ho",
    "husband", "f", "fo", "father", "alias", "urf", "@",
}

_EQUIV = [
    (r"aa+", "a"), (r"ee+", "i"), (r"oo+", "u"), (r"ii+", "i"), (r"uu+", "u"),
    (r"th", "t"), (r"dh", "d"), (r"bh", "b"), (r"gh", "g"), (r"kh", "k"),
    (r"ph", "f"), (r"ch", "c"), (r"sh", "s"), (r"zh", "l"), (r"z", "s"),
    (r"w", "v"), (r"y$", "i"), (r"ck", "k"), (r"x", "ks"), (r"q", "k"),
    (r"j", "g"), (r"([bcdfghklmnprstv])\1+", r"\1"),
]


@dataclass(frozen=True)
class NormalisedName:
    original: str
    script: str
    latin: str
    tokens: tuple[str, ...]
    key: str
    """The comparison key: sorted, phonetically folded tokens."""
    initials: str

    def similarity(self, other: "NormalisedName") -> float:
        """0..1 similarity designed for Indian personal names.

        Blends three signals because none alone is sufficient:
        token-set overlap (handles reordering and dropped patronymics), the folded-key
        edit distance (handles spelling variation), and initial agreement (handles the
        very common "R. Swamy" against "Ramaswamy Rajan" case).
        """
        from rapidfuzz.distance import JaroWinkler

        if not self.tokens or not other.tokens:
            return 0.0
        a, b = set(self.tokens), set(other.tokens)
        jaccard = len(a & b) / len(a | b)
        key_sim = JaroWinkler.similarity(self.key, other.key)
        init = 1.0 if self.initials and self.initials == other.initials else (
            0.5 if self.initials and other.initials and
            (self.initials in other.initials or other.initials in self.initials) else 0.0
        )
        # best-token pairing catches one strongly matching surname among noise
        best_pair = max(
            (JaroWinkler.similarity(t1, t2) for t1 in self.tokens for t2 in other.tokens),
            default=0.0,
        )
        return round(0.32 * jaccard + 0.34 * key_sim + 0.24 * best_pair + 0.10 * init, 4)


def fold(token: str) -> str:
    """Collapse a romanised token into its phonetic skeleton."""
    t = token.lower()
    for pat, rep in _EQUIV:
        t = re.sub(pat, rep, t)
    t = re.sub(r"[^a-z]", "", t)
    t = re.sub(r"(?<=.)[aeiou]", "", t) if len(t) > 4 else t
    return t


def normalise_name(raw: str) -> NormalisedName:
    if raw is None:
        raw = ""
    text = unicodedata.normalize("NFC", str(raw)).strip()
    script = detect_script(text)
    latin = transliterate(text, script) if script not in ("latin", "unknown") else text
    cleaned = re.sub(r"[^\w\s@./-]", " ", latin.lower())
    cleaned = re.sub(r"[./-]", " ", cleaned)
    parts = [p for p in cleaned.split() if p]

    tokens: list[str] = []
    initials: list[str] = []
    for p in parts:
        if p in HONORIFICS or p in RELATION_MARKERS:
            continue
        if len(p) == 1 and p.isalpha():
            initials.append(p)
            continue
        tokens.append(p)
    folded = tuple(sorted({fold(t) for t in tokens if fold(t)}))
    return NormalisedName(
        original=str(raw),
        script=script,
        latin=latin,
        tokens=folded,
        key="".join(folded),
        initials="".join(sorted(initials)),
    )


def normalise_place(raw: str) -> str:
    """Normalise a village / locality / street name for joining.

    Place names carry a different noise profile from personal names: administrative
    suffixes, spelling drift, and the colonial-versus-current name split (Madras vs
    Chennai, Trichy vs Tiruchirappalli). The suffix list below is the set that actually
    appears in Tamil Nadu and neighbouring revenue records.
    """
    if not raw:
        return ""
    text = transliterate(unicodedata.normalize("NFC", str(raw)))
    t = re.sub(r"[^\w\s]", " ", text.lower())
    drop = {
        "village", "vill", "gram", "panchayat", "taluk", "taluka", "tehsil", "mandal",
        "district", "dist", "town", "city", "post", "po", "colony", "nagar", "puram",
        "pettai", "pakkam", "kuppam", "extn", "extension", "phase", "block", "sector",
        "ward", "zone", "north", "south", "east", "west", "new", "old",
    }
    toks = [fold(w) for w in t.split() if w not in drop and len(w) > 1]
    return " ".join(t for t in toks if t)


PLACE_ALIASES = {
    "madras": "chennai", "trichy": "tiruchirappalli", "trichinopoly": "tiruchirappalli",
    "tanjore": "thanjavur", "tuticorin": "thoothukudi", "conjeevaram": "kancheepuram",
    "ootacamund": "udhagamandalam", "ooty": "udhagamandalam", "vellore": "vellore",
    "cuddalore": "cuddalore", "coimbatore": "coimbatore", "bangalore": "bengaluru",
    "mysore": "mysuru", "calcutta": "kolkata", "bombay": "mumbai", "poona": "pune",
    "baroda": "vadodara", "cochin": "kochi", "trivandrum": "thiruvananthapuram",
    "pondicherry": "puducherry", "quilon": "kollam", "calicut": "kozhikode",
}


def canonical_place(raw: str) -> str:
    """Resolve a place name to its current canonical form.

    Alias resolution happens *before* phonetic folding, not after. Folding is lossy by
    design — "Madras" becomes "mdrs" — and an alias table keyed on real names can never
    match a folded key. Getting this order wrong silently breaks every join between a
    colonial-era revenue record and a current municipal one.
    """
    import re as _re

    plain = _re.sub(r"[^a-z ]", " ", str(raw or "").lower()).strip()
    if plain in PLACE_ALIASES:
        return normalise_place(PLACE_ALIASES[plain])
    for token in plain.split():
        if token in PLACE_ALIASES:
            return normalise_place(PLACE_ALIASES[token])
    return normalise_place(raw)
