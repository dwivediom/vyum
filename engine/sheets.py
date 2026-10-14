"""Formula-first spreadsheet edits.

What spreadsheet benchmarks (SpreadsheetBench, SheetCopilot, SpreadsheetLLM) found, and what this module does about it:
- Models fail when they do not look first (wrong header row, a totals row counted as data, several tables): Vyum
  reads the sheet and gives the model a compact map: header row, data rows, totals row, each column's type and range,
  and any cells already showing errors.
- Values typed in by the model are wrong or go stale: every computed cell is a real Excel formula. The model writes
  ONE formula for the first data row; Vyum fills it down, shifting relative references the way Excel does.
- Off-by-one and wrong-target errors: rows and columns are named by Vyum (the next free column, the data rows, the
  totals row), never counted by the model.
- Unsupported functions and broken formulas: every formula is checked against the functions IronCalc (GenOffice's
  MIT/Apache formula engine) can compute; after writing, IronCalc computes the sheet and Vyum reads the results back.
  Any #DIV/0!, #NAME?, #REF!, #VALUE! goes back to the model with the formula that caused it, up to twice; if it
  still fails, the sheet is restored.
"""
import datetime
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

FUNCTIONS = {ln.strip() for ln in (Path(__file__).with_name("sheet_functions.txt")).read_text().splitlines()
             if ln.strip() and not ln.startswith("#")}
MISSING_HELP = {
    "FILTER": "use the \"summary\" step, SUMIFS/COUNTIFS, or a helper column with IF",
    "UNIQUE": "use the \"summary\" step (Vyum lists the distinct values)",
    "SORT": "use the \"sort\" step", "SORTBY": "use the \"sort\" step",
    "LET": "write the expression out in full", "LAMBDA": "write the expression out in full",
    "SUMPRODUCT": "use SUMIFS, or a helper column with the product and SUM over it",
    "TEXTSPLIT": "use TEXTBEFORE / TEXTAFTER", "PROPER": "use UPPER(LEFT(x,1))&LOWER(MID(x,2,100))",
    "XMATCH": "use MATCH", "AVG": "use AVERAGE, or AVERAGEIFS(values, criteria_range, criteria) for a conditional average",
    "AVERAGEA": "use AVERAGE", "SEQUENCE": "use ROW()-ROW($A$1)", "PERCENTILE": "use PERCENTILE.INC",
}
ERROR_RE = re.compile(r"^#(DIV/0!|NAME\?|REF!|VALUE!|N/A|NUM!|NULL!|ERROR!|SPILL!|CALC!)")
MAX_FILL = 200_000


class StepError(ValueError):
    """A problem with the model's plan that it can fix when told about it."""


