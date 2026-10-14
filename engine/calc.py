"""Vyum's calculator: exact arithmetic, algebra, calculus, statistics and finance, so the model never does maths in its head.

The model writes expressions; this module computes them. Expressions are read by our own parser (Python's ast, with
a whitelist of nodes and functions) and built into SymPy objects directly, so model or document text is never run as
code. SymPy (BSD-3) does the maths.

Language (what the model is told, see GUIDE):
  numbers      1,00,000  2.5e6  18%  (₹, $, Rs, commas and spaces between digit groups are ignored)
  operators    + - * / ^ (or **)  ( )  mod(a, b)
  equations    "x^2 - 5x + 6 = 0", several separated by ";" -> solved for their unknowns
  commands     solve(eq, x) simplify(e) expand(e) factor(e) diff(e, x) integrate(e, x[, a, b]) limit(e, x, a) sum(e, k, a, b)
  functions    sqrt cbrt root(x, n) abs exp ln log(x[, base]) (log = base 10) sin cos tan asin acos atan sind cosd tand
               floor ceil round(x[, digits]) min max factorial nCr nPr gcd lcm
  statistics   total(...) mean(...) median(...) stdev(...) var(...) product(...)
  finance      emi(principal, annual_rate_pct, months)  compound(principal, annual_rate_pct, years[, times_per_year])
               simple_interest(principal, annual_rate_pct, years)  cagr(start, end, years)  pct_change(old, new)
               pct_of(part, whole)  gst(amount, rate_pct)  with_gst(amount, rate_pct)  npv(rate_pct, cf1, cf2, ...)
               irr(cf0, cf1, ...)
"""
import ast
import re
import threading
from decimal import Decimal

import sympy as sp

MAX_LEN = 600
TIMEOUT_S = 8

GUIDE = """Calculator language (one expression per step):
- numbers: 1,00,000  2.5e6  18%  (₹ $ Rs and commas are fine); operators + - * / ^ ( ); mod(a, b)
- an equation uses "=", e.g. "2*x + 3 = 11"; a system uses ";", e.g. "x + y = 10; x - y = 2"
- solve(eq, x) simplify(e) expand(e) factor(e) diff(e, x) integrate(e, x) integrate(e, x, 0, 1) limit(e, x, 0) sum(k^2, k, 1, 10)
- sqrt cbrt root(x, n) abs exp ln log(x) (base 10) log(x, base) sin cos tan (radians) sind cosd tand (degrees)
  floor ceil round(x, 2) min max factorial nCr(n, r) nPr(n, r) gcd lcm
- total(a, b, ...) mean(...) median(...) stdev(...) (sample) var(...) product(...)
- emi(principal, annual_rate, months)  compound(principal, annual_rate, years, times_per_year)
  simple_interest(principal, annual_rate, years)  cagr(start, end, years)  pct_change(old, new)  pct_of(part, whole)
  gst(amount, rate) = the tax  with_gst(amount, rate) = amount + tax  npv(rate, cf1, cf2, ...)  irr(cf0, cf1, ...)
- Rates: write them as the user does, e.g. 8% (8% and 0.08 and 8 all mean eight percent in these functions).
  pct_change, pct_of, cagr and irr return percent values (25 means 25%)."""


class CalcError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------- functions
def _n(x):
    return sp.nsimplify(x) if isinstance(x, float) else sp.sympify(x) if not isinstance(x, sp.Basic) else x


def _flat(args):
    out = []
    for a in args:
        out.extend(_flat(a) if isinstance(a, (list, tuple)) else [a])
    return out


def _median(*xs):
    v = sorted(_flat(xs), key=lambda z: float(z))
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


def _var(*xs):
    v = _flat(xs)
    if len(v) < 2:
        raise CalcError("stdev/var need at least two numbers")
    mean = sp.Add(*v) / len(v)
    return sp.Add(*[(x - mean) ** 2 for x in v]) / (len(v) - 1)


def _rate(r):
    """A rate written any of the usual ways means the same: 8%, 0.08 and 8 are all eight percent (rates above 100% are
    taken as written in percent, which is what people mean by "8")."""
    r = sp.sympify(r)
    return r / 100 if r.is_number and abs(r) > 1 else r


