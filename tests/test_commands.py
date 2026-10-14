from engine.commands import is_command, resolve_files
from engine.harness import split_topics


def test_split_topics():
    assert split_topics("payment terms, warranty and governing law") == ["payment terms", "warranty", "governing law"]
    assert split_topics("liability cap") == ["liability cap"]
    assert split_topics("price vs. delivery; insurance") == ["price", "delivery", "insurance"]

DOCS = [{"id": "a", "name": "acme_supply_agreement.pdf"}, {"id": "g", "name": "globex_supply_agreement.docx"},
        {"id": "r", "name": "gdpr_regulation_2016_679.pdf"}, {"id": "h", "name": "employee_handbook.md"}]


def test_is_command():
    assert is_command("/compare a b") and is_command(" /help")
    assert not is_command("what is /compare?") and not is_command("/ not a command")


def test_distinctive_words_pick_files_and_shared_words_stay_text():
    picked, rest = resolve_files("acme globex supply payment terms", DOCS)
    assert [d["id"] for d in picked] == ["a", "g"]
    assert rest == "supply payment terms"


def test_full_and_quoted_names():
    picked, rest = resolve_files('"employee_handbook.md" gdpr_regulation_2016_679 leave', DOCS)
    assert [d["id"] for d in picked] == ["h", "r"] and rest == "leave"


def test_project_paths_match_by_base_name():
    docs = [{"id": "a", "name": "contracts/acme_supply_agreement.pdf"}, {"id": "g", "name": "contracts/globex_supply_agreement.docx"}]
    picked, rest = resolve_files("acme_supply_agreement globex_supply_agreement.docx warranty", docs)
    assert [d["id"] for d in picked] == ["a", "g"] and rest == "warranty"


def test_prefix_match_and_no_duplicates():
    picked, _ = resolve_files("acm acme glob", DOCS)
    assert [d["id"] for d in picked] == ["a", "g"]


def test_compare_cells_map_by_label_or_name():
    from engine.harness import cell_doc
    docs = [{"id": "a", "name": "contracts/acme_supply_agreement.pdf"}, {"id": "g", "name": "contracts/globex_supply_agreement.docx"}]
    labels = {"a": "A", "g": "B"}
    assert [cell_doc(k, docs, labels) for k in ("A", "Document B", "acme_supply_agreement.pdf",
                                                 "globex_supply_agreement", "Acme", "zzz")] == ["a", "g", "a", "g", "a", None]


def test_quick_replies_skip_the_search():
    from engine.commands import quick_reply
    assert quick_reply("hello", [])["title"] == "Hi, I'm Vyum"
    assert quick_reply("what is the payment term?", [])["title"] == "Add a document first"
    assert "rescan" in quick_reply("what is the payment term?", [], in_project=True)["note"]
    assert "4 documents" in quick_reply("Hey there!", DOCS)["note"]
    assert quick_reply("thank you", DOCS)["title"] == "You're welcome"
    assert quick_reply("hello, what are the payment terms?", DOCS) is None  # a real question is searched
    assert quick_reply("What does the hey clause say?", DOCS) is None