# ----------------------------------------------------------------------------------------------- addresses
def col_name(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def col_num(s: str) -> int:
    n = 0
    for ch in s.upper().replace("$", ""):
        n = n * 26 + ord(ch) - 64
    return n


_REF = re.compile(r"(?<![A-Za-z0-9_.$])((?:'[^']+'|[A-Za-z_][\w.]*)!)?(\$?)([A-Z]{1,3})(\$?)(\d{1,7})(?![\w(])")


def shift_rows(formula: str, delta: int) -> str:
    """=B2/SUM($B$2:$B$13) shifted by 3 -> =B5/SUM($B$2:$B$13): relative rows move, $-anchored ones stay; text in
    quotes is left alone."""
    if not delta:
        return formula
    parts = re.split(r'("(?:[^"]|"")*")', formula)
    for i in range(0, len(parts), 2):
        parts[i] = _REF.sub(lambda m: f"{m.group(1) or ''}{m.group(2)}{m.group(3)}{m.group(4)}"
                            f"{m.group(5) if m.group(4) else max(1, int(m.group(5)) + delta)}", parts[i])
    return "".join(parts)


def clean_formula(f):
    """Small model slips: ="="&RANK(...) or ==SUM(...) for =RANK(...) / =SUM(...)."""
    if not isinstance(f, str):
        return f
    f = f.strip()
    f = re.sub(r'^=\s*"="\s*&\s*', "=", f)
    f = re.sub(r'^=\s*""\s*&\s*', "=", f)
    f = re.sub(r"^=+", "=", f)
    # ">=2025-02-01" as a criterion: Excel reads it, IronCalc does not; a DATE() works in both
    f = re.sub(r'"(<=|>=|<>|<|>|=)?(\d{4})-(\d{1,2})-(\d{1,2})"',
               lambda m: (f'"{m.group(1)}"&' if m.group(1) else "") + f"DATE({int(m.group(2))},{int(m.group(3))},{int(m.group(4))})", f)
    return f if f.startswith("=") else ("=" + f if re.match(r"[A-Z][A-Z0-9.]*\(", f) else f)


_DATE_FN = re.compile(r"\b(EOMONTH|EDATE|WORKDAY|WORKDAY\.INTL|DATE|TODAY|DATEVALUE)\s*\(", re.I)


def check_formula(formula: str):
    if not isinstance(formula, str) or not formula.startswith("="):
        raise StepError(f"{formula!r} is not a formula (formulas start with =)")
    if re.fullmatch(r'=\s*"?#[A-Z/0!?]+[!?]?"?\s*', formula):
        raise StepError(f"{formula} only writes an error code; write the formula that computes the value instead.")
    if formula.count("(") != formula.count(")"):
        raise StepError(f"{formula}: brackets do not match. Split the logic into helper columns with one simple formula each.")
    outside = "".join(re.split(r'"(?:[^"]|"")*"', formula)[::1])
    for name in re.findall(r"([A-Za-z][A-Za-z0-9._]*)\s*\(", outside):
        up = name.upper().removeprefix("_XLFN.")
        if up not in FUNCTIONS:
            hint = MISSING_HELP.get(up, "use a function from the usual Excel set")
            raise StepError(f"{formula}: {up} is not available in this spreadsheet engine; {hint}")


# ----------------------------------------------------------------------------------------------- reading
@dataclass
class Table:
    sheet: str
    rows: list                      # values, rows[0] is row 1 (A1-aligned)
    formulas: dict                  # "B5" -> "=..."
    header: int = 1                 # 1-based row numbers
    first: int = 2
    last: int = 1                   # last data row (totals excluded)
    totals: int | None = None
    width: int = 0
    headers: dict = field(default_factory=dict)   # "B" -> "Units"

    def value(self, addr: str):
        m = re.fullmatch(r"([A-Z]{1,3})(\d+)", addr)
        r, c = int(m.group(2)) - 1, col_num(m.group(1)) - 1
        return self.rows[r][c] if r < len(self.rows) and c < len(self.rows[r]) else None

    def column_values(self, letter: str) -> list:
        c = col_num(letter) - 1
        return [(self.rows[r - 1][c] if c < len(self.rows[r - 1]) else None) for r in range(self.first, self.last + 1)]


def _plain(v):
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return int(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()[:10] if not isinstance(v, datetime.datetime) or not (v.hour or v.minute) else v.isoformat()
    return None if v == "" else v


def sheet_names(path: Path) -> list[str]:
    from python_calamine import CalamineWorkbook
    return CalamineWorkbook.from_path(str(path)).sheet_names


_TOTAL_LABEL = re.compile(r"^\s*(grand\s+)?(total|totals|sum|कुल|योग)\b", re.I)


def read_values(path: Path, sheet: str) -> list[list]:
    """A1-aligned cell values. calamine is fast; it refuses some error values (#CIRC!), then openpyxl reads the file."""
    from python_calamine import CalamineWorkbook
    try:
        return CalamineWorkbook.from_path(str(path)).get_sheet_by_name(sheet).to_python(skip_empty_area=False)
    except Exception:
        import openpyxl
        ws = openpyxl.load_workbook(path, data_only=True, read_only=True)[sheet]
        return [list(r) for r in ws.iter_rows(values_only=True)]


def read_table(path: Path, sheet: str | None = None) -> Table:
    from .office import _xlsx_formulas
    names = sheet_names(path)
    sheet = sheet if sheet in names else names[0]
    rows = [[_plain(v) for v in r] for r in read_values(path, sheet)]
    while rows and all(v is None for v in rows[-1]):
        rows.pop()
    formulas = _xlsx_formulas(path).get(sheet, {})
    t = Table(sheet, rows, formulas)
    t.width = max((max((j + 1 for j, v in enumerate(r) if v is not None), default=0) for r in rows), default=0)
    t.width = max([t.width] + [col_num(re.match(r"[A-Z]+", a).group(0)) for a in formulas])
    for i, r in enumerate(rows, 1):  # header: the first row with two or more text cells
        if sum(isinstance(v, str) and v.strip() != "" for v in r) >= 2 or (i == len(rows)):
            t.header = i
            break
    t.first = t.header + 1
    t.last = len(rows)
    first_text = next((v for v in (rows[-1] if rows else []) if isinstance(v, str) and v.strip()), "")
    total_formula = any(re.match(r"=\s*(SUM|SUBTOTAL)\(", f, re.I) and int(re.sub(r"[A-Z]+", "", a)) == len(rows)
                        for a, f in formulas.items())
    if len(rows) > t.first and (_TOTAL_LABEL.match(str(first_text)) or total_formula):
        t.totals = len(rows)
        t.last = len(rows) - 1
        while t.last > t.header and all(v is None for v in rows[t.last - 1]):
            t.last -= 1
    head = rows[t.header - 1] if rows else []
    t.headers = {col_name(j + 1): str(v).strip() for j, v in enumerate(head) if v not in (None, "")}
    return t


def cell_errors(path: Path, sheet: str) -> dict[str, str]:
    """Cells whose computed value is an Excel error (#REF!, #DIV/0!, ...), read from the sheet XML (t="e")."""
    import zipfile
    from lxml import etree
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    out: dict[str, str] = {}
    with zipfile.ZipFile(path) as z:
        wb = etree.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in etree.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        for sh in wb.findall("m:sheets/m:sheet", ns):
            if sh.get("name") != sheet:
                continue
            name = "xl/" + rels.get(sh.get(f"{{{ns['r']}}}id"), "").lstrip("/").removeprefix("xl/")
            with z.open(name) as f:
                for _, c in etree.iterparse(f, tag=f"{{{ns['m']}}}c"):
                    if c.get("t") == "e":
                        v = c.find("m:v", ns)
                        out[c.get("r")] = v.text if v is not None and v.text else "#ERROR!"
                    c.clear()
    return out


def _kind(vals: list) -> str:
    vals = [v for v in vals if v is not None]
    if not vals:
        return "empty"
    nums = sum(isinstance(v, (int, float)) for v in vals)
    dates = sum(isinstance(v, str) and bool(re.match(r"\d{4}-\d{2}-\d{2}", v)) for v in vals)
    if nums == len(vals):
        return "number"
    if dates == len(vals):
        return "date"
    if nums + dates == 0:
        return "text"
    return "mixed"


def describe(path: Path, focus: str | None = None, show_errors: bool = True) -> str:
    """The map of the workbook the model plans from."""
    names = sheet_names(path)
    t = read_table(path, focus)
    out = [f'Sheet "{t.sheet}". Header row {t.header}. Data rows {t.first} to {t.last} ({max(0, t.last - t.first + 1)} rows).'
           + (f" Totals row {t.totals}." if t.totals else " No totals row.")
           + f" Next empty column: {col_name(t.width + 1)}.", "Columns:"]
    for j in range(1, t.width + 1):
        L = col_name(j)
        vals = t.column_values(L)
        kind = _kind(vals)
        f = t.formulas.get(f"{L}{t.first}")
        desc = f'  {L} "{t.headers.get(L, "")}" {kind}'
        present = [v for v in vals if v is not None]
        if kind == "number" and present:
            desc += f", {min(present):,} to {max(present):,}"
        elif present:
            distinct = list(dict.fromkeys(str(v) for v in present))
            desc += f", {len(distinct)} different, e.g. " + ", ".join(distinct[:4])
        blanks = len(vals) - len(present)
        if blanks:
            desc += f", {blanks} blank"
        if f:
            desc += f", formula {f} in row {t.first}"
        out.append(desc)
    sample = [t.first, t.first + 1, t.first + 2, t.last] + ([t.totals] if t.totals else [])
    out.append("Rows (cell=value, formulas shown):")
    for r in sorted({x for x in sample if t.first <= x <= (t.totals or t.last)}):
        cells = [f"{col_name(j + 1)}{r}={t.formulas.get(f'{col_name(j + 1)}{r}', v)}"
                 for j, v in enumerate(t.rows[r - 1]) if v is not None or f"{col_name(j + 1)}{r}" in t.formulas]
        out.append(f"  {r}: " + " | ".join(cells))
    errors = sorted(cell_errors(path, t.sheet).items(), key=lambda kv: (int(re.sub(r"[A-Z]+", "", kv[0])), kv[0]))
    if errors and show_errors:
        out.append("Cells showing errors: " + "; ".join(f"{a} {v} ({t.formulas.get(a, '')})" for a, v in errors[:12]))
    others = [n for n in names if n != t.sheet]
    for n in others[:4]:
        o = read_table(path, n)
        out.append(f'Other sheet "{n}": header row {o.header}, data rows {o.first} to {o.last}, columns '
                   + ", ".join(f'{k} "{v}"' for k, v in list(o.headers.items())[:12]))
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------- workbook structure
_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def _rewrite_zip(path: Path, changes: dict[str, bytes]):
    import zipfile
    tmp = path.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            if item.filename not in changes:
                dst.writestr(item, src.read(item.filename))
        for name, data in changes.items():
            dst.writestr(name, data)
    tmp.replace(path)


def add_sheet(path: Path, name: str):
    """A new, empty sheet tab at the end. GenOffice has no such operation, so the workbook parts are edited directly
    (workbook, its relationships and content types); every other part of the file is copied unchanged."""
    import zipfile
    from lxml import etree
    name = re.sub(r"[\[\]:*?/\\]", "", str(name)).strip()[:31] or "Sheet"
    if name.lower() in (n.lower() for n in sheet_names(path)):
        return name
    with zipfile.ZipFile(path) as z:
        wb = etree.fromstring(z.read("xl/workbook.xml"))
        rels = etree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        ct = etree.fromstring(z.read("[Content_Types].xml"))
        files = set(z.namelist())
    sheets = wb.find(f"{{{_NS}}}sheets")
    sid = max([int(x.get("sheetId", "0")) for x in sheets] + [0]) + 1
    k = 1
    while f"xl/worksheets/sheet{k}.xml" in files:
        k += 1
    rid = "rIdVy" + str(sid)
    el = etree.SubElement(sheets, f"{{{_NS}}}sheet")
    el.set("name", name)
    el.set("sheetId", str(sid))
    el.set(f"{{{_RNS}}}id", rid)
    r = etree.SubElement(rels, f"{{{_PKG}}}Relationship")
    r.set("Id", rid)
    r.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet")
    r.set("Target", f"worksheets/sheet{k}.xml")
    o = etree.SubElement(ct, "{http://schemas.openxmlformats.org/package/2006/content-types}Override")
    o.set("PartName", f"/xl/worksheets/sheet{k}.xml")
    o.set("ContentType", "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")
    blank = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{_NS}"><sheetData/></worksheet>'
    ser = lambda x: etree.tostring(x, xml_declaration=True, encoding="UTF-8", standalone=True)
    _rewrite_zip(path, {"xl/workbook.xml": ser(wb), "xl/_rels/workbook.xml.rels": ser(rels), "[Content_Types].xml": ser(ct),
                        f"xl/worksheets/sheet{k}.xml": blank.encode()})
    return name


def rename_sheet(path: Path, old: str, new: str) -> str:
    import zipfile
    from lxml import etree
    new = re.sub(r"[\[\]:*?/\\]", "", str(new)).strip()[:31] or old
    with zipfile.ZipFile(path) as z:
        wb = etree.fromstring(z.read("xl/workbook.xml"))
    for x in wb.find(f"{{{_NS}}}sheets"):
        if x.get("name") == old:
            x.set("name", new)
    _rewrite_zip(path, {"xl/workbook.xml": etree.tostring(wb, xml_declaration=True, encoding="UTF-8", standalone=True)})
    return new


# ----------------------------------------------------------------------------------------------- writing
def _write_cells(path: Path, cells: list[dict]) -> dict:
    from .office import cli
    if not cells:
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "cells.json"
        f.write_text(json.dumps(cells, ensure_ascii=False))
        return cli("sheet", "apply", path, "--cells", f).get("detail", {})


def _structure(path: Path, ops: list[dict]):
    from .office import _apply
    if ops:
        _apply(["sheet", "apply", path], ops)


def _cell(sheet, addr, *, value=None, formula=None, fmt=None) -> dict:
    c = {"cell": addr, "sheet": sheet}
    if isinstance(value, str) and ERROR_RE.match(value.strip()):
        raise StepError(f"{addr}: {value} is an error code, not a value; write the formula that computes it.")
    if isinstance(value, str) and value.strip().startswith("=") and formula is None:
        formula, value = value, None
    if formula is not None:
        formula = clean_formula(formula)
        check_formula(formula)
        if not fmt and re.match(r"=\s*(EOMONTH|EDATE|WORKDAY(\.INTL)?|DATE|TODAY|DATEVALUE)\s*\(", formula, re.I):
            fmt = "yyyy-mm-dd"  # a date result shown as a date, not as 45747
        c["formula"] = formula
    else:
        c["value"] = value
    if fmt:
        c["style"] = {"numberFormat": fmt}
    return c


def _target_column(t: Table, step: dict) -> str:
    want = str(step.get("column") or "").strip().upper()
    if re.fullmatch(r"[A-Z]{1,3}", want):
        return want
    head = str(step.get("header") or "").strip().lower()
    for L, h in t.headers.items():
        if head and h.lower() == head:
            return L  # a column with that header already exists: fill it instead of adding another
    return col_name(t.width + 1)


def _fill(t: Table, L: str, formula: str, fmt=None) -> list[dict]:
    n = t.last - t.first + 1
    if n > MAX_FILL:
        raise StepError(f"the sheet has {n:,} data rows; filling more than {MAX_FILL:,} is not supported")
    formula = clean_formula(formula)
    check_formula(formula)
    if re.fullmatch(r"=\s*(?:'[^']+'|[A-Za-z_][\w.]*)?!?\$?[A-Z]{1,3}\$?\d+:\$?[A-Z]{1,3}\$?\d+\s*", formula):
        raise StepError(f"{formula} is a whole range, not one value per row. For a table per product, region or rep, "
                        'use one summary step with "at": "<Sheet>!A1".')
    return [_cell(t.sheet, f"{L}{r}", formula=shift_rows(formula, r - t.first), fmt=fmt) for r in range(t.first, t.last + 1)]


AGGS = {"SUM", "AVERAGE", "COUNT", "COUNTA", "MAX", "MIN", "MEDIAN"}
_RATIO = re.compile(r"margin|share|percent|%|growth|प्रतिशत|मार्जिन", re.I)


def _extend_ranges(formula: str, old_last: int, new_last: int, first: int) -> str:
    """SUM(B2:B13) -> SUM(B2:B15) when rows 14-15 were added below the data."""
    return re.sub(r"(\$?[A-Z]{1,3}\$?)(\d+):(\$?[A-Z]{1,3}\$?)(\d+)",
                  lambda m: f"{m.group(1)}{m.group(2)}:{m.group(3)}{new_last}"
                  if int(m.group(4)) == old_last and int(m.group(2)) <= first else m.group(0), formula)


def _letter(t: Table, x) -> str:
    """"F", "f" or the header "Revenue" -> "F" (models often name the column instead of giving its letter)."""
    x = str(x or "").strip()
    if re.fullmatch(r"[A-Za-z]{1,3}", x) and (x.upper() in t.headers or len(x) <= 2):
        return x.upper()
    for L, h in t.headers.items():
        if h.strip().lower() == x.lower():
            return L
    for L, h in t.headers.items():
        if x and x.lower() in h.lower():
            return L
    return x.upper()


def _normalize_step(t: Table, step: dict) -> dict:
    s = dict(step)
    for k in ("column", "by", "group_by"):
        if s.get(k) and not (k == "column" and s.get("do") == "add_column" and str(s[k]).strip().lower() not in
                                 {h.lower() for h in t.headers.values()} and not re.fullmatch(r"[A-Za-z]{1,3}", str(s[k]))):
            s[k] = _letter(t, s[k])
    if isinstance(s.get("columns"), dict):
        s["columns"] = {_letter(t, k): v for k, v in s["columns"].items()}
    if isinstance(s.get("values"), list):
        s["values"] = [dict(v, column=_letter(t, v.get("column"))) if isinstance(v, dict) else v for v in s["values"]]
    return s


def _split_sheet(step: dict) -> dict:
    """'Product Report'!D1 or Product Report!A1:D9 in cell/range: the sheet part moves to "sheet"."""
    s = dict(step)
    for k in ("cell", "range"):
        v = s.get(k)
        if isinstance(v, str) and "!" in v:
            name, ref = v.rsplit("!", 1)
            s[k], s["sheet"] = ref, name.strip().strip("'\"")
    return s


_CHANGE_DATA = re.compile(r"\b(change|replace|overwrite|update|correct|rename|edit|set|clean|trim|fix the (values|data|names)|बदल|ठीक)\b", re.I)
REQUEST = {"text": ""}  # the request being carried out, for the data-protection rule below


def _protect_inputs(t: Table, cells: list[str]):
    """The user's typed-in values (names, quantities, dates) are never overwritten unless the request asks to change them."""
    if _CHANGE_DATA.search(REQUEST["text"]):
        return
    typed = [a for a in cells if a not in t.formulas and t.value(a) not in (None, "")]
    if typed:
        raise StepError(f"{', '.join(typed[:4])}{' …' if len(typed) > 4 else ''} hold typed-in data "
                        f"({t.headers.get(re.match(r'[A-Z]+', typed[0]).group(0), '')}); do not overwrite it. "
                        "Put new results in a new column or another cell.")


def run_step(path: Path, step: dict, sheet: str | None) -> list[str]:
    """Applies one step to the file. Returns plain-language change notes."""
    step = _split_sheet(step)
    if step.get("sheet") and step["sheet"] not in sheet_names(path) and step.get("do") in ("set", "format", "table"):
        add_sheet(path, step["sheet"])
    t = read_table(path, step.get("sheet") or sheet)
    step = _normalize_step(t, step)
    do = step.get("do")
    fmt = step.get("number_format") or None
    if do in ("add_column", "fill"):
        L = _target_column(t, step) if do == "add_column" else str(step.get("column", "")).upper()
        head = str(step.get("header") or t.headers.get(L, ""))
        ratio = bool(_RATIO.search(head))
        if ratio and not fmt:
            fmt = "0.0%"  # a margin or share reads as 33.3%, not 0.3333333
        if fmt and "%" in str(fmt) and not ratio:
            fmt = "#,##0.00"  # "Profit per Order" is money, not a percentage (407.31 must not show as 40731%)
        if ratio and str(step.get("total") or "").upper() == "SUM":
            step = dict(step, total=None)  # percentages are never added up in a totals row
        if not re.fullmatch(r"[A-Z]{1,3}", L):
            raise StepError(f"{do}: say which column")
        cells = _fill(t, L, step.get("formula"), fmt)
        _protect_inputs(t, [c["cell"] for c in cells])
        if do == "add_column" and step.get("header"):
            cells.append(_cell(t.sheet, f"{L}{t.header}", value=str(step["header"])))
        agg = str(step.get("total") or "").upper()
        if t.totals and agg in AGGS:
            cells.append(_cell(t.sheet, f"{L}{t.totals}", formula=f"={agg}({L}{t.first}:{L}{t.last})", fmt=fmt))
        _write_cells(path, cells)
        what = f'column {L} "{step.get("header") or t.headers.get(L, "")}"'
        return [f"{'Added' if do == 'add_column' else 'Filled'} {what}: {step.get('formula')} in row {t.first}, "
                f"filled down to row {t.last}"]
    if do == "new_sheet":
        name = add_sheet(path, step.get("name") or "Summary")
        return [f'Added a sheet "{name}"']
    if do == "table":
        target = step.get("sheet") or t.sheet
        if target not in sheet_names(path):
            target = add_sheet(path, target)
        return write_block(path, target, step.get("rows") or [], step.get("at") or "A1", step.get("number_formats"))
    if do == "set":
        addr = str(step.get("cell", "")).upper()
        if not re.fullmatch(r"[A-Z]{1,3}\d{1,7}", addr):
            raise StepError(f"set: {addr!r} is not a cell address")
        L, row = re.match(r"([A-Z]+)(\d+)", addr).groups()
        f0 = step.get("formula") or (step.get("value") if isinstance(step.get("value"), str) and str(step["value"]).startswith("=") else None)
        if f0 and int(row) == t.first and t.last > t.first and f"{L}{t.first + 1}" in t.formulas:
            # a new formula in the first row of a computed column means the whole column, as in Excel's fill-down
            return run_step(path, {"do": "fill", "column": L, "formula": f0, "number_format": step.get("number_format"),
                                   "sheet": t.sheet}, t.sheet)
        f = clean_formula(step.get("formula") or step.get("expr") or (step.get("value") if isinstance(step.get("value"), str)
                                                                      and step["value"].strip().startswith("=") else None))
        if f is None and "value" not in step:
            raise StepError(f"set {addr}: give a formula or a value")
        if not re.search(rf"\b{addr}\b", REQUEST["text"]):  # a cell the request names may be changed
            _protect_inputs(t, [addr])
        _write_cells(path, [_cell(t.sheet, addr, formula=f, value=None if f else step.get("value"), fmt=fmt)])
        return [f"{addr} → {f or step.get('value')}"]
    if do == "total_row":
        cols = step.get("columns") or {}
        row = t.totals or t.last + 1
        cells = []
        label_col = next(iter(t.headers), "A")
        if not t.totals:
            cells.append(_cell(t.sheet, f"{label_col}{row}", value=str(step.get("label") or "Total")))
        for L, agg in cols.items():
            L, agg = str(L).upper(), str(agg or "").upper()
            if agg in ("", "NONE", "NULL", "NO", "-", "BLANK"):
                continue  # "no total for this column": nothing to do, not an error
            if agg == "SUM" and _RATIO.search(t.headers.get(L, "")):
                continue  # a total of percentages means nothing
            if agg not in AGGS or not re.fullmatch(r"[A-Z]{1,3}", L):
                raise StepError(f"total_row: use SUM, AVERAGE, COUNT, MAX, MIN or MEDIAN for a column letter, not {L}: {agg}")
            cells.append(_cell(t.sheet, f"{L}{row}", formula=f"={agg}({L}{t.first}:{L}{t.last})", fmt=fmt))
        if len(cells) == (0 if t.totals else 1):
            return []  # every column said "none": the step changes nothing
        _write_cells(path, cells)
        return [f"Totals in row {row}: " + ", ".join(f"{L} = {a}" for L, a in cols.items() if str(a or "").upper() in AGGS)]
    if do == "add_rows":
        new = [r for r in (step.get("rows") or []) if isinstance(r, list)]
        if not new:
            raise StepError("add_rows: give the rows to add")
        at = t.last + 1
        if t.totals:
            _structure(path, [{"op": "insert_rows", "row": at, "count": len(new), "sheet": t.sheet}])
        cells = []
        for k, values in enumerate(new):
            r = at + k
            for j in range(1, max(t.width, len(values)) + 1):
                L = col_name(j)
                v = values[j - 1] if j - 1 < len(values) else None
                above = t.formulas.get(f"{L}{t.last}")
                if isinstance(v, str) and v.startswith("="):
                    cells.append(_cell(t.sheet, f"{L}{r}", formula=v))
                elif above and (v is None or isinstance(v, (int, float))):
                    # a formula column keeps its formula; a number the model worked out itself is not trusted
                    cells.append(_cell(t.sheet, f"{L}{r}", formula=shift_rows(above, r - t.last)))
                elif v is not None:
                    cells.append(_cell(t.sheet, f"{L}{r}", value=v))
        new_last = t.last + len(new)
        if t.totals:
            moved = t.totals + len(new)
            for addr, f in t.formulas.items():
                if int(re.sub(r"[A-Z]+", "", addr)) == t.totals:
                    L = re.match(r"[A-Z]+", addr).group(0)
                    nf = _extend_ranges(f, t.last, new_last, t.first)
                    cells.append(_cell(t.sheet, f"{L}{moved}", formula=nf))
        _write_cells(path, cells)
        return [f"Added {len(new)} row(s) at row {at}" + (", totals now include them" if t.totals else "")]
    if do == "summary":
        g = str(step.get("group_by", "")).upper()
        vals = step.get("values") or []
        if g not in t.headers or not vals:
            raise StepError("summary: give group_by (a column letter) and values")
        keys = list(dict.fromkeys(v for v in t.column_values(g) if v is not None))
        at = str(step.get("at") or f"{col_name(t.width + 2)}{t.header}")
        dest = t.sheet
        if "!" in at:  # "Summary!A1": the table goes on another sheet and points back at this one
            dest, at = at.rsplit("!", 1)
            dest = dest.strip("'\"")
            if dest not in sheet_names(path):
                dest = add_sheet(path, dest)
        at = at.upper()
        m = re.fullmatch(r"([A-Z]{1,3})(\d+)", at)
        if not m:
            raise StepError('summary: "at" is a cell like J1 or Summary!A1')
        c0, r0 = col_num(m.group(1)), int(m.group(2))
        prefix = "" if dest == t.sheet else f"'{t.sheet}'!"
        rng = lambda L: f"{prefix}${L}${t.first}:${L}${t.last}"
        cells = [_cell(dest, f"{col_name(c0)}{r0}", value=t.headers[g], fmt=None)]
        cells[0]["style"] = {"bold": True, "fillColor": "#E8F0FE"}
        for k, spec in enumerate(vals, 1):
            L, agg = str(spec.get("column", "")).upper(), str(spec.get("agg", "SUM")).upper()
            if agg not in ("SUM", "AVERAGE", "COUNT", "MAX", "MIN"):
                raise StepError(f"summary: agg must be SUM, AVERAGE, COUNT, MAX or MIN, not {agg}")
            label = spec.get("label") or f"{agg.title()} of {t.headers.get(L, L)}" if agg != "COUNT" else spec.get("label") or "Count"
            cells.append(_cell(dest, f"{col_name(c0 + k)}{r0}", value=label) | {"style": {"bold": True, "fillColor": "#E8F0FE"}})
            for i, key in enumerate(keys, 1):
                kc = f"${col_name(c0)}{r0 + i}"
                f = (f"=COUNTIFS({rng(g)},{kc})" if agg == "COUNT" else
                     f"={agg}IFS({rng(L)},{rng(g)},{kc})" if agg != "SUM" else f"=SUMIFS({rng(L)},{rng(g)},{kc})")
                cells.append(_cell(dest, f"{col_name(c0 + k)}{r0 + i}", formula=f, fmt=spec.get("number_format")))
        for i, key in enumerate(keys, 1):
            cells.append(_cell(dest, f"{col_name(c0)}{r0 + i}", value=key))
        _write_cells(path, cells)
        return [f"Summary table on \"{dest}\" at {at}: {len(keys)} {t.headers[g]} value(s) with " + ", ".join(
            f"{str(v.get('agg', 'SUM')).upper()} of {t.headers.get(str(v.get('column', '')).upper(), v.get('column'))}" for v in vals)]
    if do == "sort":
        L = str(step.get("by", "")).upper()
        if L not in t.headers:
            raise StepError("sort: give by (a column letter)")
        desc = str(step.get("order", "asc")).lower().startswith("d")
        rows = list(range(t.first, t.last + 1))
        key = lambda r: (t.value(f"{L}{r}") is None, t.value(f"{L}{r}") if isinstance(t.value(f"{L}{r}"), (int, float)) else str(t.value(f"{L}{r}")))
        order = sorted(rows, key=key, reverse=False)
        if desc:
            filled = [r for r in order if t.value(f"{L}{r}") is not None][::-1]
            order = filled + [r for r in order if t.value(f"{L}{r}") is None]
        cells = []
        for new_r, old_r in zip(rows, order):
            for j in range(1, t.width + 1):
                a = f"{col_name(j)}{old_r}"
                f = t.formulas.get(a)
                cells.append(_cell(t.sheet, f"{col_name(j)}{new_r}", formula=shift_rows(f, new_r - old_r)) if f
                             else _cell(t.sheet, f"{col_name(j)}{new_r}", value=t.value(a)))
        _write_cells(path, cells)
        return [f"Sorted rows {t.first} to {t.last} by {t.headers[L]} ({'largest first' if desc else 'smallest first'})"]
    if do == "format":
        rng = str(step.get("range", "")).upper()
        m = re.fullmatch(r"([A-Z]{1,3})(\d+)?:([A-Z]{1,3})(\d+)?", rng)
        if not m or not fmt:
            return []  # nothing to format: not worth failing the whole change for
        r1, r2 = int(m.group(2) or t.first), int(m.group(4) or t.last)
        cells = [{"cell": f"{col_name(c)}{r}", "sheet": t.sheet, "style": {"numberFormat": fmt}}
                 for c in range(col_num(m.group(1)), col_num(m.group(3)) + 1) for r in range(r1, r2 + 1)]
        _write_cells(path, cells)
        return [f"Formatted {rng} as {fmt}"]
    if do == "delete_rows_where":
        L = str(step.get("column", "")).upper()
        want = step.get("equals")
        hit = [r for r in range(t.first, t.last + 1)
               if (t.value(f"{L}{r}") is None if want in (None, "", "blank") else str(t.value(f"{L}{r}")).strip().lower() == str(want).strip().lower())]
        if not hit:
            return ["No rows matched, nothing deleted"]
        _structure(path, [{"op": "delete_rows", "row": r, "count": 1, "sheet": t.sheet} for r in sorted(hit, reverse=True)])
        if t.totals:
            t2 = read_table(path, t.sheet)
            cells = [_cell(t.sheet, a, formula=_extend_ranges(f, t.last, t.last - len(hit), t.first))
                     for a, f in t2.formulas.items() if int(re.sub(r"[A-Z]+", "", a)) == t2.totals
                     and _extend_ranges(f, t.last, t.last - len(hit), t.first) != f]
            _write_cells(path, cells)
        return [f"Deleted {len(hit)} row(s) where {t.headers.get(L, L)} is {want or 'blank'}"]
    raise StepError(f"there is no step {do!r}. The steps are add_column, fill, set, total_row, add_rows, summary, sort, "
                    "format, delete_rows_where, new_sheet and table. A lookup is an add_column whose formula is "
                    "=XLOOKUP(C2,'Other sheet'!$A$2:$A$99,'Other sheet'!$B$2:$B$99,\"\").")


# ----------------------------------------------------------------------------------------------- planning
def describe_step(s: dict) -> str:
    """One line of the to-do list, in plain words."""
    do = s.get("do")
    f = s.get("formula") or ""
    if do == "build_sheet":
        spec = s["spec"]
        n = max(0, len(spec["rows"]) - 1)
        computed = [str(h) for h, v in zip(spec["rows"][0], spec["rows"][1] if len(spec["rows"]) > 1 else []) if isinstance(v, str) and v.startswith("=")]
        return f'Sheet “{spec.get("name") or "Sheet1"}”: {n} rows' + (f", formulas for {', '.join(computed[:4])}" if computed else "")
    if do == "add_column":
        return f'Add column “{s.get("header", "")}” = {f}' + (f', total {s["total"]}' if s.get("total") else "")
    if do == "fill":
        return f'Fill column {s.get("column", "")} with {f}'
    if do == "set":
        return f'Put {f or s.get("value")} in {s.get("cell", "")}'
    if do == "total_row":
        return "Totals row: " + ", ".join(f"{k} {v}" for k, v in (s.get("columns") or {}).items())
    if do == "add_rows":
        return f'Add {len(s.get("rows") or [])} row(s)'
    if do == "summary":
        vals = ", ".join(f'{v.get("agg", "SUM")} of {v.get("column")}' for v in s.get("values") or [] if isinstance(v, dict))
        return f'Summary by {s.get("group_by")}: {vals}' + (f' at {s["at"]}' if s.get("at") else "")
    if do == "sort":
        return f'Sort by {s.get("by")} ({s.get("order", "asc")})'
    if do == "format":
        return f'Format {s.get("range")} as {s.get("number_format")}'
    if do == "delete_rows_where":
        return f'Delete rows where {s.get("column")} is {s.get("equals")}'
    if do == "new_sheet":
        return f'New sheet “{s.get("name")}”'
    if do == "table":
        rows = s.get("rows") or []
        return f'Table on “{s.get("sheet")}” at {s.get("at", "A1")}: {max(0, len(rows) - 1)} row(s)'
    return str(do)


CHECKS = ["Compute every formula", "Check for errors", "Check the result matches the request"]


def step_target(path: Path, step: dict, sheet: str) -> dict | None:
    """Where a step works: {"sheet", "range"}, so the canvas can show it (switch tab, select the column or cell)."""
    try:
        if step.get("do") == "build_sheet":
            spec = step["spec"]
            rows = spec["rows"]
            return {"sheet": spec.get("name") or sheet, "range": f"A1:{col_name(max(1, len(rows[0])))}{len(rows)}"}
        s = _split_sheet(step)
        do = s.get("do")
        if do == "new_sheet":
            return {"sheet": s.get("name"), "range": "A1"}
        if do == "table":
            rows = s.get("rows") or [[]]
            at = str(s.get("at") or "A1").upper()
            m = re.fullmatch(r"([A-Z]{1,3})(\d+)", at) or re.fullmatch(r"(A)(1)", "A1")
            c0, r0 = col_num(m.group(1)), int(m.group(2))
            return {"sheet": s.get("sheet") or sheet,
                    "range": f"{at}:{col_name(c0 + max(1, len(rows[0])) - 1)}{r0 + len(rows) - 1}"}
        target_sheet = s.get("sheet") or sheet
        if target_sheet not in sheet_names(path):
            return {"sheet": target_sheet, "range": "A1"}
        t = read_table(path, target_sheet)
        s = _normalize_step(t, s)
        if do == "add_column":
            L = _target_column(t, s)
            return {"sheet": t.sheet, "range": f"{L}{t.header}:{L}{t.last}"}
        if do == "fill":
            L = str(s.get("column") or "").upper()
            return {"sheet": t.sheet, "range": f"{L}{t.first}:{L}{t.last}"}
        if do == "set":
            return {"sheet": t.sheet, "range": str(s.get("cell", "A1")).upper()}
        if do == "total_row":
            row = t.totals or t.last + 1
            return {"sheet": t.sheet, "range": f"A{row}:{col_name(max(1, t.width))}{row}"}
        if do == "add_rows":
            n = len(s.get("rows") or [])
            return {"sheet": t.sheet, "range": f"A{t.last + 1}:{col_name(max(1, t.width))}{t.last + max(1, n)}"}
        if do == "summary":
            at = str(s.get("at") or f"{col_name(t.width + 2)}{t.header}")
            dest = t.sheet
            if "!" in at:
                dest, at = at.rsplit("!", 1)
                dest = dest.strip("'\"")
            m = re.fullmatch(r"([A-Z]{1,3})(\d+)", at.upper())
            if m:
                g = str(s.get("group_by", "")).upper()
                keys = len({v for v in t.column_values(g) if v is not None}) if g in t.headers else 5
                c0, r0 = col_num(m.group(1)), int(m.group(2))
                return {"sheet": dest, "range": f"{at.upper()}:{col_name(c0 + len(s.get('values') or [1]))}{r0 + keys}"}
        if do in ("sort", "delete_rows_where"):
            return {"sheet": t.sheet, "range": f"A{t.first}:{col_name(max(1, t.width))}{t.last}"}
        if do == "format" and s.get("range"):
            return {"sheet": t.sheet, "range": str(s["range"]).upper()}
    except Exception:
        return None
    return None

RULES = """You edit an Excel sheet by giving steps. Vyum writes them as real Excel formulas and computes them; you never
calculate numbers yourself. Return JSON only:
{{"approach": "2 or 3 short sentences: what the request needs and how you will do it", "summary": "one short sentence", "steps": [ ... ]}}

Steps:
  {{"do": "add_column", "header": "...", "formula": "=...", "number_format": "0.0%", "total": "SUM"}}
      A new column (Vyum picks the next empty one). Write the formula for the FIRST data row (row {first}) only, with
      relative references for that row (B{first}) and $ anchors for fixed ranges ($B${first}:$B${last}). Vyum fills it
      down to row {last}. "total" (optional) also fills the totals row: SUM, AVERAGE, COUNT, MAX or MIN.
  {{"do": "fill", "column": "E", "formula": "=..."}}            replace an existing column's formula, same rules
  {{"do": "set", "cell": "H2", "formula": "=..."}}  or  {{"do": "set", "cell": "H2", "value": 42}}
  {{"do": "total_row", "columns": {{"C": "SUM", "D": "AVERAGE"}}}}   totals under the data
  {{"do": "add_rows", "rows": [["North", 120, 4.5]]}}           new data rows (formula columns and totals follow)
  {{"do": "summary", "group_by": "A", "values": [{{"column": "D", "agg": "SUM"}}], "at": "J1"}}
      a pivot-style table: one row per distinct value of the group_by column, computed with SUMIFS/COUNTIFS/AVERAGEIFS.
      "number of sales/orders/rows" is {{"column": "A", "agg": "COUNT"}}; a total of a column is {{"column": "F", "agg": "SUM"}}
  {{"do": "sort", "by": "D", "order": "desc"}}
  {{"do": "format", "range": "E{first}:E{last}", "number_format": "#,##0.00"}}
  {{"do": "delete_rows_where", "column": "C", "equals": "blank"}}
  {{"do": "new_sheet", "name": "Summary"}}                      a new, empty sheet tab
  {{"do": "table", "sheet": "Summary", "at": "A1", "rows": [["Region", "Revenue"], ["North", "=SUMIFS(Sales!$F${first}:$F${last},Sales!$B${first}:$B${last},A2)"]],
   "number_formats": {{"B": "#,##0"}}}}   a block of values and formulas on any sheet; formulas use real cell positions
  A summary can go on another sheet: "at": "Summary!A1".
Formula rules:
- A formula is one Excel formula starting with "=", for example =ROUND(B{first}*1.18, 2) or =IF(C{first}>=50, "High", "Low").
  Never write ="="&... .
- "Number of" / "how many" / "count" means COUNT or COUNTIFS (rows), never SUM.
- Refer to cells by their letters from the map below. Data rows are {first} to {last}; never include the header or the totals row
  in ranges.
- Available: SUM, SUMIFS, COUNTIFS, AVERAGEIFS, MAXIFS, MINIFS, IF, IFS, AND, OR, IFERROR, XLOOKUP, INDEX, MATCH, VLOOKUP,
  ROUND, ABS, RANK.EQ, MEDIAN, STDEV.S, LARGE, SMALL, PMT, NPV, IRR, XIRR, FV, PV, RATE, EDATE, EOMONTH, NETWORKDAYS,
  WORKDAY, DATEDIF, YEARFRAC, TODAY, DATE, YEAR, MONTH, DAY, TEXT, LEFT, RIGHT, MID, LEN, TRIM, UPPER, LOWER,
  SUBSTITUTE, TEXTBEFORE, TEXTAFTER, TEXTJOIN, CONCAT and most other classic Excel functions.
- NOT available: FILTER, UNIQUE, SORT, LET, LAMBDA, SUMPRODUCT, TEXTSPLIT, PROPER, SEQUENCE. Use the summary or sort step,
  SUMIFS, or a helper column instead.
- Another sheet: 'Sheet name'!B2. Text criteria in quotes: "North".
- Dates: one month is ">="&DATE(2025,2,1) together with "<"&DATE(2025,3,1). Never use text like "2025-02*" for dates.
- One result in one named cell ("in J8 put ...") is ONE set step on that cell with the whole formula in it; never add a
  helper column for it.
- Later steps can use columns added by earlier steps: refer to them by their header text (e.g. "group_by": "Department").
- A report per product / region / rep on a new sheet is ONE summary step: "group_by" the column, "values" with COUNT for
  "number of" and SUM for totals, and "at": "Product Report!A1" (Vyum creates the sheet). Never add columns for a report.
- Steps work on the sheet in focus; add "sheet": "Orders" to a step to work on another sheet's data. Cells and ranges on
  another sheet are written 'Product Report'!D1.
- Percent or growth: divide, and set number_format "0.0%" (do not multiply by 100).
- SUM, AVERAGE, COUNT, MIN and MAX already skip blank cells: use them directly on the column (=AVERAGE(E{first}:E{last})).
  Never build a helper column just to skip blanks.
- Work like an expert: split hard logic into helper columns, one simple formula each. A lookup first (e.g. a Department
  column with =XLOOKUP(C{first},'Staff'!$A$2:$A$99,'Staff'!$B$2:$B$99,"")), then the column that uses it
  (=F{first}*IFS(H{first}="Sales",5%,H{first}="Enterprise",8%,TRUE,3%)). Never one giant nested formula.
- Conditional averages, sums and counts use AVERAGEIFS / SUMIFS / COUNTIFS, never IF() over a range.
- NETWORKDAYS(start, end) and WORKDAY(start, days) take no holiday list unless the workbook has one.
- Change only what the user asked for. Put any constant the user gives (a rate, a target) in the formula itself."""


BUILD_RULES = """You build tables on an empty workbook. Vyum writes it from cell A1 and the spreadsheet engine computes every
formula; you never calculate numbers yourself. Return JSON only:
{"approach": "2 or 3 short sentences: which sheets, how they link, which formulas", "summary": "one short sentence", "sheets": [
  {"name": "Orders", "headers": ["Date", "Product", "Qty", "Region", "Price", "Revenue"],
   "rows": [["2025-01-05", "Laptop", 2, "North"], ["2025-01-07", "Mouse", 5, "South"]],
   "formulas": {"Price": "=XLOOKUP([Product], Products[Product], Products[Unit Price], 0)", "Revenue": "=[Qty]*[Price]"},
   "totals": {"Qty": "SUM", "Revenue": "SUM"}, "number_formats": {"Price": "#,##0", "Revenue": "#,##0"}},
  {"name": "Summary", "headers": ["Region", "Revenue"], "rows": [["North"], ["South"]],
   "formulas": {"Revenue": "=SUMIFS(Orders[Revenue], Orders[Region], [Region])"}}]}
- "rows" hold ONLY the typed-in input values, in the order of the headers (input columns first, computed columns last).
  Every computed column is ONE formula in "formulas"; Vyum fills it down every row. Never type a computed number.
- In formulas, name columns instead of using letters: [Qty] is this row's Qty cell; Orders[Revenue] is the whole Revenue
  column of the Orders sheet. Vyum turns them into exact cell references.
- "totals" (optional) adds a Total row: SUM, AVERAGE, COUNT, MAX or MIN per column name.
- Write exactly as many rows as the request asks for (25 orders = 25 rows). Dates as "YYYY-MM-DD".
- Keep per-unit and total amounts apart: looked-up per-unit values are "Unit Price" / "Unit Cost"; an order's totals are
  "Revenue" =[Qty]*[Unit Price], "Total Cost" =[Qty]*[Unit Cost], "Profit" =[Revenue]-[Total Cost].
- Margins, shares and rates are fractions with number format "0.0%"; money uses "#,##0".
- One sheet unless the request asks for several (at most 6), in the order they depend on each other.
- Row 1 is the header. Item k of "rows" is spreadsheet row k, so the first data row is row 2.
- Every computed cell is an Excel formula starting with "=" that points at its own row, e.g. a difference in row 2 is
  =C2-B2 and in row 3 is =C3-B3. A total row uses =SUM(B2:B7) over the data rows only.
- Numbers are JSON numbers. Use the user's numbers exactly; when none are given, use realistic example values.
- Available functions: the classic Excel set (SUM, SUMIFS, COUNTIFS, AVERAGE, IF, IFS, XLOOKUP, ROUND, PMT, EDATE, EOMONTH,
  TEXT, ...). NOT available: FILTER, UNIQUE, SORT, LET, LAMBDA, SUMPRODUCT, TEXTSPLIT, PROPER, SEQUENCE.
- Percent cells: a fraction with number format "0.0%". At most 60 rows and 12 columns per sheet."""


def infer_formulas(rows: list[list]) -> tuple[list[list], list[str]]:
    """Numbers the model computed itself become the formulas they came from: a column that equals B-C (or B+C, B*C,
    B/C) on every data row is written as =B2-C2 ...; a total row's number that equals the column's sum becomes =SUM()."""
    rows = [list(r) for r in rows]
    width = max(len(r) for r in rows)
    for r in rows:
        r += [None] * (width - len(r))
    totals = [i for i in range(2, len(rows)) if _TOTAL_LABEL.match(str(rows[i][0] or ""))]
    data = [i for i in range(1, len(rows)) if i not in totals]
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    cols = [j for j in range(width) if data and all(num(rows[i][j]) for i in data)]
    notes = []
    if len(data) >= 2:
        ops = [("-", lambda a, b: a - b), ("+", lambda a, b: a + b), ("*", lambda a, b: a * b), ("/", lambda a, b: a / b if b else None)]
        for t in reversed(cols):  # a computed column sits to the right of the columns it is computed from
            done = False
            for a in cols:
                for b in cols:
                    if done or len({a, b, t}) < 3 or a > t or b > t:
                        continue
                    for sym, f in ops:
                        if all(num(rows[i][a]) and num(rows[i][b]) and num(rows[i][t]) and f(rows[i][a], rows[i][b]) is not None and abs(f(rows[i][a], rows[i][b]) - rows[i][t]) <= 1e-9 * max(1, abs(rows[i][t]))
                               for i in data):
                            for i in data:
                                rows[i][t] = f"={col_name(a + 1)}{i + 1}{sym}{col_name(b + 1)}{i + 1}"
                            notes.append(f"Column {col_name(t + 1)} is now a formula ({col_name(a + 1)}{sym}{col_name(b + 1)}) instead of typed numbers")
                            done = True
                            break
    first, last = data[0] + 1, data[-1] + 1
    for i in totals:
        for j in range(1, width):
            v = rows[i][j]
            col_vals = [rows[k][j] for k in data]
            if num(v) and all(num(x) or (isinstance(x, str) and x.startswith("=")) for x in col_vals):
                known = [x for x in col_vals if num(x)]
                if len(known) == len(col_vals) and abs(sum(known) - v) <= 1e-9 * max(1, abs(v)) or len(known) < len(col_vals):
                    rows[i][j] = f"=SUM({col_name(j + 1)}{first}:{col_name(j + 1)}{last})"
    return rows, notes


def write_block(path: Path, sheet: str, rows: list, at: str = "A1", fmts: dict | None = None) -> list[str]:
    """A block of values and formulas with its top-left at `at`; the first row is a styled header, a Total row is bold.
    Numbers the model worked out from other columns become formulas (infer_formulas) when the block starts at A1."""
    rows = [r for r in rows if isinstance(r, list)][:201]
    if len(rows) < 2:
        raise StepError("give a header row and at least one data row")
    m = re.fullmatch(r"([A-Z]{1,3})(\d+)", str(at or "A1").upper())
    c0, r0 = (col_num(m.group(1)), int(m.group(2))) if m else (1, 1)
    inferred = []
    if (c0, r0) == (1, 1):
        rows, inferred = infer_formulas(rows)
    fmts = {str(k).upper(): v for k, v in (fmts or {}).items() if isinstance(v, str)}
    for j, h in enumerate(rows[0][:26]):  # margins and shares read as percentages, not 0.7288293897882939
        L = col_name(c0 + j)
        if L not in fmts and re.search(r"margin|share|percent|%|growth", str(h or ""), re.I):
            fmts[L] = "0.0%"
    cells = []
    for i, row in enumerate(rows):
        is_total = i > 1 and bool(_TOTAL_LABEL.match(str(row[0] if row else "")))
        for j, v in enumerate(row[:26]):
            if v is None or v == "":
                continue
            L = col_name(c0 + j)
            style = {"bold": True, "fillColor": "#E8F0FE"} if i == 0 else ({"bold": True} if is_total else None)
            fmt = None if i == 0 else fmts.get(L)
            if i and isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()):
                try:  # a real date (Excel serial number shown as a date), so MONTH(), EOMONTH() and sorting work
                    v = (datetime.date.fromisoformat(v.strip()) - datetime.date(1899, 12, 30)).days
                    fmt = fmt or "yyyy-mm-dd"
                except ValueError:
                    pass
            c = _cell(sheet, f"{L}{r0 + i}", value=v, fmt=fmt)
            if style:
                c["style"] = {**c.get("style", {}), **style}
            cells.append(c)
    _write_cells(path, cells)
    head = ", ".join(str(h) for h in rows[0][:8] if h)
    return [f'Wrote a table on "{sheet}" at {col_name(c0)}{r0}: {len(rows) - 1} row(s) under {head}'] + inferred


def resolve_names(f: str, here: str, cols: dict[str, dict[str, str]], last: dict[str, int]) -> str:
    """Structured references, so the model never counts column letters:
       [Unit Price]           -> D2 (this row; filled down later)
       Products[Unit Price]   -> 'Products'!$C$2:$C$9 (that column's data rows)"""
    def letter(sheet, name):
        return cols.get(sheet, {}).get(name.strip().lower())
    def qualified(m):
        sheet = m.group(1).strip("'")
        L = letter(sheet, m.group(2))
        if not L:
            raise StepError(f'{f}: sheet "{sheet}" has no column "{m.group(2)}". Its columns: {", ".join(cols.get(sheet, {}).keys()) or "none"}')
        return f"'{sheet}'!${L}$2:${L}${last.get(sheet, 2)}"
    def local(m):
        L = letter(here, m.group(1))
        if not L:
            raise StepError(f'{f}: this sheet has no column "{m.group(1)}". Its columns: {", ".join(cols.get(here, {}).keys())}')
        return f"{L}2"
    parts = re.split(r'("(?:[^"]|"")*")', f)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"('[^']+'|[A-Za-z_][\w ]*?)\[([^\]]+)\]", qualified, parts[i])
        parts[i] = re.sub(r"\[([^\]]+)\]", local, parts[i])
    return "".join(parts)


