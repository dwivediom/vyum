"""Tests for the large-document pipeline: headings, page furniture, identifiers, streaming and resume."""
from engine.harness import _renumber, identifiers
from engine.ingest import Headings, learn_page_furniture, read_text


def feed_all(lines):
    h, paths = Headings(), []
    for line in lines:
        if h.feed(line):
            paths.append(h.path)
    return h, paths


def test_two_level_paths_and_numbered_clauses():
    h, paths = feed_all(["SUPPLIER AGREEMENT NO. 05012 — HALVORSEN FREIGHT AS", "4. Payment", "Body text that runs on.",
                         "6. Warranty"])
    assert paths == ["SUPPLIER AGREEMENT NO. 05012 — HALVORSEN FREIGHT AS",
                     "SUPPLIER AGREEMENT NO. 05012 — HALVORSEN FREIGHT AS › 4. Payment",
                     "SUPPLIER AGREEMENT NO. 05012 — HALVORSEN FREIGHT AS › 6. Warranty"]


def test_wrapped_heading_is_joined_not_replaced():
    h, _ = feed_all(["SUPPLIER AGREEMENT NO. 04773 — LINDQVIST COLD CHAIN AB AND NORTHWIND LOGISTICS", "AS", "1. Parties"])
    assert h.path == "SUPPLIER AGREEMENT NO. 04773 — LINDQVIST COLD CHAIN AB AND NORTHWIND LOGISTICS AS › 1. Parties"


def test_distinct_consecutive_headings_are_not_merged():
    h, _ = feed_all(["PART II — SUPPLIER AGREEMENTS", "SUPPLIER AGREEMENT NO. 00001 — BALTIC METALL OY AND NORTHWIND"])
    assert h.l1 == "SUPPLIER AGREEMENT NO. 00001 — BALTIC METALL OY AND NORTHWIND"


def test_article_label_joins_its_title():
    h, _ = feed_all(["CHAPTER III", "Article 17", "Right to erasure ('right to be forgotten')", "1. The data subject shall have the right."])
    assert h.path == "CHAPTER III › Article 17 — Right to erasure ('right to be forgotten')"


def test_sentences_and_list_items_are_not_headings():
    h = Headings()
    for line in ["2. Paragraph 1 shall not apply where:", "The Buyer shall pay within 45 days.", "AS",
                 "1. The controller shall implement appropriate technical and organisational measures to ensure security."]:
        assert not h.classify(line), line


def test_page_furniture_learned_with_digits_normalised():
    import random
    rng = random.Random(1)
    words = "the buyer supplier shall pay invoice within days delivery warranty clause agreement notice".split()
    body = lambda: " ".join(rng.choice(words) for _ in range(12))
    pages = ["\n".join(["Northwind · Master Archive", f"Page {i}"] + [body() for _ in range(8)]) for i in range(1, 30)]
    assert learn_page_furniture(pages) == {"northwind · master archive", "page #"}
    assert learn_page_furniture(pages[:5]) == set()  # too few pages to tell


def test_identifiers():
    assert identifiers("What is the payment term under Supplier Agreement No. 05012?") == ["05012"]
    assert identifiers("What is the access code QX-4471-B and audit NV-2207-981?") == ["QX-4471-B", "NV-2207-981"]
    assert identifiers("Did the ISO 14001 audit in 2023 pass within 72 hours?") == ["14001"]
    assert identifiers("How much salmon was moved in Q3 2025?") == []


def test_renumber_citations():
    assert _renumber("Fact [1][3] and more [2, 3].", 10) == "Fact [11][13] and more [12][13]."


def test_text_stream_resume_matches_full_run(tmp_path):
    lines = []
    for s in range(60):
        lines += [f"# Section {s}", ""] + [f"Paragraph {s}.{p} " + "word " * 30 for p in range(8) for _ in (0,)]
        lines = lines[:-8] + [x for p in lines[-8:] for x in (p, "")]
    f = tmp_path / "big.md"
    f.write_text("\n".join(lines))
    full = [seg for seg in read_text(f, 0, segment_lines=120)]
    assert len(full) > 5
    texts = [c.text for seg in full for c in seg.chunks]
    assert all(c.page_start >= 1 for seg in full for c in seg.chunks)  # line numbers, not "page 1"
    cut = full[2].done  # resume after the third checkpoint
    resumed = [c.text for seg in read_text(f, cut, segment_lines=120) for c in seg.chunks]
    before = [c.text for seg in full[:3] for c in seg.chunks]
    assert before + resumed == texts  # nothing lost, nothing duplicated
    sections = {c.section for seg in read_text(f, cut, segment_lines=120) for c in seg.chunks}
    assert all(s.startswith("Section ") for s in sections)  # headings replayed before the checkpoint


