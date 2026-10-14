"""Formula helpers of the formula-first sheet editor (no model, no GenOffice)."""
import pytest

from engine.sheets import StepError, check_formula, clean_formula, shift_rows


def test_fill_down_shifts_relative_rows_only():
    assert shift_rows("=F2/SUM($F$2:$F$41)", 3) == "=F5/SUM($F$2:$F$41)"
    assert shift_rows("=XLOOKUP(C2,'Staff'!$A$2:$A$6,'Staff'!$B$2:$B$6)", 1) == "=XLOOKUP(C3,'Staff'!$A$2:$A$6,'Staff'!$B$2:$B$6)"
    assert shift_rows('=IF(D2>=100,"A2 big","small")', 2) == '=IF(D4>=100,"A2 big","small")'  # text stays
    assert shift_rows("=LOG10(B2)+B$2", 1) == "=LOG10(B3)+B$2"


def test_model_slips_are_cleaned():
    assert clean_formula('="="&RANK.EQ(F2,$F$2:$F$41,0)') == "=RANK.EQ(F2,$F$2:$F$41,0)"
    assert clean_formula("==SUM(A1:A3)") == "=SUM(A1:A3)"
    assert clean_formula("SUM(A1:A2)") == "=SUM(A1:A2)"
    assert clean_formula('=SUMIFS(F2:F9,A2:A9,">=2025-02-01")') == '=SUMIFS(F2:F9,A2:A9,">="&DATE(2025,2,1))'


def test_unavailable_functions_are_refused_with_a_hint():
    check_formula("=XLOOKUP(A2,B:B,C:C)")
    check_formula("=SUMIFS(F2:F9,B2:B9,\"North\")")
    with pytest.raises(StepError, match="summary"):
        check_formula("=UNIQUE(B2:B9)")
    with pytest.raises(StepError, match="SUMIFS"):
        check_formula("=SUMPRODUCT(D2:D9,E2:E9)")
    with pytest.raises(StepError):
        check_formula("=SUM(A1:A3")


def test_numbers_the_model_computed_become_formulas():
    from engine.sheets import infer_formulas
    rows = [["Category", "Planned", "Actual", "Difference"], ["Rent", 2000, 2100, 100], ["Salaries", 4000, 4200, 200],
            ["Misc", 600, 650, 50], ["Total", 6600, "=SUM(C2:C4)", 350]]
    out, notes = infer_formulas(rows)
    assert [r[3] for r in out[1:4]] == ["=C2-B2", "=C3-B3", "=C4-B4"]
    assert out[4][1] == "=SUM(B2:B4)" and out[4][3] == "=SUM(D2:D4)"
    assert notes


def test_column_names_become_exact_references():
    from engine.sheets import build_specs
    p = {"sheets": [
        {"name": "Products", "headers": ["Product", "Unit Price"], "rows": [["Laptop", 50000], ["Mouse", 500]]},
        {"name": "Orders", "headers": ["Product", "Qty", "Region", "Price", "Revenue"],
         "rows": [["Laptop", 2, "North"], ["Mouse", 5, "South"]],
         "formulas": {"Price": "=XLOOKUP([Product], Products[Product], Products[Unit Price], 0)", "Revenue": "=[Qty]*[Price]"},
         "totals": {"Revenue": "SUM"}},
        {"name": "Summary", "headers": ["Region", "Revenue"], "rows": [["North"], ["South"]],
         "formulas": {"Revenue": "=SUMIFS(Orders[Revenue], Orders[Region], [Region])"}}]}
    specs = {x["name"]: x["rows"] for x in build_specs(p)}
    assert specs["Orders"][1][3] == "=XLOOKUP(A2, 'Products'!$A$2:$A$3, 'Products'!$B$2:$B$3, 0)"
    assert specs["Orders"][2][4] == "=B3*D3"
    assert specs["Orders"][3] == ["Total", None, None, None, "=SUM(E2:E3)"]
    assert specs["Summary"][2][1] == "=SUMIFS('Orders'!$E$2:$E$3, 'Orders'!$C$2:$C$3, A3)"


def test_short_tables_are_caught():
    from engine.sheets import build_specs, check_counts
    specs = build_specs({"sheets": [{"name": "Orders", "headers": ["Item"], "rows": [["a"], ["b"], ["c"]]}]})
    with pytest.raises(StepError, match="25"):
        check_counts(specs, "with 25 orders")
    check_counts(specs, "with 3 orders")