def expand_spec(spec: dict) -> list[list]:
    """{"headers", "rows" of inputs, "formulas" per column, "totals"} -> full rows with formulas filled down."""
    headers = spec.get("headers")
    rows = [r for r in (spec.get("rows") or []) if isinstance(r, list)]
    if not headers:
        return rows  # the older shape: rows[0] is the header and rows are complete
    lower = [str(h).strip().lower() for h in headers]
    def key(k):  # a formula or total may be keyed by header text or by column letter
        k = str(k).strip()
        return col_name(lower.index(k.lower()) + 1) if k.lower() in lower else k.upper()
    formulas = {key(k): v for k, v in (spec.get("formulas") or {}).items() if isinstance(v, str)}
    width = len(headers)
    out = [list(headers)]
    for i, r in enumerate(rows):
        full = list(r[:width]) + [None] * (width - len(r[:width]))
        for L, f in formulas.items():
            j = col_num(L) - 1
            if 0 <= j < width:
                full[j] = shift_rows(clean_formula(f), i)
        out.append(full)
    totals = {key(k): str(v).upper() for k, v in (spec.get("totals") or {}).items()}
    if totals and rows:
        last = len(rows) + 1
        tot = ["Total"] + [None] * (width - 1)
        for L, agg in totals.items():
            j = col_num(L) - 1
            if 0 < j < width and agg in AGGS:
                tot[j] = f"={agg}({L}2:{L}{last})"
        out.append(tot)
    return out


