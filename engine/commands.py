"""Slash commands typed into the composer: /compare, /summarize, /find, /files, /rescan, /help."""
import re
import shlex

from . import harness

COMMANDS = [
    {"name": "compare", "usage": "/compare <file> <file> [what to compare]",
     "description": "Side-by-side table of 2 to 5 files, every cell cited",
     "examples": ["/compare acme globex payment terms and warranty", "/compare acme globex"]},
    {"name": "summarize", "usage": "/summarize <file>", "description": "Key points of one file, each one cited",
     "examples": ["/summarize gdpr"]},
    {"name": "outline", "usage": "/outline <file>", "description": "The structure of a file: its parts and repeating sections",
     "examples": ["/outline northwind"]},
    {"name": "find", "usage": "/find <words>", "description": "Exact keyword search across files, no AI involved",
     "examples": ["/find liability cap", "/find 72 hours"]},
    {"name": "files", "usage": "/files", "description": "List the files this chat or project can see",
     "examples": ["/files"]},
    {"name": "rescan", "usage": "/rescan", "description": "Re-read the project folder for new, changed or deleted files",
     "examples": ["/rescan"]},
    {"name": "help", "usage": "/help", "description": "Show every command", "examples": ["/help"]},
]
DEFAULT_COMPARE = "key terms, obligations, amounts, dates and deadlines"


def is_command(text: str) -> bool:
    return bool(re.match(r"^/[a-z]+\b", text.strip()))


_GREETING = re.compile(r"^(hi|hii+|hello|hey|heya|hiya|yo|namaste|namaskar|नमस्ते|नमस्कार|राम राम|good (morning|afternoon|evening))"
                       r"( there| vyum| ji| जी)?[\s!.,।]*$", re.I)
_THANKS = re.compile(r"^(thanks|thank you|thank you so much|thanks a lot|thx|ty|dhanyavad|dhanyavaad|shukriya|धन्यवाद|शुक्रिया)"
                     r"( vyum| ji| जी)?[\s!.,।]*$", re.I)
ADD_FILES = ("Click + to attach files (or drag them in), or open a folder as a project. "
             "Vyum answers only from your documents, and they never leave this Mac.")


_HI = {"Hi, I'm Vyum": "नमस्ते, मैं Vyum हूँ", "You're welcome": "आपका स्वागत है", "Add a document first": "पहले कोई दस्तावेज़ जोड़ें",
       "Nothing to search yet": "अभी खोजने के लिए कुछ नहीं है",
       "Ask another question whenever you like.": "जब चाहें, अगला सवाल पूछिए।",
       ADD_FILES: "+ दबाकर फ़ाइलें जोड़ें (या खींचकर छोड़ें), या किसी फ़ोल्डर को प्रोजेक्ट बनाइए। Vyum सिर्फ़ आपके दस्तावेज़ों से जवाब देता है, और वे इसी Mac पर रहते हैं।",
       "This project has no readable files yet. Add files to its folder, then type /rescan.":
           "इस प्रोजेक्ट में अभी पढ़ने लायक कोई फ़ाइल नहीं है। फ़ोल्डर में फ़ाइलें डालें, फिर /rescan लिखें।"}


def quick_reply(text: str, docs: list[dict], in_project: bool = False) -> dict | None:
    """Replies that need no search: small talk, and questions asked before any document is added."""
    from . import lang
    text = text.strip()
    hindi = lang.detect(text) == "hi"

    def reply(title, note):
        if hindi:
            title, note = _HI.get(title, title), _HI.get(note, note)
        return {"kind": "instructions", "command": None, "title": title, "note": note,
                "commands": COMMANDS, "files": [], "examples": []}
    if _THANKS.match(text):
        return reply("You're welcome", "Ask another question whenever you like.")
    if not docs:
        if in_project:
            return reply("Nothing to search yet", "This project has no readable files yet. Add files to its folder, then type /rescan.")
        return reply("Hi, I'm Vyum" if _GREETING.match(text) else "Add a document first", ADD_FILES)
    if _GREETING.match(text):
        n = len(docs)
        if hindi:
            return reply("Hi, I'm Vyum", f"अपने {n} दस्तावेज़ों के बारे में कुछ भी पूछिए। हर जवाब दस्तावेज़ से जाँचा जाता है और स्रोत के साथ आता है।")
        return reply("Hi, I'm Vyum", f"Ask me anything about your {n} document{'s' if n != 1 else ''}. "
                                     "Every answer is checked against the text and cited.")
    return None


def _words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", name.lower().rsplit(".", 1)[0])


def _names(name: str) -> set[str]:
    """Every way a user may type a file: 'contracts/acme.pdf', 'contracts/acme', 'acme.pdf', 'acme'."""
    low = name.lower()
    base = low.rsplit("/", 1)[-1]
    return {low, low.rsplit(".", 1)[0], base, base.rsplit(".", 1)[0]}