def test_sentence_split_keeps_abbreviations():
    from engine.harness import split_sentences
    assert split_sentences("The penalty under Supplier Agreement No. 00161 is EUR 1,875 [1]. It applies daily [1].") == [
        "The penalty under Supplier Agreement No. 00161 is EUR 1,875 [1].", "It applies daily [1]."]
    assert split_sentences("Costs rise, e.g. fuel and fees [2]. Art. 17 applies [3].") == [
        "Costs rise, e.g. fuel and fees [2].", "Art. 17 applies [3]."]
    assert split_sentences("Paid in 30 days. then late fees apply [1].") == ["Paid in 30 days. then late fees apply [1]."]


def test_question_names_and_grounding():
    from engine.harness import _ungrounded_name, question_names
    assert question_names("When was the backup generator at the Bergen depot replaced?") == ["Bergen"]
    assert question_names("What are the payment terms in the Globex supply agreement?") == ["Globex"]
    assert question_names("Which supply agreement has the longer warranty, Acme or Globex?") == ["Acme", "Globex"]
    tromso = {"doc_name": "archive.pdf", "section": "OPERATIONS LOG — MARCH 2021", "text": "At the Tromsø depot the backup generator was replaced on 14 March 2021."}
    assert _ungrounded_name("The Bergen generator was replaced on 14 March 2021 [1].", [1], [tromso], ["Bergen"]) == "Bergen"
    assert _ungrounded_name("It was replaced on 14 March 2021 [1].", [1], [tromso], ["Bergen"]) is None
    acme = {"doc_name": "acme_supply_agreement.pdf", "section": "4. Payment", "text": "Pay within 45 days."}
    assert _ungrounded_name("Acme requires payment within 45 days [1].", [1], [acme], ["Acme"]) is None  # name in file name


def test_find_prefers_exact_phrase():
    from engine.harness import find

    class FakeStore:
        def keyword_search(self, text, k, doc_ids=None):
            mk = lambda t, p: {"doc_id": "d", "doc_name": "a.pdf", "section": "", "page_start": p, "text": t, "kind": "text"}
            return [mk("(b) the processor shall assist", 3), mk("The code for vault 7 is QX-4471-B after review.", 71),
                    mk("Item b of 4471 units", 9)]
    r = find(FakeStore(), "QX-4471-B")
    assert r["exact"] == 1 and [h["page"] for h in r["hits"]] == [71]


def test_anchored_grounding_on_mixed_passages():
    from engine.harness import _ungrounded_name
    log = {"doc_name": "northwind_master_archive.pdf", "section": "OPERATIONS LOG — MARCH 2021",
           "text": "13 March 2021 — Bergen depot. Inbound volume was 410 pallets. 14 March 2021. On inspection at the "
                   "Tromsø depot, technicians recorded that the backup generator had been replaced on 14 March 2021 at a "
                   "cost of NOK 412,750. 15 March 2021 — Molde depot. Night supervisor Ingrid Solheim at the Bodø depot "
                   "reported that the roof membrane above hall C was replaced in a single weekend."}
    # Bergen is in the passage, but not in the sentence that carries the date: dropped
    assert _ungrounded_name("The backup generator at the Bergen depot was replaced on 14 March 2021 [1].", [1], [log], ["Bergen"]) == "Bergen"
    # the real fact, named correctly: kept
    assert _ungrounded_name("At the Tromsø depot the generator was replaced on 14 March 2021 [1].", [1], [log], ["Tromsø"]) is None
    # no numbers: anchored on distinctive words ("supervisor", "solheim"): Molde is elsewhere in the passage, dropped
    assert _ungrounded_name("The night supervisor at the Molde depot was Ingrid Solheim [1].", [1], [log], ["Molde"]) == "Molde"
    # identity from the file name still counts (a chart description never repeats the company name)
    fig = {"doc_name": "acme_supply_agreement.pdf", "section": "6. Delivery history", "text": "Q1: 8,200\nQ3: 11,300"}
    assert _ungrounded_name("Acme delivered 11,300 units in Q3 2025 [1].", [1], [fig], ["Acme"]) is None
    glo = {"doc_name": "contracts/globex_supply_agreement.docx", "section": "Delivery", "text": "Deliver within 10 business days."}
    assert _ungrounded_name("Globex must deliver within ten (10) business days [1].", [1], [glo], ["Globex"]) is None


def test_grounding_accepts_caption_identity_and_possessives():
    from engine.harness import _ungrounded_name, question_names
    q = "What was Vyum Labs' Q3 2026 revenue in the West region?"
    assert question_names(q) == ["Vyum", "Labs", "West"]
    fig = {"doc_name": "q3_revenue_by_region.png", "section": "Image",
           "text": "North: 4.2 USD million\nWest: 5.6 USD million\n\nThe figure shows the revenue by region for Vyum Labs in Q3 2026."}
    assert _ungrounded_name("Vyum Labs' Q3 2026 revenue in the West region was 5.6 USD million [1].", [1], [fig],
                            question_names(q)) is None