def build_specs(p: dict) -> list[dict]:
    """The planned sheets, expanded (formulas filled down) and with cross-sheet ranges fitted to the real rows."""
    raw = [x for x in (p.get("sheets") or []) if isinstance(x, dict) and x.get("rows")]
    cols = {str(x.get("name")): {str(h).strip().lower(): col_name(j + 1) for j, h in enumerate(x.get("headers") or [])} for x in raw}
    rows_of = {str(x.get("name")): 1 + len([r for r in x["rows"] if isinstance(r, list)]) for x in raw}
    for x in raw:  # [Header] and Sheet[Header] become cell references before anything else
        x["formulas"] = {k: resolve_names(clean_formula(v), str(x.get("name")), cols, rows_of)
                         for k, v in (x.get("formulas") or {}).items() if isinstance(v, str)}
    specs = [dict(x, rows=expand_spec(x)) for x in raw]
    # ranges into another sheet of this plan end at that sheet's real last data row ('Orders'!$E$2:$E$30 -> $E$26)
    last = {str(x.get("name")): len(x["rows"]) - (1 if _TOTAL_LABEL.match(str((x["rows"][-1] or [""])[0])) else 0)
            for x in specs if x.get("name")}

    def fix(f):
        if not isinstance(f, str) or not f.startswith("="):
            return f
        return re.sub(r"('?)([^'!,(=]+)\1!(\$?[A-Z]{1,3}\$?)2:(\$?[A-Z]{1,3}\$?)(\d+)",
                      lambda m: f"{m.group(1)}{m.group(2)}{m.group(1)}!{m.group(3)}2:{m.group(4)}{last[m.group(2)]}"
                      if m.group(2) in last else m.group(0), f)
    for x in specs:
        x["rows"] = [[fix(v) for v in r] for r in x["rows"]]
    if not specs and p.get("rows"):
        specs = [{"name": None, "rows": p["rows"], "number_formats": p.get("number_formats")}]
    return specs[:6]