def _emi(p, rate, months):
    r = _rate(rate) / 12
    return p / months if r == 0 else p * r * (1 + r) ** months / ((1 + r) ** months - 1)


def _irr(*cfs):
    cfs = [sp.Float(c) for c in _flat(cfs)]
    r = sp.Symbol("r")
    f = sp.Add(*[c / (1 + r) ** i for i, c in enumerate(cfs)])
    for guess in (0.1, 0.01, 0.3, -0.5, 1.0):
        try:
            root = sp.nsolve(f, r, guess)
            return root * 100
        except Exception:
            continue
    raise CalcError("irr: no rate makes the cash flows add up to zero")


def _round(x, digits=0):
    q = Decimal(1).scaleb(-int(digits))
    return sp.Rational(str(Decimal(str(sp.N(x, 30))).quantize(q, rounding="ROUND_HALF_UP")))


def _sum_cmd(e, k, a, b):
    return sp.summation(e, (k, a, b))


def _integrate(e, x, a=None, b=None):
    return sp.integrate(e, (x, a, b)) if a is not None else sp.integrate(e, x)


def _solve(e, *xs):
    eqs = e if isinstance(e, (list, tuple)) else [e]
    return ("solution", sp.solve(eqs, list(xs) if xs else None, dict=True))


FUNCS = {
    "sqrt": sp.sqrt, "cbrt": sp.cbrt, "root": lambda x, n: sp.root(x, n), "abs": sp.Abs, "exp": sp.exp,
    "ln": sp.log, "log": lambda x, b=10: sp.log(x, b), "log10": lambda x: sp.log(x, 10), "log2": lambda x: sp.log(x, 2),
    "sin": sp.sin, "cos": sp.cos, "tan": sp.tan, "asin": sp.asin, "acos": sp.acos, "atan": sp.atan,
    "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh,
    "sind": lambda x: sp.sin(x * sp.pi / 180), "cosd": lambda x: sp.cos(x * sp.pi / 180), "tand": lambda x: sp.tan(x * sp.pi / 180),
    "floor": sp.floor, "ceil": sp.ceiling, "round": _round, "min": lambda *a: sp.Min(*_flat(a)), "max": lambda *a: sp.Max(*_flat(a)),
    "factorial": sp.factorial, "nCr": sp.binomial, "comb": sp.binomial, "nPr": lambda n, r: sp.factorial(n) / sp.factorial(n - r),
    "gcd": sp.gcd, "lcm": sp.lcm, "mod": sp.Mod,
    "total": lambda *a: sp.Add(*_flat(a)), "mean": lambda *a: sp.Add(*_flat(a)) / len(_flat(a)),
    "avg": lambda *a: sp.Add(*_flat(a)) / len(_flat(a)), "average": lambda *a: sp.Add(*_flat(a)) / len(_flat(a)),
    "median": _median, "var": _var, "stdev": lambda *a: sp.sqrt(_var(*a)), "product": lambda *a: sp.Mul(*_flat(a)),
    "emi": _emi, "pmt": _emi,
    "compound": lambda p, r, y, n=1: p * (1 + _rate(r) / n) ** (n * y),
    "simple_interest": lambda p, r, y: p * _rate(r) * y,
    "cagr": lambda s, e, y: ((e / s) ** (sp.Integer(1) / y) - 1) * 100,
    "pct_change": lambda old, new: (new - old) / old * 100, "pct_of": lambda part, whole: part / whole * 100,
    "gst": lambda a, r: a * _rate(r), "with_gst": lambda a, r: a * (1 + _rate(r)),
    "npv": lambda r, *cf: sp.Add(*[c / (1 + _rate(r)) ** (i + 1) for i, c in enumerate(_flat(cf))]),
    "irr": _irr,
    # commands
    "simplify": sp.simplify, "expand": sp.expand, "factor": sp.factor, "diff": lambda e, *x: sp.diff(e, *x),
    "derivative": lambda e, *x: sp.diff(e, *x), "integrate": _integrate, "limit": lambda e, x, a: sp.limit(e, x, a),
    "sum": _sum_cmd, "solve": _solve,
}
CONSTS = {"pi": sp.pi, "π": sp.pi, "e": sp.E, "inf": sp.oo, "infinity": sp.oo}


