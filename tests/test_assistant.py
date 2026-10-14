"""General chat helpers: citation clean-up, search planning guard, and how messages are routed."""
from engine.assistant import _ASKS_TO_SEARCH, clean_citations
from engine.web import page_text, site


def test_citations_that_point_nowhere_are_removed_but_code_is_kept():
    text = "It is a palindrome [1]. Use `s[1]` or\n```py\nx = a[2]\n```\nDone [2][3]."
    assert clean_citations(text, 2) == "It is a palindrome [1]. Use `s[1]` or\n```py\nx = a[2]\n```\nDone [2]."
    assert clean_citations("Shipped in August [1][4], see [2, 9].", 3) == "Shipped in August [1], see [2]."
    assert clean_citations("A palindrome reads the same [1].", 0) == "A palindrome reads the same."
    assert clean_citations("matrix m[1][2] stays", 0) == "matrix m[1][2] stays"


def test_explicit_requests_to_search():
    assert _ASKS_TO_SEARCH.search("search the web for the new iPhone price")
    assert _ASKS_TO_SEARCH.search("What's the latest news on the Fed?")
    assert not _ASKS_TO_SEARCH.search("Write a haiku about autumn")


def test_web_helpers():
    assert site("https://www.python.org/downloads/") == "python.org"
    html = ("<html><body><nav>Home About Contact Login Register Menu</nav><article><h1>Release notes</h1>"
            "<p>Version 3.14 was released on 7 October 2025 with free-threading support.</p>"
            "<p>It also adds template strings and deferred evaluation of annotations.</p></article>"
            "<footer>Copyright footer text that is long enough to count</footer></body></html>")
    text = page_text(html)
    assert "Version 3.14 was released" in text and "Login" not in text and "Copyright" not in text


def test_compare_mode_uses_every_file_when_none_are_named():
    from engine import commands
    docs = [{"id": "a", "name": "acme.pdf"}, {"id": "g", "name": "globex.docx"}]
    seen = {}

    def fake_compare(store, aspects, ids, mode, think):
        seen.update(aspects=aspects, ids=ids)
        return {"status": "answered", "rows": [], "summary": [], "citations": [], "documents": [], "timings": {}}
    orig, commands.harness.compare = commands.harness.compare, fake_compare
    try:
        out = list(commands.run("/compare payment terms", store=None, docs=docs, doc_ids=None, mode="fast", think=False,
                                compare_all=True))
    finally:
        commands.harness.compare = orig
    assert seen == {"aspects": "payment terms", "ids": ["a", "g"]} and out[-1]["result"]["kind"] == "compare"
    one = list(commands.run("/compare warranty", store=None, docs=docs[:1], doc_ids=None, mode="fast", think=False,
                            compare_all=True))
    assert "Attach them with +" in one[-1]["result"]["note"]


def test_scope_phrases_do_not_sink_the_evidence_score():
    from engine.harness import scoring_question as f
    assert f("How long is the warranty in each agreement?") == "How long is the warranty?"
    assert f("What are the payment terms in both contracts?") == "What are the payment terms?"
    assert f("What is the governing law of each of the agreements?") == "What is the governing law?"
    assert f("Compare the liability cap across all the documents") == "Compare the liability cap"
    for q in ["Which agreement has the longer warranty, Acme or Globex?", "What does Article 17 say?",
              "Do all documents mention GDPR?"]:
        assert f(q) == q