def check_counts(specs: list[dict], instruction: str):
    """"25 orders" must give 25 order rows: a short table is caught before anything is written."""
    for n, word in re.findall(r"\b(\d{1,3})\s+([A-Za-z]+)", instruction):
        n, stem = int(n), word.lower().rstrip("s")
        if n < 2 or len(stem) < 3:
            continue
        for x in specs:
            name = str(x.get("name") or "").lower().rstrip("s")
            if name and (stem in name or name in stem):
                rows = len(x["rows"]) - 1 - (1 if _TOTAL_LABEL.match(str((x["rows"][-1] or [""])[0])) else 0)
                if rows < n:
                    raise StepError(f'Sheet "{x.get("name")}" has {rows} rows but the request asks for {n} {word}: '
                                    f"write all {n} rows of input values.")


def build_sheet(path: Path, sheet: str, k: int, spec: dict) -> list[str]:
    name = spec.get("name")
    if k == 0:
        target = rename_sheet(path, sheet, name) if name and name != sheet else sheet
    else:
        target = add_sheet(path, name or f"Sheet{k + 1}")
    return write_block(path, target, spec["rows"], "A1", spec.get("number_formats"))


def build(path: Path, sheet: str, p: dict) -> list[str]:
    notes = []
    for k, spec in enumerate(build_specs(p)):
        notes += build_sheet(path, sheet, k, spec)
    return notes


