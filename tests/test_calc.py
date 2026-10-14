"""The calculator the model uses instead of doing arithmetic itself."""
import pytest

from engine.calc import evaluate, evaluate_steps, needs_math


def r(text, indian=False):
    out = evaluate(text, indian)
    assert out["error"] is None, out
    return out["result"]


@pytest.mark.parametrize("text,want", [
    ("2+2", "4"), ("1,00,000 * 18%", "18,000"), ("₹2,500 * 3", "7,500"), ("20 000 000 * 4%", "800,000"),
    ("x^2 - 5x + 6 = 0", "x = 2 or x = 3"), ("x + y = 10; x - y = 2", "x = 6, y = 4"), ("(1+2)(3+4)", "21"),
    ("2.5e6 / 4", "625,000"), ("10 % 3", "1"), ("log(1000)", "3"), ("ln(e^2)", "2"), ("sind(30)", "0.5 (exact: 1/2)"),
    ("diff(x^3 + 2x, x)", "3*x^2 + 2"), ("integrate(x^2, x, 0, 3)", "9"), ("sum(k^2, k, 1, 10)", "385"),
    ("nCr(10, 3)", "120"), ("factorial(20)", "2,432,902,008,176,640,000"), ("round(2.675, 2)", "2.68 (exact: 67/25)"),
])
def test_arithmetic_algebra_calculus(text, want):
    assert r(text) == want


@pytest.mark.parametrize("text,want", [
    ("emi(500000, 9.5%, 60)", "10,500.93065"), ("emi(500000, 9.5, 60)", "10,500.93065"), ("emi(500000, 0.095, 60)", "10,500.93065"),
    ("gst(1000, 18%)", "180"), ("gst(1000, 18)", "180"), ("with_gst(1000, 18%)", "1,180"),
    ("simple_interest(10000, 6%, 2)", "1,200"), ("pct_change(1200, 1500)", "25"),
])
def test_rates_mean_the_same_however_written(text, want):
    assert r(text) == want


def test_compound_npv_irr():
    assert r("compound(200000, 8%, 10)").startswith("431,784.9995")
    assert r("compound(200000, 0.08, 10)").startswith("431,784.9995")
    assert r("npv(10%, 100, 100, 100)").startswith("248.685")
    assert r("irr(-1000, 300, 400, 500)").startswith("8.896")


def test_indian_grouping():
    assert r("1,00,000 * 12.5", indian=True) == "12,50,000"


def test_code_and_errors_are_refused():
    for bad in ["__import__('os')", "open('x')", "().__class__", "1/0", "factorial(100000)"]:
        assert evaluate(bad)["error"]


def test_steps_use_earlier_results():
    steps = evaluate_steps([{"name": "p", "expr": "500000"}, {"name": "m", "expr": "emi(p, 9.5%, 60)"},
                            {"name": "total", "expr": "m * 60"}, {"name": "interest", "expr": "total - p"}])
    assert [s["result"][:11] for s in steps] == ["500,000", "10,500.9306", "630,055.839", "130,055.839"]


def test_needs_math():
    assert needs_math("What is the EMI on a 5 lakh loan at 9.5% for 5 years?")
    assert needs_math("10 लाख पर 8% ब्याज कितना होगा?")
    assert needs_math("solve x^2 = 4")
    assert not needs_math("hello")
    assert not needs_math("who is the CEO of Acme?")
