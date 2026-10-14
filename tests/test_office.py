"""Office: saving the Docs canvas writes only changed blocks into the .docx (needs Vyum's GenOffice build)."""
import pytest

from engine import config, office

pytestmark = pytest.mark.skipif(not (config.GENOFFICE_APP / "Contents/Resources/cli/genoffice").exists(),
                                reason="GenOffice is not built")


@pytest.fixture
def doc(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OFFICE_DIR", tmp_path / "office")
    src = tmp_path / "t.md"
    src.write_text("# T\n\nIntro text.\n\n| Item | Cost |\n|---|---|\n| Dev | 45,000 |\n| Total | 80,000 |\n\n"
                   "## Conclusion\n\nBudget is EUR 80,000.\n\nThank you for your continued partnership.\n")
    out = tmp_path / "t.docx"
    office.cli("create", "--type", "docx", "--from", src, "--out", out)
    return office.register(out, "opened"), out


def blocks(path):
    return [(b["type"], b.get("text", "")) for b in office.cli("docs", "read", path, "--full")["detail"]["items"]]


def test_editor_spelling_of_an_unchanged_table_is_not_a_change(doc):
    it, _ = doc
    h = office.doc_html(office.get(it["id"]))["html"]
    tiptap = (h.replace("<table>", '<table style="min-width: 50px"><colgroup><col><col></colgroup>')
               .replace("<td>", '<td colspan="1" rowspan="1"><p>').replace("</td>", "</p></td>"))
    assert office.save_doc_html(office.get(it["id"]), tiptap) == {"changed": False}


def test_table_change_insert_and_rewrite_land_in_place(doc):
    it, path = doc
    h = office.doc_html(office.get(it["id"]))["html"]
    h = (h.replace("80,000", "95,000").replace("<h2>Conclusion</h2>", "<h2>Risks</h2><p>Delays.</p><h2>Conclusion</h2>")
          .replace("continued partnership.", "<strong>continued</strong> partnership and support."))
    assert office.save_doc_html(office.get(it["id"]), h)["changed"]
    assert blocks(path) == [("heading", "T"), ("paragraph", "Intro text."), ("table", "ItemCostDev45,000Total95,000"),
                            ("heading", "Risks"), ("paragraph", "Delays."), ("heading", "Conclusion"),
                            ("paragraph", "Budget is EUR 95,000."), ("paragraph", "Thank you for your continued partnership and support.")]


def test_lists_map_to_one_block_per_item(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    src = tmp_path / "l.md"
    src.write_text("# P\n\n## Goals\n\n- One\n- Two\n- Three\n\n## Budget\n\nEUR 80,000.\n\n## Conclusion\n\nDone.\n")
    out = tmp_path / "l.docx"
    office.cli("create", "--type", "docx", "--from", src, "--out", out)
    it = office.register(out, "opened")
    h = office.doc_html(office.get(it["id"]))["html"]
    h = h.replace("EUR 80,000.", "EUR 95,000.").replace("<h2>Conclusion</h2>", "<h2>Risks</h2><p>Delays.</p><h2>Conclusion</h2>")
    office.save_doc_html(office.get(it["id"]), h)
    assert [t for _, t in blocks(out)] == ["P", "Goals", "One", "Two", "Three", "Budget", "EUR 95,000.", "Risks", "Delays.",
                                           "Conclusion", "Done."]
