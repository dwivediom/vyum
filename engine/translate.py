"""Translation for answers in Indian languages, with a check on every sentence.

Vyum answers a Hindi question in three steps: translate the question to English, answer and verify in English (where
the harness is strongest), then translate the verified sentences back. A translated sentence is shown only if its
numbers and codes survived, its limits kept their direction ("fewer than 250" must not become "कम से कम 250"), and a
translation back into English still says the same thing. Otherwise it is translated again, and failing that the
verified English sentence is shown instead of a doubtful translation.

Backends: IndicTrans2 (AI4Bharat, MIT) when its models are on disk; otherwise the local LLM."""
import json
import os
import re
import subprocess
import threading
from pathlib import Path

from . import config, lang, llm

TRANSLATED = {"hi", "mr", "bn", "ta", "te", "gu", "kn", "ml", "pa", "or", "ur"}  # Hinglish is written directly
_IT2 = {"hi": "hin_Deva", "mr": "mar_Deva", "bn": "ben_Beng", "ta": "tam_Taml", "te": "tel_Telu", "gu": "guj_Gujr",
        "kn": "kan_Knda", "ml": "mal_Mlym", "pa": "pan_Guru", "or": "ory_Orya", "ur": "urd_Arab"}
_lock = threading.Lock()

TO_ENGLISH = """Translate the user's question into English for a document search. Keep names, numbers, codes, amounts and
dates exactly as written. Keep English words the user mixed in. Return JSON only: {"english": "..."}"""

FROM_ENGLISH = """You translate short, verified answer sentences from English into {name} ({native} script) for an Indian reader.
Return JSON only: {{"translations": ["...", ...]}} with exactly one translation per input sentence, in order.
Rules:
- Natural, simple, everyday {name}, the way an educated person writes it at work. Short sentences.
- Copy every number, amount, percentage, date, code and ID exactly, as Western digits (45, 12,400, 1.5%, 30 October 2026, QX-4471-B).
  Never write numbers as words.
- Keep names of companies, people, products, laws and technical or legal terms in English letters (Acme, GDPR, invoice,
  calendar year, liability cap) unless you are certain of the common {name} word. Never guess a translation.
- Keep every limit exactly: fewer than 250 = 250 से कम; at least 250 = कम से कम 250; more than = से ज़्यादा; at most / up to = अधिकतम / तक.
- Translate only what the sentence says. Add nothing, drop nothing.{hint}"""

BACK = """Translate each {name} sentence into English, word for word in meaning. Return JSON only: {{"english": ["...", ...]}}"""

SAME = """For each pair, decide whether sentence B says exactly the same facts as sentence A: the same numbers, limits
(fewer than / at least / more than / at most), dates, names, conditions, qualifiers and negations. Wording may differ, but
any changed condition or qualifier makes it different: "late payments" vs "insufficient payments", "paid leave" vs
"unpaid leave", "within 30 days" vs "after 30 days" are all different.
Return JSON only: {"same": [true or false, ...]} with one entry per pair, in order."""


# backends ----------------------------------------------------------------------------------------------------------
def _it2_dir(direction: str) -> Path:
    return config.INDICTRANS_DIR / f"{direction}-dist-200M"


_PY = Path(__file__).resolve().parents[1] / ".venv-indictrans/bin/python"
_WORKER = Path(__file__).resolve().parent / "it2_worker.py"
_proc = None


def backend(direction: str = "en-indic") -> str:
    """IndicTrans2 for a direction once its model and its environment (scripts/setup_indictrans.sh) exist; else the LLM."""
    return "indictrans2" if (_it2_dir(direction) / "config.json").exists() and _PY.exists() else "llm"


