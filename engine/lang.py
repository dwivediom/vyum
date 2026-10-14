"""Indian languages: which language a question is in, how the answer should be written, and the few fixed phrases the
harness says itself. Detection is by script (Devanagari, Bengali, Tamil, …) plus a word list for Hinglish (Hindi in
Latin letters), which is how many people type Hindi on a phone or laptop."""
import re

SCRIPTS = [  # (Unicode block, language code) — the first block with enough letters wins
    ("ऀ-ॿ", "hi"), ("ঀ-৿", "bn"), ("਀-੿", "pa"), ("઀-૿", "gu"),
    ("଀-୿", "or"), ("஀-௿", "ta"), ("ఀ-౿", "te"), ("ಀ-೿", "kn"),
    ("ഀ-ൿ", "ml"), ("؀-ۿ", "ur"),
]
NAMES = {"en": "English", "hi": "Hindi", "mr": "Marathi", "bn": "Bengali", "pa": "Punjabi", "gu": "Gujarati", "or": "Odia",
         "ta": "Tamil", "te": "Telugu", "kn": "Kannada", "ml": "Malayalam", "ur": "Urdu", "hinglish": "Hinglish"}
NATIVE = {"hi": "हिन्दी", "mr": "मराठी", "bn": "বাংলা", "pa": "ਪੰਜਾਬੀ", "gu": "ગુજરાતી", "or": "ଓଡ଼ିଆ", "ta": "தமிழ்", "te": "తెలుగు",
          "kn": "ಕನ್ನಡ", "ml": "മലയാളം", "ur": "اردو"}
_MARATHI = re.compile(r"(आहे|आहेत|आणि|नाही|मध्ये|काय|किती|कोण|होते|करा|आपण|त्या|ह्या|हे\s)")
_HINGLISH = set("""kya kyaa hai hain hoga hogi tha thi kitna kitne kitni kaise kaisa kaun kab kahan kyun kyon kyu mein mai mujhe mera meri mere humein hamara aap aapka aapki batao bataiye bata btao chahiye karo karna karein kar nahi nahin haan ji wala wali wale yeh ye woh wo isme usme iska uska iski uski liye ke ki ka ko se aur kuch dikhao samjhao likho bhejo milega milegi kaam paisa rupaye mahine saal""".split())  # words that are also common English (the, me, main…) are left out
_STRONG = set("kya kyaa kitna kitne kitni kaise kaisa kaun kyun kyon batao bataiye btao chahiye samjhao dikhao hain nahi nahin".split())
_DIGITS = {ord(z) + i: str(i) for z in "०০੦૦୦௦౦೦൦٠" for i in range(10)}
GAP = {
    "en": "The documents do not state", "hi": "दस्तावेज़ों में यह नहीं बताया गया", "hinglish": "Documents mein yeh nahi bataya gaya",
    "mr": "कागदपत्रांमध्ये हे सांगितलेले नाही",
}
NOT_FOUND = {
    "en": ("Not found in these documents.", "No passage in the documents is relevant enough."),
    "hi": ("इन दस्तावेज़ों में यह जानकारी नहीं मिली।", "दस्तावेज़ों में इससे जुड़ा कोई हिस्सा नहीं मिला।"),
    "hinglish": ("In documents mein yeh jaankari nahi mili.", "Documents mein isse juda koi hissa nahi mila."),
    "mr": ("या कागदपत्रांमध्ये ही माहिती सापडली नाही.", "कागदपत्रांमध्ये याच्याशी संबंधित भाग सापडला नाही."),
    "bn": ("এই নথিগুলিতে এই তথ্য পাওয়া যায়নি।", "নথিতে প্রাসঙ্গিক কোনো অংশ পাওয়া যায়নি।"),
    "ta": ("இந்த ஆவணங்களில் இந்தத் தகவல் இல்லை.", "ஆவணங்களில் தொடர்புடைய பகுதி கிடைக்கவில்லை."),
    "te": ("ఈ పత్రాలలో ఈ సమాచారం లేదు.", "పత్రాలలో సంబంధిత భాగం దొరకలేదు."),
    "gu": ("આ દસ્તાવેજોમાં આ માહિતી મળી નથી.", "દસ્તાવેજોમાં સંબંધિત ભાગ મળ્યો નથી."),
    "kn": ("ಈ ದಾಖಲೆಗಳಲ್ಲಿ ಈ ಮಾಹಿತಿ ಸಿಗಲಿಲ್ಲ.", "ದಾಖಲೆಗಳಲ್ಲಿ ಸಂಬಂಧಿತ ಭಾಗ ಸಿಗಲಿಲ್ಲ."),
    "ml": ("ഈ രേഖകളിൽ ഈ വിവരം ഇല്ല.", "രേഖകളിൽ ബന്ധപ്പെട്ട ഭാഗം കണ്ടെത്തിയില്ല."),
    "pa": ("ਇਨ੍ਹਾਂ ਦਸਤਾਵੇਜ਼ਾਂ ਵਿੱਚ ਇਹ ਜਾਣਕਾਰੀ ਨਹੀਂ ਮਿਲੀ।", "ਦਸਤਾਵੇਜ਼ਾਂ ਵਿੱਚ ਸੰਬੰਧਿਤ ਹਿੱਸਾ ਨਹੀਂ ਮਿਲਿਆ।"),
    "ur": ("ان دستاویزات میں یہ معلومات نہیں ملیں۔", "دستاویزات میں متعلقہ حصہ نہیں ملا۔"),
}