# ---------------------------------------------------------------------------------------------- parsing
_GROUPED = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{2,3})+(?:\.\d+)?(?![\w])")


def normalize(text: str) -> str:
    t = text.strip().replace("×", "*").replace("÷", "/").replace("−", "-").replace("–", "-").replace("^", "**")
    t = re.sub(r"(₹|\$|€|£|\bRs\.?|\bINR\b|\bUSD\b|\bEUR\b)\s*", "", t)
    t = _GROUPED.sub(lambda m: m.group(0).replace(",", ""), t)                      # 1,00,000 -> 100000
    t = re.sub(r"(\d)\s+(?=\d{3}\b)", r"\1", t)                                      # 20 000 000 -> 20000000
    t = re.sub(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%(?!\s*[\w(])", r"(\1/100)", t)                   # 18% -> (18/100); "10 % 3" stays mod
    t = re.sub(r"(?<![\w.])(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![\d.]|[eE][+-]?\d)\s*(?=[A-Za-z_(])", r"\1*", t)  # 5x -> 5*x
    t = re.sub(r"\)\s*(?=[A-Za-z_(\d])", ")*", t)                                     # (x+1)(x-1) -> (x+1)*(x-1)
    return t


class _Builder(ast.NodeVisitor):
    def __init__(self):
        self.symbols: dict[str, sp.Symbol] = {}

    def generic_visit(self, node):
        raise CalcError(f"not allowed in a calculation: {type(node).__name__}")

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_Constant(self, node):
        v = node.value
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise CalcError("only numbers are allowed")
        return sp.Integer(v) if isinstance(v, int) else sp.Rational(repr(v))

    def visit_Name(self, node):
        if node.id in CONSTS:
            return CONSTS[node.id]
        if node.id in FUNCS:
            raise CalcError(f"{node.id} needs brackets, e.g. {node.id}(...)")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,30}", node.id):
            raise CalcError(f"bad name {node.id}")
        return self.symbols.setdefault(node.id, sp.Symbol(node.id))

    def visit_UnaryOp(self, node):
        v = self.visit(node.operand)
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return v
        raise CalcError("only + and - signs are allowed")

    def visit_BinOp(self, node):
        a, b = self.visit(node.left), self.visit(node.right)
        op = type(node.op)
        if op is ast.Add:
            return a + b
        if op is ast.Sub:
            return a - b
        if op is ast.Mult:
            return a * b
        if op is ast.Div:
            if b == 0:
                raise CalcError("division by zero")
            return a / b
        if op is ast.Pow:
            if b.is_number and abs(b) > 10000:
                raise CalcError("exponent too large")
            return a ** b
        if op is ast.Mod:
            return sp.Mod(a, b)
        if op is ast.FloorDiv:
            return sp.floor(a / b)
        raise CalcError("operator not allowed")

    def visit_Tuple(self, node):
        return [self.visit(x) for x in node.elts]

    visit_List = visit_Tuple

    def visit_Compare(self, node):
        if len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            return sp.Eq(self.visit(node.left), self.visit(node.comparators[0]))
        raise CalcError("only = is allowed between two sides")

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS or node.keywords:
            raise CalcError(f"unknown function {getattr(node.func, 'id', '?')}")
        args = [self.visit(a) for a in node.args]
        if node.func.id == "factorial" and args and args[0].is_number and args[0] > 1000:
            raise CalcError("factorial too large")
        return FUNCS[node.func.id](*args)


def parse(text: str):
    """Text -> (SymPy object or list of equations, symbols). Never evaluates text as code."""
    if len(text) > MAX_LEN:
        raise CalcError("expression too long")
    parts = [p for p in re.split(r";|\n", text) if p.strip()]
    b = _Builder()
    out = []
    for part in parts:
        t = normalize(part)
        t = re.sub(r"(?<![<>=!])=(?!=)", "==", t)  # an equation's "=" becomes "==" for the parser
        try:
            tree = ast.parse(t, mode="eval")
        except SyntaxError as e:
            raise CalcError(f"could not read '{part.strip()}': {e.msg}") from None
        out.append(b.visit(tree))
    return (out[0] if len(out) == 1 else out), b.symbols


# ---------------------------------------------------------------------------------------------- results
def fmt_number(x, digits: int = 10, indian: bool = False) -> str:
    """12345.6789 -> "12,345.6789"; exact integers keep every digit; Indian grouping on request (12,34,567)."""
    x = sp.nsimplify(x) if isinstance(x, float) else x
    if x.is_Integer:
        s, sign = str(abs(int(x))), "-" if x < 0 else ""
        return sign + (_indian(s) if indian else f"{int(s):,}")
    if not x.is_real:
        return str(sp.N(x, digits))
    v = Decimal(str(sp.N(x, digits)))
    if v == v.to_integral() and abs(v) < Decimal(10) ** digits:
        return fmt_number(sp.Integer(int(v)), digits, indian)
    s = f"{v:f}".rstrip("0").rstrip(".") if abs(v) >= Decimal("1e-6") else f"{v:.{digits}g}"
    whole, _, frac = s.lstrip("-").partition(".")
    whole = _indian(whole) if indian else f"{int(whole):,}"
    return ("-" if v < 0 else "") + whole + (f".{frac}" if frac else "")


def _indian(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    return re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head) + "," + tail


def _describe(value, indian=False) -> str:
    if isinstance(value, tuple) and value and value[0] == "solution":
        sols = value[1]
        if not sols:
            return "no solution"
        return " or ".join(", ".join(f"{k} = {_describe(v, indian)}" for k, v in s.items()) for s in sols)
    if isinstance(value, list):
        return ", ".join(_describe(v, indian) for v in value)
    if isinstance(value, sp.Basic) and value.is_number:
        exact = str(value).replace("**", "^")
        approx = fmt_number(value, indian=indian)
        if value.is_Float or exact.replace("-", "").isdigit() or exact == approx.replace(",", "") or len(exact) >= 40:
            return approx
        return f"{approx} (exact: {exact})"
    return re.sub(r"\blog\(", "ln(", str(value).replace("**", "^"))  # SymPy's log is the natural log


def _compute(text: str):
    expr, symbols = parse(text)
    if isinstance(expr, list) and all(isinstance(e, sp.Equality) for e in expr):
        return ("solution", sp.solve(expr, list(symbols.values()), dict=True))
    if isinstance(expr, sp.Equality):
        unknowns = sorted(expr.free_symbols, key=str)
        if not unknowns:
            return sp.true if sp.simplify(expr.lhs - expr.rhs) == 0 else sp.false
        sols = sp.solve(expr, unknowns[0] if len(unknowns) == 1 else unknowns, dict=True)
        return ("solution", sols)
    return sp.nsimplify(expr) if isinstance(expr, float) else expr


def evaluate(text: str, indian: bool = False) -> dict:
    """{"expr", "result" (text), "value" (float or None), "error"}. Hard sums are cut off after TIMEOUT_S seconds."""
    box: dict = {}

    def run():
        try:
            box["v"] = _compute(text)
        except CalcError as e:
            box["e"] = str(e)
        except (ZeroDivisionError, ValueError, TypeError, NotImplementedError, AttributeError) as e:
            box["e"] = f"{type(e).__name__}: {e}"
        except Exception as e:  # SymPy raises many kinds; the caller only needs to know it failed
            box["e"] = f"{type(e).__name__}: {e}"
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(TIMEOUT_S)
    if th.is_alive():
        return {"expr": text, "result": None, "value": None, "error": "took too long"}
    if "e" in box:
        return {"expr": text, "result": None, "value": None, "error": box["e"]}
    v = box["v"]
    if v is sp.zoo or v is sp.nan:
        return {"expr": text, "result": None, "value": None, "error": "undefined"}
    num = None
    if isinstance(v, sp.Basic) and v.is_number and v.is_real:
        num = float(sp.N(v, 17))
    return {"expr": text, "result": _describe(v, indian), "value": num, "error": None}


# ---------------------------------------------------------------------------------------------- the model's side
_MATHY = re.compile(
    r"\b(calculat\w*|compute|solve|equation|simplify|factori[sz]e|expand|derivative|differentiate|integra\w*|limit|"
    r"sum|total|average|mean|median|percent\w*|interest|emi|loan|mortgage|cagr|growth|profit|margin|discount|gst|tax|"
    r"vat|ratio|split|per (day|month|year|unit|person)|how (much|many)|times|divided|multipl\w*|square root|"
    r"kitna|kitne|kitni|jod|hisab|byaj|kul|"
    r"कितना|कितने|कितनी|जोड़|योग|कुल|प्रतिशत|ब्याज|हिसाब|औसत|गुणा|भाग)\b|[\d)]\s*[-+*/^×÷%]\s*[\d(]|\d\s*%|[=^]", re.I)


def needs_math(text: str) -> bool:
    """Cheap first check: is there arithmetic or a maths word next to a number?"""
    return bool(re.search(r"\d", text) and _MATHY.search(text)) or bool(
        re.search(r"\b(solve|simplify|factori[sz]e|derivative|differentiate|integrate)\b", text, re.I))


PLAN = """You prepare calculations for an answer. A calculator computes them exactly; you never compute anything yourself.
Return JSON only: {{"steps": [{{"name": "short_name", "label": "what it is, in {language}", "expr": "..."}}]}}
- One step per quantity. A later step may use an earlier step's name as a number (e.g. "monthly_payment * 60").
- Use the numbers exactly as the user or the sources give them; write rates with % (8%).
- If nothing needs calculating (the answer is just a fact, a word or an explanation), return {{"steps": []}}.
{guide}"""


def evaluate_steps(steps: list[dict], indian: bool = False) -> list[dict]:
    """Evaluates named steps in order; a step can use the names of earlier steps."""
    env: dict[str, sp.Basic] = {}
    out = []
    for s in steps[:12]:
        expr = str(s.get("expr") or "").strip()
        name = re.sub(r"\W", "_", str(s.get("name") or "")).strip("_")[:30]
        text = expr
        for k in sorted(env, key=len, reverse=True):  # earlier results go in as exact numbers
            text = re.sub(rf"(?<![\w.]){re.escape(k)}(?![\w(])", f"({env[k]})", text)
        r = evaluate(text, indian=indian)
        r["expr"], r["label"], r["name"] = expr, str(s.get("label") or name or expr)[:120], name
        if r["error"] is None and name and r["value"] is not None:
            env[name] = sp.Rational(repr(r["value"]))  # 17 significant digits carried into later steps
        out.append(r)
    return out


def plan_and_compute(question: str, context: str = "", indian: bool = False, language: str = "English") -> list[dict]:
    """The model writes the calculations; the calculator computes them. One retry with the calculator's error messages."""
    from . import llm
    system = PLAN.format(guide=GUIDE, language=language)
    user = (f"Sources:\n{context[:6000]}\n\n" if context else "") + f"Question: {question}"
    steps: list[dict] = []
    for attempt in range(2):
        data = llm.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=700)
        plan = [s for s in (data.get("steps") or []) if isinstance(s, dict) and s.get("expr")]
        if not plan:
            return steps
        steps = evaluate_steps(plan, indian)
        bad = [s for s in steps if s["error"]]
        if not bad:
            return steps
        user += "\n\nThese steps failed in the calculator; fix them:\n" + "\n".join(f"- {s['expr']}: {s['error']}" for s in bad)
    return [s for s in steps if not s["error"]] or steps


def results_block(steps: list[dict]) -> str:
    ok = [s for s in steps if not s["error"]]
    if not ok:
        return ""
    return ("Calculator results (exact, computed by Vyum's calculator; use these numbers as they are, do not recompute, "
            "and round only for display):\n" + "\n".join(f"- {s['label']}: {s['expr']} = {s['result']}" for s in ok))
