"""Power-BI-style dashboards over a workbook: KPI cards, charts, slicers and cross-filtering.

The model only plans the dashboard (which measures, which breakdowns, which chart types, which slicers). Every number
is computed here, in Python, from the sheet's own rows, and recomputed for every filter the person clicks, so a
dashboard is never "the model's guess" and always matches the data. Each measure and column in the plan is checked
before anything is drawn; a bad plan goes back to the model with what was wrong.
"""
import datetime as dt
import json
import re
import time
import uuid
from collections import defaultdict
from pathlib import Path

from . import sheets

AGGS = {"SUM", "AVERAGE", "COUNT", "COUNT_DISTINCT", "MIN", "MAX", "RATIO"}
CHART_TYPES = {"bar", "column", "line", "area", "pie", "donut", "table", "combo", "scatter", "funnel"}
GRAINS = {"day", "week", "month", "quarter", "year"}
MAX_ROWS = 200_000
MONEY = re.compile(r"revenue|sales|price|cost|profit|amount|value|income|expense|spend|budget|salary|fee|gst|tax|"
                   r"राशि|रकम|कीमत|लागत|मुनाफ़ा|बिक्री", re.I)


class DashboardError(ValueError):
    pass


# ------------------------------------------------------------------------------------------------ data
def _as_date(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", v.strip()):
        try:
            return dt.date.fromisoformat(v.strip()[:10])
        except ValueError:
            return None
    return None


def table(path: Path, sheet: str) -> dict:
    """The data rows of one sheet as typed columns (the totals row is left out)."""
    t = sheets.read_table(path, sheet)
    cols = [(L, h) for L, h in t.headers.items()]
    raw = [[t.rows[r - 1][sheets.col_num(L) - 1] if sheets.col_num(L) - 1 < len(t.rows[r - 1]) else None for L, _ in cols]
           for r in range(t.first, min(t.last, t.first + MAX_ROWS - 1) + 1)]
    raw = [r for r in raw if any(v not in (None, "") for v in r)]
    types = []
    for j in range(len(cols)):
        vals = [r[j] for r in raw if r[j] not in (None, "")]
        if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            types.append("number")
        elif vals and all(_as_date(v) for v in vals):
            types.append("date")
        else:
            types.append("text")
    rows = []
    for r in raw:
        rows.append([_as_date(v) if types[j] == "date" else v for j, v in enumerate(r)])
    return {"sheet": t.sheet, "columns": [h for _, h in cols], "types": types, "rows": rows}


def describe(path: Path) -> str:
    out = []
    for name in sheets.sheet_names(path):
        tb = table(path, name)
        if not tb["rows"]:
            continue
        parts = []
        for j, (c, ty) in enumerate(zip(tb["columns"], tb["types"])):
            vals = [r[j] for r in tb["rows"] if r[j] not in (None, "")]
            if ty == "number" and vals:
                lo, hi = min(vals), max(vals)
                parts.append(f'"{c}" number {lo:,.2f}..{hi:,.2f}' if hi <= 1 else f'"{c}" number {lo:,.0f}..{hi:,.0f}')
            elif ty == "date" and vals:
                parts.append(f'"{c}" date {min(vals)}..{max(vals)}')
            else:
                distinct = list(dict.fromkeys(str(v) for v in vals))
                parts.append(f'"{c}" text, {len(distinct)} different (e.g. {", ".join(distinct[:3])})')
        out.append(f'Sheet "{name}", {len(tb["rows"])} rows: ' + "; ".join(parts))
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ measures
def _col(tb: dict, name: str) -> int:
    names = [c.lower() for c in tb["columns"]]
    n = str(name or "").strip().lower()
    if n in names:
        return names.index(n)
    hits = [i for i, c in enumerate(names) if n and (n in c or c in n)]
    if len(hits) == 1:
        return hits[0]
    raise DashboardError(f'there is no column "{name}" in sheet "{tb["sheet"]}". Columns: {", ".join(tb["columns"])}')


def check_measure(tb: dict, m: dict):
    agg = str(m.get("agg", "SUM")).upper()
    if agg not in AGGS:
        raise DashboardError(f'measure "{m.get("label")}": agg must be one of {", ".join(sorted(AGGS))}, not {agg}')
    if agg == "RATIO":
        check_measure(tb, m.get("num") or {})
        check_measure(tb, m.get("den") or {})
        return
    if agg == "COUNT" and not m.get("column"):
        return
    j = _col(tb, m.get("column"))
    if agg in ("SUM", "AVERAGE", "MIN", "MAX") and tb["types"][j] != "number":
        raise DashboardError(f'measure "{m.get("label")}": {agg} needs a number column; "{tb["columns"][j]}" is {tb["types"][j]}')


def value(tb: dict, rows: list, m: dict):
    agg = str(m.get("agg", "SUM")).upper()
    if agg == "RATIO":
        a, b = value(tb, rows, m["num"]), value(tb, rows, m["den"])
        return (a / b) if (a is not None and b) else None
    if agg == "COUNT" and not m.get("column"):
        return len(rows)
    j = _col(tb, m.get("column"))
    vals = [r[j] for r in rows if r[j] not in (None, "")]
    if agg == "COUNT":
        return len(vals)
    if agg == "COUNT_DISTINCT":
        return len({str(v) for v in vals})
    nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not nums:
        return None
    return {"SUM": sum(nums), "AVERAGE": sum(nums) / len(nums), "MIN": min(nums), "MAX": max(nums)}[agg]


def kind(m: dict) -> str:
    """How a measure reads: percent, money or a plain number."""
    if m.get("format") in ("percent", "money", "number"):
        return m["format"]
    if str(m.get("agg", "")).upper() == "RATIO":
        label = str(m.get("label", ""))
        if re.search(r"margin|share|rate|percent|%|conversion|growth|मार्जिन|हिस्सा|प्रतिशत", label, re.I):
            return "percent"
        if re.search(r"\bper\b|average|avg|प्रति|औसत", label, re.I):
            return "money" if MONEY.search(f'{label} {(m.get("num") or {}).get("column", "")}') else "number"
        return "percent"
    if str(m.get("agg", "")).upper() in ("COUNT", "COUNT_DISTINCT"):
        return "number"
    return "money" if MONEY.search(f'{m.get("column", "")} {m.get("label", "")}') else "number"


def show(v, k: str, indian: bool = False) -> str:
    if v is None:
        return "–"
    if k == "percent":
        return f"{v * 100:.1f}%"
    if abs(v) >= 10_000_000 and indian:
        return f"{v / 10_000_000:.2f} Cr"
    if abs(v) >= 100_000 and indian:
        return f"{v / 100_000:.2f} L"
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if abs(v) >= 10_000:
        return f"{v / 1000:.1f}K"
    return f"{v:,.0f}" if abs(v - round(v)) < 1e-9 or abs(v) >= 100 else f"{v:,.2f}"


# ------------------------------------------------------------------------------------------------ grouping
def _period(d: dt.date, grain: str) -> str:
    if grain == "day":
        return d.isoformat()
    if grain == "week":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    if grain == "quarter":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    if grain == "year":
        return str(d.year)
    return f"{d.year}-{d.month:02d}"


def _key(tb: dict, j: int, v, grain: str | None):
    if tb["types"][j] == "date" and v is not None:
        return _period(v, grain or "month")
    return "(blank)" if v in (None, "") else str(v)


def apply_filters(tb: dict, filters: dict) -> list:
    rows = tb["rows"]
    for key, wanted in (filters or {}).items():
        if not wanted:
            continue
        col, _, grain = str(key).partition("|")
        j = _col(tb, col)
        want = {str(w) for w in wanted}
        rows = [r for r in rows if _key(tb, j, r[j], grain or None) in want]
    return rows


# ------------------------------------------------------------------------------------------------ render
def check(spec: dict, path: Path) -> dict:
    """Every sheet, column and measure in the plan must exist and make sense. Raises DashboardError with the fix."""
    names = sheets.sheet_names(path)
    src = spec.get("source") or names[0]
    if src not in names:
        raise DashboardError(f'there is no sheet "{src}". Sheets: {", ".join(names)}')
    tb = table(path, src)
    if not tb["rows"]:
        raise DashboardError(f'sheet "{src}" has no data rows')
    if not spec.get("kpis") and not spec.get("charts"):
        raise DashboardError("the plan has no KPIs and no charts")
    for m in spec.get("kpis") or []:
        check_measure(tb, m)
    for c in spec.get("charts") or []:
        if str(c.get("type", "bar")).lower() not in CHART_TYPES:
            raise DashboardError(f'chart "{c.get("title")}": type must be one of {", ".join(sorted(CHART_TYPES))}')
        if c.get("type") != "scatter":
            _col(tb, c.get("dimension"))
        if c.get("grain") and c["grain"] not in GRAINS:
            raise DashboardError(f'chart "{c.get("title")}": grain must be day, week, month, quarter or year')
        if not c.get("measures"):
            raise DashboardError(f'chart "{c.get("title")}" has no measures')
        for m in c["measures"]:
            check_measure(tb, m)
    for s in spec.get("slicers") or []:
        j = _col(tb, s)
        if tb["types"][j] == "number":
            raise DashboardError(f'slicer "{s}" is a number column; slicers are for text columns like Region or Product')
    return tb


def render(path: Path, spec: dict, filters: dict | None = None, indian: bool = False) -> dict:
    """KPIs, chart series and slicer values for the current filters. All numbers computed here."""
    tb = check(spec, path)
    rows = apply_filters(tb, filters or {})
    out = {"title": spec.get("title") or "Dashboard", "source": tb["sheet"], "rows": len(rows), "total_rows": len(tb["rows"]),
           "filters": filters or {}, "kpis": [], "charts": [], "slicers": []}
    for m in spec.get("kpis") or []:
        v = value(tb, rows, m)
        out["kpis"].append({"label": m.get("label") or m.get("column"), "value": v, "display": show(v, kind(m), indian),
                            "kind": kind(m)})
    for i, c in enumerate(spec.get("charts") or []):
        ctype = str(c.get("type", "bar")).lower()
        measures = c["measures"][:3]
        if ctype == "scatter":
            pts = [[value(tb, [r], measures[0]), value(tb, [r], measures[-1])] for r in rows]
            out["charts"].append({"id": i, "type": ctype, "title": c.get("title"), "points": [p for p in pts if None not in p],
                                  "measures": [{"label": m.get("label"), "kind": kind(m)} for m in measures]})
            continue
        j = _col(tb, c.get("dimension"))
        grain = c.get("grain") or ("month" if tb["types"][j] == "date" else None)
        groups = defaultdict(list)
        for r in rows:
            groups[_key(tb, j, r[j], grain)].append(r)
        cats = list(groups)
        series = [[value(tb, groups[k], m) for k in cats] for m in measures]
        if tb["types"][j] == "date":
            order = sorted(range(len(cats)), key=lambda x: cats[x])
        else:
            sort = str(c.get("sort") or ("desc" if ctype in ("bar", "column", "pie", "donut", "funnel", "table") else "none")).lower()
            order = list(range(len(cats)))
            if sort in ("desc", "asc"):
                order.sort(key=lambda x: (series[0][x] is None, series[0][x] or 0), reverse=(sort == "desc"))
                if sort == "desc":
                    order = [x for x in order if series[0][x] is not None] + [x for x in order if series[0][x] is None]
        top = int(c.get("top") or 0)
        if top and len(order) > top:
            order = order[:top]
        out["charts"].append({
            "id": i, "type": ctype, "title": c.get("title") or f"{measures[0].get('label')} by {tb['columns'][j]}",
            "dimension": tb["columns"][j], "filter_key": tb["columns"][j] + (f"|{grain}" if tb["types"][j] == "date" else ""),
            "categories": [cats[x] for x in order],
            "series": [{"name": m.get("label") or m.get("column"), "kind": kind(m), "data": [s[x] for x in order]}
                       for m, s in zip(measures, series)]})
    for s in spec.get("slicers") or []:
        j = _col(tb, s)
        vals = sorted({_key(tb, j, r[j], None) for r in tb["rows"]})
        out["slicers"].append({"column": tb["columns"][j], "values": vals[:200]})
    return out


# ------------------------------------------------------------------------------------------------ planning
RULES = """You design a Power-BI-style dashboard for the user's spreadsheet. You choose what to show; Vyum computes every
number from the data. Return JSON only:
{{"approach": "2 short sentences: what the person wants to see and how the dashboard shows it",
 "title": "short title", "source": "the sheet with the data rows",
 "kpis": [{{"label": "Total revenue", "agg": "SUM", "column": "Revenue"}},
          {{"label": "Orders", "agg": "COUNT"}},
          {{"label": "Profit margin", "agg": "RATIO", "num": {{"agg": "SUM", "column": "Profit"}}, "den": {{"agg": "SUM", "column": "Revenue"}}}}],
 "charts": [{{"type": "column", "title": "Revenue by region", "dimension": "Region", "measures": [{{"label": "Revenue", "agg": "SUM", "column": "Revenue"}}]}},
            {{"type": "line", "title": "Revenue by month", "dimension": "Date", "grain": "month", "measures": [...]}},
            {{"type": "donut", "title": "Share of revenue by product", "dimension": "Product", "measures": [...]}},
            {{"type": "bar", "title": "Top 5 products by profit", "dimension": "Product", "measures": [...], "sort": "desc", "top": 5}},
            {{"type": "combo", "title": "Revenue and margin by region", "dimension": "Region", "measures": [{{...revenue...}}, {{...margin RATIO...}}]}},
            {{"type": "table", "title": "Summary by product", "dimension": "Product", "measures": [..., ...]}}],
 "slicers": ["Region", "Product"]}}
Rules:
- Use only the sheets and columns listed below, spelled exactly. Measures: SUM, AVERAGE, MIN, MAX (number columns),
  COUNT (rows; no column needed), COUNT_DISTINCT (any column), RATIO (num / den). A ratio named margin/share/rate shows
  as a percent; "revenue per unit" or "average order value" shows as money. Add "format": "percent" | "money" | "number"
  to any measure to be explicit.
- Chart types: column, bar (horizontal, good for rankings), line or area (a date dimension with "grain": day, week,
  month, quarter or year), pie or donut (shares, at most 8 slices: add "top"), combo (two measures: bars + a line),
  table, funnel, scatter (two number measures, no dimension).
- 3 to 5 KPIs and 4 to 6 charts unless the person asks otherwise; every chart answers a different question.
- Slicers are text columns people filter by (region, product, category, rep), 1 to 3 of them.
- Never invent numbers, targets or columns."""


def asked_for(path: Path, spec: dict, instruction: str) -> list[str]:
    """Measures the request names (a column of the data, "margin", "orders") that no KPI shows."""
    tb = table(path, spec.get("source") or sheets.sheet_names(path)[0])
    kpis = spec.get("kpis") or []
    covered = {str(k.get("column") or "").lower() for k in kpis if str(k.get("agg", "")).upper() != "RATIO"}
    has_ratio = any(str(k.get("agg", "")).upper() == "RATIO" for k in kpis)
    has_count = any(str(k.get("agg", "")).upper() in ("COUNT", "COUNT_DISTINCT") for k in kpis)
    missing = []
    for c, ty in zip(tb["columns"], tb["types"]):  # a column the request names needs a card of its own
        if has_ratio and re.search(r"margin|share|percent|rate|%", c, re.I):
            continue  # a ratio card already shows it
        if ty == "number" and re.search(rf"\b{re.escape(c.lower())}\b", instruction.lower()) and c.lower() not in covered:
            missing.append(c)
    if re.search(r"\bmargin|मार्जिन", instruction, re.I) and not has_ratio:
        missing.append("margin")
    if re.search(r"\b(orders|number of|count)\b|ऑर्डर|संख्या", instruction, re.I) and not has_count:
        missing.append("number of orders")
    return missing


def plan(path: Path, instruction: str, feedback: str = "") -> dict:
    from .office import _plan
    user = f"Data:\n{describe(path)}\n\nRequest: {instruction}"
    if feedback:
        user += f"\n\nYour previous dashboard plan was wrong:\n{feedback}\nReturn the corrected plan."
    return _plan(RULES.format(), user)


def dashboard_events(it: dict, instruction: str):
    """Plan -> check -> compute, with the plan shown as a to-do list. Saves the dashboard with the workbook."""
    from .office import _update, get
    path = Path(it["path"])
    t0 = time.time()
    items = ["Read the data", "Plan the dashboard", "Check every column and measure", "Compute KPIs and charts",
             "Check every visual has data"]
    yield {"type": "plan", "attempt": 1, "items": items}
    yield {"type": "plan_step", "index": 0, "state": "running"}
    data_map = describe(path)
    yield {"type": "plan_step", "index": 0, "state": "done", "note": data_map.split("\n")[0][:160]}
    feedback, spec, rendered = "", None, None
    for attempt in range(3):
        if attempt:
            yield {"type": "plan", "attempt": attempt + 1, "items": items}
            yield {"type": "plan_step", "index": 0, "state": "done"}
        yield {"type": "plan_step", "index": 1, "state": "running"}
        try:
            spec = plan(path, instruction, feedback)
        except Exception as e:
            feedback = f"Your reply was not valid JSON ({e}). Return only the JSON object."
            yield {"type": "plan_step", "index": 1, "state": "failed", "note": "unreadable plan"}
            continue
        if spec.get("approach"):
            yield {"type": "thinking", "text": str(spec["approach"]) + "\n\n"}
        yield {"type": "plan_step", "index": 1, "state": "done",
               "note": f'{len(spec.get("kpis") or [])} KPIs, {len(spec.get("charts") or [])} charts, slicers: '
                       f'{", ".join(spec.get("slicers") or []) or "none"}'}
        yield {"type": "plan_step", "index": 2, "state": "running"}
        try:
            check(spec, path)
        except DashboardError as e:
            feedback = str(e)
            yield {"type": "plan_step", "index": 2, "state": "failed", "note": str(e)[:300]}
            yield {"type": "thinking", "text": f"Problem: {e}\n\n"}
            continue
        yield {"type": "plan_step", "index": 2, "state": "done"}
        yield {"type": "plan_step", "index": 3, "state": "running"}
        rendered = render(path, spec)
        yield {"type": "plan_step", "index": 3, "state": "done"}
        yield {"type": "plan_step", "index": 4, "state": "running"}
        empty = [c["title"] for c in rendered["charts"] if not any(v for s in c.get("series", []) for v in s["data"])
                 and not c.get("points")]
        if empty:
            feedback = f"These charts have no data: {', '.join(empty)}. Use columns with values."
            yield {"type": "plan_step", "index": 4, "state": "failed", "note": feedback}
            continue
        missing = asked_for(path, spec, instruction)
        if missing and attempt < 2:
            feedback = (f"The request asks for {', '.join(missing)} but the dashboard does not show "
                        f"{'it' if len(missing) == 1 else 'them'}: add a KPI for each.")
            yield {"type": "plan_step", "index": 4, "state": "failed", "note": feedback}
            yield {"type": "thinking", "text": feedback + "\n\n"}
            continue
        yield {"type": "plan_step", "index": 4, "state": "done"}
        break
    else:
        raise DashboardError("I could not build a dashboard from this data. Try naming the columns to show. "
                             f"(Details: {feedback})")
    spec["id"] = uuid.uuid4().hex[:10]
    spec["created_at"] = time.time()
    fresh = get(it["id"])
    _update(it["id"], dashboards=[spec] + [d for d in (fresh.get("dashboards") or []) if d.get("title") != spec.get("title")][:9])
    k = rendered["kpis"]
    yield {"type": "result", "result": {"kind": "dashboard", "dashboard": spec["id"], "title": spec.get("title"),
                                        "summary": f'Built the dashboard “{spec.get("title")}”: '
                                                   f'{len(rendered["kpis"])} KPIs, {len(rendered["charts"])} charts.',
                                        "changes": [f'{x["label"]}: {x["display"]}' for x in k],
                                        "timings": {"total_s": round(time.time() - t0, 1)}}}