def detect(text: str) -> str:
    """Language code of a question: an Indian script, Hinglish, or English."""
    text = text or ""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    for block, code in SCRIPTS:
        n = len(re.findall(f"[{block}]", text))
        if n >= 2 and n >= 0.25 * len(letters):
            if code == "hi" and _MARATHI.search(text):
                return "mr"
            if code == "ur" and not re.search("[ٹڈڑںےۓھ]", text):
                continue  # Arabic, not Urdu
            return code
    words = re.findall(r"[a-z]+", text.lower())
    hits = sum(w in _HINGLISH for w in words)
    strong = any(w in _STRONG for w in words)
    if words and ((hits >= 2 and hits >= 0.2 * len(words)) or (strong and len(words) <= 8)):
        return "hinglish"
    return "en"


def ascii_digits(text: str) -> str:
    """०१२३ → 0123 (and the other Indian scripts' digits), so numbers match across scripts."""
    return (text or "").translate(_DIGITS)


def answer_rule(code: str) -> str:
    """The line added to a system prompt so the answer comes back in the user's language."""
    if code == "en":
        return ""
    if code == "hinglish":
        return ("\n- The user writes in Hinglish (Hindi in Latin letters). Answer in the same simple Hinglish, in Latin (English) "
                "letters only, never Devanagari, even when the sources are written in Devanagari. "
                "Write numbers as digits. Keep numbers, dates, amounts, names and document terms exactly as the sources write them, "
                "in English. End every sentence with its citation, like [1]."
                f'\n- To say a part of the question is not answered, start that sentence with "{GAP["hinglish"]}".')
    name = NAMES.get(code, code)
    rule = (f"\n- The user writes in {name}. Answer in natural, simple, everyday {name} ({NATIVE.get(code, name)} script), even when "
            f"the sources are in English, the way an educated Indian writes it at work."
            f"\n- Write every number as digits (45, 12,400, 1.5%), never as words. Copy dates and amounts exactly."
            f"\n- Translate limits and comparisons exactly: fewer than 250 = 250 से कम (not कम से कम 250, which means at least 250); "
            f"at least = कम से कम; more than = से ज़्यादा; at most / up to = अधिकतम / तक. Check every such phrase before you answer."
            f"\n- Keep names of companies, people, products, laws and technical or legal terms in their original English spelling "
            f"(Acme, GDPR, invoice, calendar year) unless you are sure of the common {name} word. Never guess a translation."
            f"\n- End every sentence with its citation, like [1]।")
    if code in GAP:
        rule += f'\n- To say a part of the question is not answered, start that sentence with "{GAP[code]}".'
    return rule


def is_gap(sentence: str) -> bool:
    s = sentence.strip()
    return any(s.startswith(p) for p in GAP.values())


def not_found(code: str) -> tuple[str, str]:
    return NOT_FOUND.get(code) or NOT_FOUND["en"]


def indian_number(x: float, decimals: int = 0) -> str:
    """1,20,37,718 — lakh and crore grouping, as Indian readers expect."""
    neg = x < 0
    s = f"{abs(x):.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1] if head else ""
        whole = f"{head},{tail}"
    return ("-" if neg else "") + whole + (f".{frac}" if frac else "")


_LOOSE_SIGN = re.compile(r"(?<=[\u0900-\u097F]) +(?=[\u0900-\u0903\u093A-\u094F\u0951-\u0957\u0962\u0963])")


def fix_devanagari(text: str) -> str:
    """PDF text often splits a Hindi word before its vowel sign ("म ं ज़ूरी"); a vowel sign never starts a word."""
    return _LOOSE_SIGN.sub("", text) if text and any("\u0900" <= c <= "\u097f" for c in text) else text


def devanagari_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum("\u0900" <= c <= "\u097f" for c in letters) / max(len(letters), 1)