def resolve_files(args: str, docs: list[dict]) -> tuple[list[dict], str]:
    """Pick the files named in `args` (full names, quoted names or a distinctive word) and return the rest."""
    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()
    picked, rest = [], []
    for tok in tokens:
        low = tok.lower()
        exact = [d for d in docs if low in _names(d["name"])]
        if not exact and len(low) >= 3:
            # a word that appears in exactly one file name ("acme", "gdpr"); shared words like "supply" stay text
            exact = [d for d in docs if any(w == low or (len(low) >= 4 and w.startswith(low)) for w in _words(d["name"]))]
        if len(exact) == 1:
            if exact[0] not in picked:
                picked.append(exact[0])
        else:
            rest.append(tok)
    return picked, " ".join(rest).strip()


def _instructions(name, note=None, docs=()):
    cmd = next((c for c in COMMANDS if c["name"] == name), None)
    return {"type": "result", "result": {"kind": "instructions", "command": name, "note": note,
                                         "usage": cmd["usage"] if cmd else None,
                                         "description": cmd["description"] if cmd else None,
                                         "examples": cmd["examples"] if cmd else [],
                                         "files": [d["name"] for d in docs], "commands": COMMANDS}}


def run(text: str, *, store, docs: list[dict], doc_ids, mode: str, think: bool, project=None, start_scan=None,
        compare_all=False):
    """Yields the same events as an answer: status lines, then one result.
    compare_all: Compare mode, where naming no files means every file in this chat or project."""
    m = re.match(r"^/([a-z]+)\s*(.*)$", text.strip(), re.DOTALL)
    name, args = m.group(1), m.group(2).strip()

    if name == "help":
        yield {"type": "result", "result": {"kind": "help", "commands": COMMANDS}}

    elif name == "files":
        yield {"type": "result", "result": {"kind": "files", "files": [
            {k: d.get(k) for k in ("id", "name", "ext", "pages", "chunks", "figures", "quarantined")} for d in docs]}}

    elif name == "find":
        if not args:
            yield _instructions("find", "Add the words to look for.", docs)
            return
        yield {"type": "result", "result": {"kind": "find", **harness.find(store, args, doc_ids)}}

    elif name == "summarize":
        picked, _ = resolve_files(args, docs)
        if not picked and len(docs) == 1:
            picked = docs
        if len(picked) != 1:
            yield _instructions("summarize", "Name one file to summarize." if not picked else
                                f"That matches {len(picked)} files; name just one.", docs)
            return
        for ev in harness.summarize_events(store, picked[0]["id"], mode, think):
            if ev["type"] == "result":
                ev = {"type": "result", "result": {"kind": "answer", **ev["result"]}}
            yield ev

    elif name == "outline":
        picked, _ = resolve_files(args, docs)
        if not picked and len(docs) == 1:
            picked = docs
        if len(picked) != 1:
            yield _instructions("outline", "Name one file." if not picked else f"That matches {len(picked)} files; name just one.", docs)
            return
        yield {"type": "status", "text": f"Reading the structure of {picked[0]['name']}"}
        yield {"type": "result", "result": {"kind": "outline", **harness.document_outline(store, picked[0]["id"])}}

    elif name == "compare":
        picked, aspects = resolve_files(args, docs)
        if compare_all and not picked and 2 <= len(docs) <= 5:
            picked = list(docs)
        if len(picked) < 2 or len(picked) > 5:
            note = ("Name at least two files." if len(picked) < 2 else "Compare at most five files at a time.")
            if len(picked) == 1:
                note = f"Found {picked[0]['name']}; name at least one more file."
            if compare_all and len(docs) < 2:
                note = "Compare needs 2 to 5 files. Attach them with +, then say what to compare."
            elif compare_all and len(docs) > 5:
                note = f"There are {len(docs)} files here. Name 2 to 5 of them, then say what to compare."
            yield _instructions("compare", note, docs)
            return
        aspects = aspects or DEFAULT_COMPARE
        yield {"type": "status", "text": f"Searching {len(picked)} files separately for: {aspects}"}
        if think:
            yield {"type": "status", "text": "Thinking through the comparison"}
        result = harness.compare(store, aspects, [d["id"] for d in picked], mode, think)
        yield {"type": "result", "result": {"kind": "compare", "aspects": aspects, **result}}

    elif name == "rescan":
        if not project or not start_scan:
            yield _instructions("rescan", "/rescan works inside a project, where a folder is attached.", docs)
            return
        yield {"type": "result", "result": {"kind": "job", "job": start_scan(),
                                            "text": f"Rescanning {project['folder']}"}}

    else:
        yield _instructions(name, f"There is no /{name} command.", docs)