def plan(path: Path, instruction: str, sheet: str | None, feedback: str = "") -> dict:
    from .office import _plan
    t = read_table(path, sheet)
    system = RULES.format(first=t.first, last=t.last)
    fixing = bool(re.search(r"#|error|fix|broken|wrong|repair|गलत|ठीक", instruction, re.I))
    user = f"Workbook map:\n{describe(path, t.sheet, show_errors=fixing)}\n\nRequest: {instruction}"
    if feedback:
        user += f"\n\nYour previous steps failed:\n{feedback}\nReturn corrected steps for the whole request."
    return _plan(system, user)


def _errors_after(path: Path, sheet: str, before: set) -> list[str]:
    from .office import _xlsx_formulas
    formulas = _xlsx_formulas(path).get(sheet, {})
    return [f"{a} shows {v} from {formulas.get(a, '(value)')}" for a, v in cell_errors(path, sheet).items() if a not in before]


def edit(path: Path, instruction: str):
    """Generator of status events; returns (summary, changes). Plan -> check -> write -> compute -> read back; errors go
    back to the model (twice at most). Raises StepError if the sheet cannot be changed cleanly (the file is restored)."""
    status = lambda text: {"type": "status", "text": text}
    t0 = read_table(path)
    sheet = t0.sheet
    names = sheet_names(path)
    def source_only(n):  # "look up ... in the Bands sheet", "from the Staff sheet": a sheet read from, not written to
        return bool(re.search(rf"\b(look(ed|ing)?\s*up|lookup|from|of|matching|match)\b[^.;]{{0,60}}\b{re.escape(n)}\b", instruction, re.I))
    for n in names:  # "in the Staff sheet" -> plan on that sheet; "from the Staff sheet" only reads it
        if n != sheet and not source_only(n) and re.search(rf"\b(in|on|into|to|using|use|with)\s+(the\s+)?['\"]?{re.escape(n)}['\"]?(\s+(sheet|tab|data))?\b", instruction, re.I):
            sheet = n
    for n in names:  # Hindi and Hinglish put the place after the name: "Summary शीट में", "Summary sheet mein"
        if n != sheet and re.search(rf"{re.escape(n)}['\"]?\s*(शीट|sheet|tab|टैब)?\s*(में|मे|पर|mein|me|par)(?![\w\u0900-\u097F])", instruction, re.I):
            sheet = n
    if sheet == t0.sheet:  # one sheet named, and not only as a source ("from the Staff sheet"): work there
        named = [n for n in names if n != t0.sheet and re.search(rf"\b{re.escape(n)}\b", instruction)  # as written
                 and not source_only(n)]
        if len(named) == 1 and not re.search(r"new sheet|add a sheet|sheet called|sheet named", instruction, re.I):
            sheet = named[0]
    if sheet == t0.sheet and re.search(r"new sheet|add a sheet|sheet called|sheet named", instruction, re.I):
        mentioned = [n for n in names if re.search(rf"\b{re.escape(n)}\b", instruction, re.I)]
        if mentioned:  # a new report sheet is built from the data sheet the request names
            sheet = max(mentioned, key=lambda n: len(read_table(path, n).rows))
    before = {(n, a) for n in names for a in cell_errors(path, n)}
    broken_cols = {(n, re.match(r"[A-Z]+", a).group(0), v) for n in names for a, v in cell_errors(path, n).items()}
    backup = Path(tempfile.mkdtemp()) / path.name
    shutil.copy2(path, backup)
    feedback, last_error = "", ""
    REQUEST["text"] = instruction
    candidate = None  # an attempt with no errors that only the model's own review doubted
    empty = not any(v is not None for r in read_table(path, sheet).rows for v in r)
    try:
        for attempt in range(3):
            yield status("Planning the formulas" if not attempt else "Fixing the formulas")
            try:
                if empty:
                    from .office import _plan
                    p = _plan(BUILD_RULES, f"Request: {instruction}" + (f"\n\nYour previous table failed:\n{feedback}\n"
                                                                       "Return the corrected table." if feedback else ""))
                else:
                    p = plan(path, instruction, sheet, feedback)
            except Exception as e:  # unreadable JSON: ask again
                feedback, last_error = "Your reply was not valid JSON. Return only the JSON object with summary and steps.", str(e)
                continue
            if candidate and not empty and json.dumps(p.get("steps"), sort_keys=True) == candidate[4]:
                # the planner stood by its steps after the review: the checked result it already produced stands
                keep, summary, notes, doubt, _ = candidate
                shutil.copy2(keep, path)
                shutil.rmtree(keep.parent, ignore_errors=True)
                yield {"type": "file_restored"}
                yield {"type": "thinking", "text": "The planner stood by its steps; keeping the checked result.\n\n"}
                return summary, notes
            if empty:
                try:
                    specs = build_specs(p)
                    check_counts(specs, instruction)
                except StepError as e:
                    feedback, last_error = str(e), str(e)
                    yield {"type": "thinking", "text": f"{p.get('approach') or ''}\n\nProblem before writing: {e}\n\n"}
                    continue
                steps = [{"do": "build_sheet", "k": k, "spec": spec} for k, spec in enumerate(specs)]
            else:
                steps = [s for s in p.get("steps") or [] if isinstance(s, dict)]
            thought = str(p.get("approach") or "").strip()
            if thought or feedback:
                yield {"type": "thinking", "text": ((f"Try {attempt + 1}. The last try failed: {feedback}\n\n" if feedback else "")
                                                    + thought + "\n\n")}
            if not steps:
                last_error = "The model did not return any steps."
                feedback = "You returned no steps."
                continue
            notes = []
            items = [describe_step(s) for s in steps]
            yield {"type": "plan", "attempt": attempt + 1, "items": items + CHECKS}
            n = len(items)
            current = 0
            try:
                yield status(f"Writing {len(steps)} step{'s' if len(steps) > 1 else ''} as formulas")
                for current, s in enumerate(steps):
                    yield {"type": "plan_step", "index": current, "state": "running",
                           "target": step_target(path, s, sheet)}
                    notes += build_sheet(path, sheet, s["k"], s["spec"]) if s.get("do") == "build_sheet" else run_step(path, s, sheet)
                    yield {"type": "plan_step", "index": current, "state": "done"}
                current = n
                yield {"type": "plan_step", "index": n, "state": "running"}
                from .office import recalc
                touched = set(sheet_names(path))  # new tabs, other-sheet tables and summaries: compute and check them all
                recalc(path, touched)
                yield {"type": "plan_step", "index": n, "state": "done"}
                current = n + 1
                yield {"type": "plan_step", "index": n + 1, "state": "running"}
                errs = []
                for sh in touched:
                    errs += [f"{sh}!{e}" for e in _errors_after(path, sh, {a for (m, a) in before if m == sh})
                             if (sh, re.match(r"[A-Z]+", e).group(0), e.split(" shows ")[1].split(" ")[0]) not in broken_cols]
                if not errs:
                    yield {"type": "plan_step", "index": n + 1, "state": "done"}
                    current = n + 2
                    yield {"type": "plan_step", "index": n + 2, "state": "running"}
                    problem = review(path, sheet, instruction, steps) if attempt < 2 else hard_checks(read_table(path, sheet), instruction, steps)
                    if not problem:
                        yield {"type": "plan_step", "index": n + 2, "state": "done"}
                        return p.get("summary", ""), notes + _results(path, sheet, steps)
                    yield {"type": "plan_step", "index": n + 2, "state": "failed", "note": problem}
                    yield {"type": "thinking", "text": f"Self-check: {problem}\n\n"}
                    if candidate is None and not hard_checks(read_table(path, sheet), instruction, steps):
                        keep = Path(tempfile.mkdtemp()) / path.name
                        shutil.copy2(path, keep)
                        candidate = (keep, p.get("summary", ""), notes + _results(path, sheet, steps), problem,
                                     json.dumps(p.get("steps"), sort_keys=True))
                    yield status("Checking the result")
                    feedback = (f"The formulas ran, but a reviewer thinks the result is wrong: {problem}\n"
                                "If you are sure your steps were right, return exactly the same steps again.")
                    last_error = f"The result did not match the request ({problem})"
                    shutil.copy2(backup, path)
                    yield {"type": "file_restored"}
                    continue
                feedback = "These cells show errors after computing:\n" + "\n".join(errs[:12])
                last_error = f"The formulas gave errors ({'; '.join(errs[:3])})"
                yield {"type": "plan_step", "index": n + 1, "state": "failed", "note": "; ".join(errs[:3])}
                yield {"type": "thinking", "text": f"Errors after computing: {'; '.join(errs[:4])}\n\n"}
            except StepError as e:
                feedback, last_error = str(e), str(e)
                yield {"type": "plan_step", "index": current, "state": "failed", "note": str(e)[:300]}
                yield {"type": "thinking", "text": f"Problem: {str(e)[:400]}\n\n"}
            shutil.copy2(backup, path)  # start the next attempt from the original sheet
            yield {"type": "file_restored"}
        if candidate:  # better a clean result the review doubted than nothing (or a later, worse try)
            keep, summary, notes, doubt, _ = candidate
            shutil.copy2(keep, path)
            shutil.rmtree(keep.parent, ignore_errors=True)
            yield {"type": "file_restored"}
            return summary, notes + [f"Please check: Vyum's self-check was not sure about this: {doubt}"]
        raise StepError(f"I could not make this change cleanly, so the sheet was not changed. Try saying it another way. "
                        f"(Details: {last_error})")
    finally:
        shutil.rmtree(backup.parent, ignore_errors=True)