def _it2_translate(texts: list[str], src: str, tgt: str) -> list[str]:
    """Through the worker process, started on first use and kept running with the model loaded."""
    global _proc
    with _lock:
        if _proc is None or _proc.poll() is not None:
            _proc = subprocess.Popen([str(_PY), "-I", str(_WORKER), str(config.INDICTRANS_DIR)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                     env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        _proc.stdin.write(json.dumps({"texts": texts, "src": src, "tgt": tgt}, ensure_ascii=False) + "\n")
        _proc.stdin.flush()
        reply = json.loads(_proc.stdout.readline() or '{"error": "worker stopped"}')
    if "error" in reply or len(reply.get("out") or []) != len(texts):
        raise RuntimeError(reply.get("error", "bad reply"))
    return [str(x) for x in reply["out"]]


def warm_up():
    """Starts the IndicTrans2 worker and loads its model in the background, so the first Hindi answer is not slowed."""
    if backend() != "indictrans2":
        return

    def run():
        try:
            _it2_translate(["Ready."], "eng_Latn", "hin_Deva")
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


# public ------------------------------------------------------------------------------------------------------------
HINGLISH_TO_ENGLISH = """The user's question is Hindi written in English letters (Hinglish), maybe mixed with English words. Rewrite
it as one plain, complete English question for a document search: translate every Hindi word ("chutti" = leave,
"hafte" = week, "kitne din" = how many days, "kab tak" = until when, "kaun" = who, "kitna" = how much, "batao" = tell me).
Keep names, numbers, codes, amounts and dates exactly. Return JSON only: {"english": "..."}"""


def question_to_english(question: str, code: str) -> str:
    if backend("indic-en") == "indictrans2" and code in _IT2:
        try:
            return _it2_translate([question], _IT2[code], "eng_Latn")[0]
        except Exception:
            pass
    rules = HINGLISH_TO_ENGLISH if code == "hinglish" else TO_ENGLISH
    data = llm.chat_json([{"role": "system", "content": rules}, {"role": "user", "content": question}], max_tokens=200)
    out = str(data.get("english") or "").strip()
    return out or question


# Business and legal terms with the Hindi a reader expects. Each sentence is translated with the entries it contains,
# so a term never depends on the model's guess ("late payment" once came back as "insufficient payment").
GLOSSARY_HI = {
    "late payment": "देर से भुगतान", "late payments": "देर से भुगतान", "paid annual leave": "सवेतन वार्षिक अवकाश",
    "paid leave": "सवेतन अवकाश", "annual leave": "वार्षिक अवकाश", "parental leave": "पैरेंटल अवकाश", "calendar year": "कैलेंडर वर्ष",
    "financial year": "वित्तीय वर्ष", "invoice date": "इनवॉइस की तारीख", "invoice": "इनवॉइस", "interest": "ब्याज",
    "per month": "प्रति माह", "per year": "प्रति वर्ष", "business days": "कार्य दिवस", "working days": "कार्य दिवस",
    "governing law": "लागू कानून", "the laws of": "के कानून", "apply to": "पर लागू होते हैं", "liability cap": "दायित्व की सीमा",
    "liability": "दायित्व", "warranty": "वारंटी", "warrants": "वारंटी देता है", "termination": "समाप्ति",
    "terminate": "समाप्त", "for convenience": "सुविधा के आधार पर", "notice": "नोटिस", "material breach": "गंभीर उल्लंघन",
    "breach": "उल्लंघन", "cure": "ठीक करना", "confidential information": "गोपनीय जानकारी", "unit price": "यूनिट मूल्य",
    "volume discount": "वॉल्यूम छूट", "delivery": "डिलीवरी", "deliver": "डिलीवर", "buyer": "खरीदार", "supplier": "आपूर्तिकर्ता",
    "agreement": "समझौता", "contract": "अनुबंध", "insurance": "बीमा", "penalty": "जुर्माना", "fine": "जुर्माना",
    "turnover": "टर्नओवर", "personal data": "व्यक्तिगत डेटा", "data subject": "डेटा विषय", "controller": "कंट्रोलर",
    "processor": "प्रोसेसर", "supervisory authority": "पर्यवेक्षी प्राधिकरण", "consent": "सहमति", "parental consent": "माता-पिता की सहमति",
    "data protection officer": "डेटा संरक्षण अधिकारी", "public authority": "सार्वजनिक प्राधिकरण", "public body": "सार्वजनिक निकाय",
    "large scale": "बड़े पैमाने", "machine-readable": "मशीन-पठनीय", "commonly used": "आम तौर पर इस्तेमाल होने वाले",
    "structured": "संरचित", "right to erasure": "मिटाने का अधिकार", "processing": "प्रोसेसिंग", "records": "रिकॉर्ड",
    "exempt": "छूट", "employees": "कर्मचारी", "remote work": "घर से काम", "work remotely": "घर से काम", "revenue": "राजस्व",
    "member state": "सदस्य देश", "member states": "सदस्य देश", "article": "अनुच्छेद", "unless": "जब तक कि ... न हो",
    "provided that": "बशर्ते कि", "right to be forgotten": "भुला दिए जाने का अधिकार", "special categories": "विशेष श्रेणियाँ",
    "information society services": "सूचना सोसाइटी सेवाएँ", "criminal convictions": "आपराधिक दोषसिद्धि",
    "region": "क्षेत्र", "quarter": "तिमाही", "due": "देय", "approved": "मंज़ूर", "without undue delay": "बिना अनुचित देरी के",
}

_TOKENS = re.compile(r"[A-Z]{2,}[\w\-]*|\d[\d,.\-:/]*\d|\d")


def _split_cites(s: str) -> tuple[str, str]:
    m = re.search(r"((?:\s*\[\d+(?:\s*,\s*\d+)*\])+)\s*[.।]?\s*$", s)
    if not m:
        return s.strip(), ""
    return (s[:m.start()].strip().rstrip(".") + "."), " " + "".join(re.findall(r"\[\d+(?:\s*,\s*\d+)*\]", m.group(1)))


def _must_keep(english: str) -> set[str]:
    """Numbers and codes that must appear, unchanged, in the translation."""
    return {t.rstrip(".,") for t in _TOKENS.findall(english) if any(ch.isdigit() for ch in t) or len(t) >= 2}


def _keeps(english: str, target: str) -> bool:
    tgt = lang.ascii_digits(target).replace(",", "")
    return all(tok.replace(",", "") in tgt for tok in _must_keep(english) if any(c.isdigit() for c in tok))


_PROTECT = re.compile(r"\b(?:[A-Z]{2,}[A-Z0-9\-]*|[A-Z][\w]*-\d[\w\-]*|\d+[A-Z]+[\w\-]*)\b"
                      r"|(?<=[a-z,] )[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*|^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+|^[A-Z][a-z]+(?='s\b)")
_LEAD = {"the", "a", "an", "under", "in", "on", "for", "if", "each", "every", "this", "that", "these", "both", "no", "all",
         "late", "per", "within", "after", "before", "only", "any"}
# Places a Hindi reader knows by their Hindi name: either form is accepted.
PLACES_HI = {"India": "भारत", "England": "इंग्लैंड", "Wales": "वेल्स", "England and Wales": "इंग्लैंड और वेल्स",
             "Germany": "जर्मनी", "France": "फ्रांस", "Singapore": "सिंगापुर", "Japan": "जापान", "China": "चीन",
             "United States": "अमेरिका", "United Kingdom": "ब्रिटेन", "Delaware": "डेलावेयर", "New York": "न्यूयॉर्क",
             "California": "कैलिफ़ोर्निया", "Maharashtra": "महाराष्ट्र", "Delhi": "दिल्ली", "Mumbai": "मुंबई",
             "Bengaluru": "बेंगलुरु", "Bangalore": "बेंगलुरु", "Europe": "यूरोप", "European Union": "यूरोपीय संघ"}


# Legal English the small model turns around ("governed by the laws of India" came back as "applies to India's laws"),
# rewritten into plain English first. Number words with the digits beside them keep only the digits.
_NUMWORD = (r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
            r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|lakh|million)")
SIMPLIFY = [
    (re.compile(r"^(.+?)\s+(?:is|are|shall be|will be)\s+governed by(?: and construed in accordance with)? the laws? of (.+?)\.?$", re.I),
     lambda m: f"The laws of {m.group(2)} apply to {m.group(1)[0].lower() + m.group(1)[1:]}."),
    (re.compile(rf"\b{_NUMWORD}(?:[- ](?:and[- ])?{_NUMWORD})*\s*\((\d[\d,.]*)\)", re.I), r"\1"),
    # "warranty is 12 months from delivery" came back as "starts 12 months after delivery"
    (re.compile(r"\b(is|are) (\d[\d,.]*) (years?|months?|days?|weeks?) from\b", re.I),
     lambda m: f"{'lasts' if m.group(1).lower() == 'is' else 'last'} {m.group(2)} {m.group(3)}, counted from"),
    # "kept for 5 years after termination" came back as "kept 5 years after termination"
    (re.compile(r"\bfor (\d[\d,.]*) (years?|months?|days?|weeks?) after\b", re.I), r"until \1 \2 after"),
]


def simplify(text: str) -> str:
    for pattern, repl in SIMPLIFY:
        text = pattern.sub(repl, text)
    return text


def question_names(question: str) -> list[str]:
    """Capitalised English words the user typed into an Indian-language question ("Acme की ...") are names."""
    words = {w for k in GLOSSARY_HI for w in k.split()}
    return sorted({w for w in re.findall(r"\b[A-Z][A-Za-z0-9\-]+\b", question or "") if w.lower() not in words})


def _wrap(text: str, known: tuple[str, ...] = ()) -> str:
    """«Acme»: a name the translator must copy as is. The name stays readable, so the sentence keeps its meaning."""
    for n in sorted(set(names(text, known)), key=len, reverse=True):
        if n not in PLACES_HI:
            text = re.sub(rf"(?<!«)\b{re.escape(n)}\b(?!»)", f"«{n}»", text)
    return text


def names(text: str, known: tuple[str, ...] = ()) -> list[str]:
    """Names, acronyms and codes in an English sentence that must reach the translation unchanged
    ("The Acme Agreement" -> "Acme": the defined term itself is translated)."""
    out: list[str] = []
    for m in _PROTECT.finditer(text):
        words = m.group(0).split()
        while words and words[0].lower() in _LEAD:
            words.pop(0)
        while words and words[-1].lower() in GLOSSARY_HI:
            words.pop()
        if not words or m.group(0).lower() in GLOSSARY_HI:
            continue
        if m.start() == 0 and len(words) == 1 and words[0] == m.group(0) and text[m.end():m.end() + 2] != "'s":
            continue  # one capitalised word at the start is just the start of a sentence
        out.append(" ".join(words))
    out += [k for k in known if re.search(rf"\b{re.escape(k)}\b", text) and not any(k in n for n in out)]
    return out


_CODE = re.compile(r"^(?:[A-Z]{2,}[A-Z0-9\-]*|[A-Z][\w]*-\d[\w\-]*|\d+[A-Z]+[\w\-]*)$")


def names_kept(english: str, target: str, known: tuple[str, ...] = (), back: str | None = None) -> bool:
    """Acronyms and codes (GDPR, QX-4471-B) must be copied exactly. Other names may be written in Hindi
    ("Article" -> "अनुच्छेद") as long as the translation back into English still has them; without that back
    translation (back=None) only the strict ones are checked."""
    for n in names(english, known):
        if n in target or (PLACES_HI.get(n) and PLACES_HI[n] in target):
            continue
        if _CODE.match(n) or (back is not None and n.lower() not in back.lower()):
            return False
    return True


# Masculine nouns ending in -ा take -े before a postposition ("समझौता पर" -> "समझौते पर"); the model often misses it.
_OBLIQUE_HI = re.compile(r"(समझौत|महीन|हिस्स|पैस|वाद|इराद|ख़र्च|दरवाज़)ा(?=\s+(?:पर|के|में|से|को|का|की|तक|द्वारा|अंतर्गत)\b)")


_LETTERS_HI = {"A": "ए", "B": "बी", "C": "सी", "D": "डी", "E": "ई", "F": "एफ", "G": "जी", "H": "एच", "I": "आई", "J": "जे",
               "K": "के", "L": "एल", "M": "एम", "N": "एन", "O": "ओ", "P": "पी", "Q": "क्यू", "R": "आर", "S": "एस", "T": "टी",
               "U": "यू", "V": "वी", "W": "डब्ल्यू", "X": "एक्स", "Y": "वाई", "Z": "ज़ेड"}


def unspell(text: str, english: str, known: tuple[str, ...] = ()) -> str:
    """IndicTrans2 spells names out letter by letter ("जी. डी. पी. आर." for GDPR): those go back to the English name."""
    for n in sorted({w for n in names(english, known) for w in n.split()}, key=len, reverse=True):
        letters = [c for c in n.upper() if c in _LETTERS_HI]
        if 2 <= len(letters) <= 8 and len(letters) == len(n.replace("-", "")):
            spelled = r"\.?\s*".join(re.escape(_LETTERS_HI[c]) for c in letters) + r"\.?"
            text = re.sub(spelled, n, text)
    return text


def polish(text: str, code: str) -> str:
    text = re.sub(r"(\d[\d,.]*)\s*\(\1\)", r"\1", text.strip())  # "45 (45)"
    if code == "hi":
        text = _OBLIQUE_HI.sub(r"\1े", text)
        text = re.sub(r"(?<![\u0900-\u097F])([\u0900-\u097F]+) \1(?![\u0900-\u097F])", r"\1", text)  # "तक तक"
    return text


def _glossary_hint(sentences: list[str], code: str) -> str:
    if code != "hi":
        return ""
    low = " ".join(sentences).lower()
    hits = {k: v for k, v in GLOSSARY_HI.items() if re.search(rf"\b{re.escape(k)}\b", low)}
    return ("\n- Use these Hindi terms: " + "; ".join(f"{k} = {v}" for k, v in sorted(hits.items(), key=lambda kv: -len(kv[0])))) if hits else ""


def _translate_batch(sentences: list[str], code: str, hint: str = "", known: tuple[str, ...] = ()) -> list[str]:
    sentences = [simplify(x) for x in sentences]
    if backend() == "indictrans2" and code in _IT2 and not hint:  # a retry goes to the LLM with the hint
        try:
            out = [unspell(o, x, known) for x, o in zip(sentences, _it2_translate(sentences, "eng_Latn", _IT2[code]))]
            rest = [i for i, (x, o) in enumerate(zip(sentences, out))
                    if not names_kept(x, o, known) or any(re.search(rf"\b{re.escape(k)}\b", x) and k not in o for k in known)]
            if rest:  # a code it transliterated (GDPR -> जीडीपीआर): those sentences go to the LLM
                llm_out = _llm_translate([sentences[i] for i in rest], code, "", known)
                for i, o in zip(rest, llm_out or [""] * len(rest)):
                    out[i] = o
            return out
        except Exception:
            pass
    return _llm_translate(sentences, code, hint, known)


def _llm_translate(sentences: list[str], code: str, hint: str = "", known: tuple[str, ...] = ()) -> list[str]:
    hint = _glossary_hint(sentences, code) + hint
    if any("«" in _wrap(x, known) for x in sentences):
        hint += "\n- Text inside « » is a name or code: copy it exactly, in English letters, with the « » marks."
    rules = FROM_ENGLISH.format(name=lang.NAMES.get(code, code), native=lang.NATIVE.get(code, ""), hint=hint)
    data = llm.chat_json([{"role": "system", "content": rules},
                          {"role": "user", "content": json.dumps({"sentences": [_wrap(x, known) for x in sentences]}, ensure_ascii=False)}],
                         max_tokens=1800)
    out = [str(x) for x in data.get("translations") or []]
    if len(out) != len(sentences):
        return []
    out = [o.replace("«", "").replace("»", "") for o in out]
    return [o if names_kept(x, o, known) else "" for x, o in zip(sentences, out)]  # a changed name fails the sentence


_N = r"(\d{1,3}(?:[ ,]\d{3})+|\d[\d,.]*)"  # 250, 12,400, 20 000 000
_EN_LIMITS = [("lt", rf"\b(?:fewer|less) than {_N}"), ("ge", rf"\b(?:at least|no fewer than|not less than|minimum of) {_N}"),
              ("gt", rf"\b(?:more|greater) than {_N}"), ("le", rf"\b(?:at most|up to|no more than|not more than|maximum of) {_N}"),
              ("ge", rf"\bnot below {_N}")]
_GAP = r"(?:\s*\S+){{0,3}}?\s*"  # up to three words between the number and its postposition ("250 कर्मचारियों से कम")
_HI_LIMITS = {"lt": "{n}" + _GAP + "से कम",
              "ge": "(?:कम से कम {n}|न्यूनतम {n}|{n}" + _GAP + "या (?:उससे |इससे )?(?:अधिक|ज़्यादा|ज्यादा)|{n}" + _GAP + "से कम न)",
              "gt": "{n}" + _GAP + "से (?:अधिक|ज़्यादा|ज्यादा)",
              "le": "(?:अधिकतम {n}|{n}" + _GAP + "तक|{n}" + _GAP + "या (?:उससे |इससे )?कम)"}
_HI_WRONG = {"lt": "(?:कम से कम {n}|{n}" + _GAP + "या (?:उससे |इससे )?कम)", "gt": "{n}" + _GAP + "या (?:उससे |इससे )?(?:अधिक|ज़्यादा)"}


# Small words that change what a sentence means; the meaning check sometimes lets them slip ("paid leave" -> "leave").
QUALIFIERS_HI = [
    (r"\bunpaid\b", ("अवैतनिक", "बिना वेतन", "बिना भुगतान")),
    (r"\bpaid (?:annual |parental |sick |maternity |paternity )?(?:leave|holiday|vacation|time off)", ("सवेतन", "वेतन सहित", "भुगतान वाली", "भुगतान सहित", "पेड")),
    (r"\blate\b", ("देर", "विलंब", "लेट")),
    (r"\bwritten\b", ("लिखित",)),
    (r"\bnet\b", ("शुद्ध", "नेट")), (r"\bgross\b", ("सकल", "ग्रॉस")),
    (r"\bexclusive\b", ("अनन्य", "एक्सक्लूसिव", "विशेष")), (r"\bnon-exclusive\b", ("गैर-अनन्य", "नॉन-एक्सक्लूसिव", "गैर-विशेष")),
    (r"\b(?:not|never|no|cannot)\b|n't\b", ("नहीं", "^न$", "^ना$", "बिना", "^मत$", "गैर")),
    (r"\bunless\b", ("जब तक", "सिवाय", "बशर्ते", "अगर ... नहीं")),
    (r"\bcarr(?:y|ies) interest|\binterest (?:is )?charged", ("ब्याज लग", "ब्याज देना", "ब्याज लिया")),
]


def qualifiers_kept(english: str, target: str, code: str) -> bool:
    if code != "hi":
        return True
    words = set(re.findall(r"[\u0900-\u097F]+", target))

    def has(h):  # "^न$" is the whole word न, never the न inside उल्लंघन
        return h[1:-1] in words if h.startswith("^") else h in target
    return all(any(has(h) for h in hindi) for pat, hindi in QUALIFIERS_HI if re.search(pat, english, re.I))


def limits_kept(english: str, target: str, code: str) -> bool:
    """Every "fewer than 250" in the English must come out as "250 से कम" (and not "250 या उससे कम")."""
    if code != "hi":
        return True
    tgt = lang.ascii_digits(target)
    for kind, pat in _EN_LIMITS:
        for m in re.finditer(pat, english, re.I):
            n = r"[ ,]?".join(re.escape(g) for g in re.split(r"[ ,]", m.group(1).rstrip(".,")))  # 20 000 000 = 20,000,000
            if not re.search(_HI_LIMITS[kind].format(n=n), tgt):
                return False
            if kind in _HI_WRONG and re.search(_HI_WRONG[kind].format(n=n), tgt):
                return False
    return True


def _back(sentences: list[str], code: str) -> list[str]:
    if backend("indic-en") == "indictrans2" and code in _IT2:
        try:
            return _it2_translate(sentences, _IT2[code], "eng_Latn")
        except Exception:
            pass
    data = llm.chat_json([{"role": "system", "content": BACK.format(name=lang.NAMES.get(code, code))},
                          {"role": "user", "content": json.dumps({"sentences": sentences}, ensure_ascii=False)}], max_tokens=1500)
    out = [str(x) for x in data.get("english") or []]
    return out if len(out) == len(sentences) else []


def _same(pairs: list[tuple[str, str]]) -> list[bool]:
    data = llm.chat_json([{"role": "system", "content": SAME},
                          {"role": "user", "content": json.dumps([{"A": a, "B": b} for a, b in pairs], ensure_ascii=False)}],
                         max_tokens=200)
    out = data.get("same") or []
    return [x in (True, "true", "True", "yes") for x in out] if len(out) == len(pairs) else [False] * len(pairs)


def answer_sentences(sentences: list[str], code: str, question: str = "") -> tuple[list[str], dict]:
    """Translates verified English answer sentences (with their [n] citations) and checks every one.
    Returns (sentences in the target language, report)."""
    if not sentences:
        return [], {}
    bodies, cites = zip(*[_split_cites(s) for s in sentences])
    bodies, cites = list(bodies), list(cites)
    out = [""] * len(bodies)
    report = {"backend": backend(), "retranslated": 0, "kept_english": 0}
    todo = list(range(len(bodies)))
    known = tuple(question_names(question))
    for attempt in range(2):
        if not todo:
            break
        hint = "" if attempt == 0 else ("\n- A previous translation changed a number, a limit or the meaning. Translate even more literally.")
        got = _translate_batch([bodies[i] for i in todo], code, hint, known)
        if not got:
            continue
        cand = {i: polish(g, code) for i, g in zip(todo, got) if g.strip()}
        # deterministic checks first: numbers and codes kept, limits not reversed
        for i in list(cand):
            fixed, _ = lang.fix_limits(cand[i], bodies[i], code)
            cand[i] = fixed
            if not (_keeps(bodies[i], cand[i]) and limits_kept(bodies[i], cand[i], code)
                    and qualifiers_kept(bodies[i], cand[i], code)):
                del cand[i]
        # meaning check: back into English, compared with the verified sentence
        ids = list(cand)
        if ids:
            back = _back([cand[i] for i in ids], code)
            same = _same([(bodies[i], b) for i, b in zip(ids, back)]) if back else [False] * len(ids)
            for i, ok, b in zip(ids, same, back or [None] * len(ids)):
                if ok and b is not None and names_kept(bodies[i], cand[i], known, b):
                    out[i] = cand[i]
        if attempt:
            report["retranslated"] += sum(1 for i in todo if out[i])
        todo = [i for i in todo if not out[i]]
    for i in todo:  # never a doubtful translation: the verified English sentence instead
        out[i] = bodies[i]
        report["kept_english"] += 1
    danda = "।" if code in ("hi", "mr") else ""
    final = []
    for body, cite in zip(out, cites):
        body = body.rstrip(" .।")
        end = danda if (danda and lang.devanagari_share(body) > 0.3) else "."
        final.append(f"{body}{cite}{end}".replace(" .", "."))
    return final, report