_KRUTI = re.compile(r"(?<![A-Za-z])(gS|esa|vkSj|dks|ls|ds|dh|dk|fd|;g|gSa|fy,|Fkk|Hkh|ugha|djsa|tkus)(?![A-Za-z])")


def looks_like_kruti_dev(text: str) -> bool:
    """Kruti Dev (an old, non-Unicode Hindi font common in Indian government PDFs) stores Hindi as Latin letters:
    है comes out as "gS", में as "esa". Such pages look right but their text is gibberish, so they are read by OCR."""
    words = re.findall(r"\S+", text or "")
    if len(words) < 30:
        return False
    return len(_KRUTI.findall(text)) >= max(6, 0.08 * len(words))


# limits and comparisons: "fewer than 250" must never come back as "at least 250" in any language ------------------
_N = r"(\d[\d,]*(?:\.\d+)?)"
_SOURCE_LIMITS = [  # English source wording -> direction
    ("ge", rf"\b(?:at least|no fewer than|not fewer than|not less than|minimum of|a minimum of)\s+{_N}"),
    ("le", rf"\b(?:up to|at most|no more than|not more than|maximum of|a maximum of|not exceeding)\s+{_N}"),
    ("lt", rf"\b(?:fewer than|less than|below|under)\s+{_N}"),
    ("gt", rf"\b(?:more than|over|exceeding|in excess of|above)\s+{_N}"),
]
_CLAIM_LIMITS = {
    "hi": [("ge", rf"(?:कम से कम|न्यूनतम)\s+{_N}"), ("le", rf"(?:अधिकतम|ज़्यादा से ज़्यादा|ज्यादा से ज्यादा)\s+{_N}"),
           ("lt", rf"{_N}\s+(?:\S+\s+)?से\s+कम"), ("gt", rf"{_N}\s+(?:\S+\s+)?से\s+(?:ज़्यादा|ज्यादा|अधिक)")],
    "hinglish": [("ge", rf"(?:kam se kam|minimum)\s+{_N}"), ("le", rf"(?:zyada se zyada|jyada se jyada|maximum)\s+{_N}"),
                 ("lt", rf"{_N}\s+(?:\w+\s+)?se\s+kam"), ("gt", rf"{_N}\s+(?:\w+\s+)?se\s+(?:zyada|jyada|adhik)")],
}
_CLAIM_LIMITS["mr"] = _CLAIM_LIMITS["hi"]
_PHRASE = {
    "hi": {"lt": "{n} से कम", "ge": "कम से कम {n}", "gt": "{n} से ज़्यादा", "le": "अधिकतम {n}"},
    "hinglish": {"lt": "{n} se kam", "ge": "kam se kam {n}", "gt": "{n} se zyada", "le": "zyada se zyada {n}"},
    "en": {"lt": "fewer than {n}", "ge": "at least {n}", "gt": "more than {n}", "le": "at most {n}"},
}
_PHRASE["mr"] = _PHRASE["hi"]


def _key(n: str) -> str:
    return n.replace(",", "")


def source_limits(text: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for direction, pat in _SOURCE_LIMITS:
        for m in re.finditer(pat, text, re.I):
            out.setdefault(_key(m.group(1)), set()).add(direction)
    return out


def fix_limits(claim: str, sources_text: str, code: str) -> tuple[str, list[str]]:
    """Rewrites a limit in the claim when the source states the opposite limit on the same number.
    Returns (claim, list of fixes) — only when the source is unambiguous about that number."""
    src = source_limits(ascii_digits(sources_text))
    if not src:
        return claim, []
    pats = _CLAIM_LIMITS.get(code) or [(d, p) for d, p in _SOURCE_LIMITS]
    phrases = _PHRASE.get(code, _PHRASE["en"])
    fixes = []
    for direction, pat in pats:
        for m in list(re.finditer(pat, claim, re.I)):
            n = _key(m.group(1))
            want = src.get(n)
            if want and len(want) == 1 and direction not in want:
                right = phrases[next(iter(want))].format(n=m.group(1))
                if direction in ("lt", "gt") and code in ("hi", "mr", "hinglish"):
                    # "250 कर्मचारियों से कम": keep the words between the number and the limit
                    middle = claim[m.start(1) + len(m.group(1)):m.end()]
                    noun = re.sub(r"\s*(से\s+(कम|ज़्यादा|ज्यादा|अधिक)|se\s+(kam|zyada|jyada|adhik))\s*$", "", middle).strip()
                    right = phrases[next(iter(want))].format(n=f"{m.group(1)} {noun}".strip()) if noun else right
                claim = claim[:m.start()] + right + claim[m.end():]
                fixes.append(f"{m.group(0)} → {right}")
    return claim, fixes