REVIEW = """You check a spreadsheet change. You see the request, the steps that were applied, and the values the
spreadsheet engine computed. Decide whether the computed values do what the request asked, for every row shown, and
whether EVERY part of the request was done (a request with "then" or "and" has several parts).
Look for wrong logic (a wrong band or condition), per-unit amounts used as totals (profit must subtract quantity x unit
cost, not the unit cost), the wrong column, counts that are sums, text glued together, dates
shown as numbers, totals that include the header or totals row, and results that are 0 or empty where the data
should give a value. Do not recompute exact numbers yourself.
The numbers were computed exactly by the spreadsheet engine: never add up or recompute numbers yourself, and never
reject a total because your own sum differs. Judge only the logic: the right columns, conditions, per-unit vs total,
and whether every part of the request is there. Only reject when you can name a specific cell whose LOGIC is wrong.
If you are not sure, it is ok.
Return JSON only: {"ok": true} or {"ok": false, "problem": "which cell is wrong, what it shows, and how the formula must change"}"""


def hard_checks(t: Table, instruction: str, steps: list[dict]) -> str | None:
    """Problems found without asking the model: empty results, a named cell left empty or zero, counts that were summed,
    a single-cell request answered with a helper column."""
    # "in J8", "into cell D2", "at H1": a target cell (not "Q1" in "Q1, Q2" or "2019 to 2024")
    named = sorted(set(re.findall(r"\b(?i:in|into|at|to|cell)\s+(?:(?i:cell)\s+)?([A-Z]{1,3}[1-9]\d{0,5})\b", instruction)))
    for a in named:
        v = t.value(a)
        if v is None or v == "":
            return f"{a} is empty; the request asks for a result in {a}. Write ONE set step with the full formula in {a}."
        if isinstance(v, str) and re.search(r"\d", v) and re.search(r"total|sum|average|count|how many|payment|cagr|rate|कुल|औसत|कितने", instruction, re.I):
            return (f'{a} shows the text "{v[:60]}"; the request asks for a number. Put only the formula that computes '
                    "it in the cell, with no words around it.")
        if isinstance(v, (int, float)) and v == 0 and re.search(r"total|sum|average|revenue|count|कुल|औसत", instruction, re.I):
            return (f"{a} came out 0, which is almost certainly wrong for this request. Check the criteria: dates need "
                    f'">="&DATE(y,m,1) and "<"&DATE(y,m+1,1); text must match the column exactly.')
    if len(named) == 1 and any(s.get("do") in ("add_column", "fill") for s in steps) and not re.search(r"column|कॉलम", instruction, re.I):
        return f"The request asks for one cell ({named[0]}); do not add a column. Use one set step on {named[0]}."
    if re.search(r"new sheet|add a sheet|sheet called|sheet named", instruction, re.I) and not any(
            s.get("do") in ("new_sheet", "table", "build_sheet") or "!" in str(s.get("at") or "") for s in steps):
        return 'The request asks for a new sheet: put the table there (a summary step with "at": "<Sheet name>!A1").'
    for s in steps:
        if s.get("do") in ("add_column", "fill"):
            L = next((k for k, v in t.headers.items() if v == s.get("header")), str(s.get("column") or "").upper())
            if L and re.fullmatch(r"[A-Z]{1,3}", L):
                vals = [t.value(f"{L}{r}") for r in range(t.first, t.last + 1)]
                if vals and all(v in (None, "") for v in vals):
                    return f"Column {L} ({t.headers.get(L, '')}) came out empty on every row; its formula is wrong."
                if len(vals) > 1 and all(isinstance(v, (int, float)) and v == 0 for v in vals):
                    return (f"Column {L} ({t.headers.get(L, '')}) came out 0 on every row: the formula points at the wrong "
                            "cells or the wrong sheet.")
        if s.get("do") == "summary" and re.search(r"number of|count|how many|कितने|संख्या", instruction, re.I):
            if not any(str(v.get("agg", "")).upper() == "COUNT" for v in s.get("values") or [] if isinstance(v, dict)):
                return 'The request asks for a number (count) of rows: add {"agg": "COUNT"} to the summary values.'
    return None


def review(path: Path, sheet: str, instruction: str, steps: list[dict]) -> str | None:
    """Execution feedback: the model sees what its formulas produced. Returns the problem, or None when it looks right."""
    from . import llm
    t = read_table(path, sheet)
    problem = hard_checks(t, instruction, steps)
    if problem:
        return problem
    lines = ["Header: " + " | ".join(f"{L}={h}" for L, h in t.headers.items())]
    for r in list(range(t.first, min(t.last, t.first + 5) + 1)) + ([t.totals] if t.totals else []):
        lines.append(f"Row {r}: " + " | ".join(f"{col_name(j + 1)}={_show(v)}" for j, v in enumerate(t.rows[r - 1]) if v is not None))
    shown = {t.sheet}
    for s in steps:
        if s.get("do") == "set" and s.get("cell"):
            lines.append(f"{s['cell']} = {_show(t.value(str(s['cell']).upper()))}")
        at = str(s.get("at") or "")
        other = s.get("sheet") if s.get("do") in ("table", "set", "format") else None
        if "!" in at:
            other = at.rsplit("!", 1)[0].strip("'\"")
        if s.get("do") == "new_sheet":
            other = s.get("name")
        if other and other not in shown and other in sheet_names(path):  # the sheet the step wrote to, as computed
            shown.add(other)
            o = read_table(path, other)
            lines.append(f'Sheet "{other}":')
            for r, row in enumerate(o.rows[:10], 1):
                lines.append(f"  Row {r}: " + " | ".join(f"{col_name(j + 1)}={_show(v)}" for j, v in enumerate(row) if v is not None))
        elif s.get("do") == "summary" and not other:
            m = re.fullmatch(r"([A-Z]{1,3})(\d+)", (at or f"{col_name(t.width)}{t.header}").upper())
            if m:
                c0, r0 = col_num(m.group(1)), int(m.group(2))
                for r in range(r0, min(r0 + 8, len(t.rows) + 1)):
                    lines.append(f"Summary row {r}: " + " | ".join(_show(t.rows[r - 1][c]) for c in range(c0 - 1, min(c0 + 4, len(t.rows[r - 1])))))
    data = llm.chat_json([{"role": "system", "content": REVIEW},
                          {"role": "user", "content": f"Request: {instruction}\nSteps: {json.dumps(steps, ensure_ascii=False)[:2500]}\n"
                                                      "Computed sheet:\n" + "\n".join(lines)}], max_tokens=300)
    return None if data.get("ok", True) is True or not data.get("problem") else str(data["problem"])[:500]


def _results(path: Path, sheet: str, steps: list[dict]) -> list[str]:
    """A few computed values so the person sees the result, not just the formula."""
    t = read_table(path, sheet)
    out = []
    for s in steps:
        if s.get("do") in ("add_column", "fill"):
            L = next((k for k, v in t.headers.items() if v == s.get("header")), str(s.get("column") or "")).upper()
            if L:
                vals = [t.value(f"{L}{r}") for r in range(t.first, min(t.last, t.first + 2) + 1)]
                tot = t.value(f"{L}{t.totals}") if t.totals else None
                out.append(f"{t.headers.get(L, L)}: " + ", ".join(_show(v, s.get("number_format")) for v in vals)
                           + (" …" if t.last > t.first + 2 else "") + (f"; total {_show(tot, s.get('number_format'))}" if tot is not None else ""))
    return out


def _show(v, fmt=None) -> str:
    if isinstance(v, (int, float)):
        if fmt and "%" in str(fmt):
            return f"{v * 100:.1f}%"
        return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{v:,}"
    return "(empty)" if v is None else str(v)
