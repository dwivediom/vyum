"""Email files: .eml and .mbox become one section per message, plain or HTML bodies."""
from engine.ingest import read_text
from engine.mail import messages, to_markdown

MBOX = """From alice@example.com Mon Oct 05 09:12:00 2026
From: Alice Rao <alice@example.com>
To: Om <om@vyum.ai>
Subject: Q3 invoice 4471
Date: Mon, 05 Oct 2026 09:12:00 +0530
Content-Type: text/plain; charset=utf-8

Hi Om,

Invoice 4471 for EUR 12,400 is due on 30 October 2026.

Thanks,
Alice

From bob@example.com Tue Oct 06 10:00:00 2026
From: bob@example.com
To: om@vyum.ai
Subject: Re: Q3 invoice 4471
Date: Tue, 06 Oct 2026 10:00:00 +0000
Content-Type: text/html; charset=utf-8

<html><body><p>Payment approved.</p><p>We will pay on <b>28 October</b>.</p></body></html>
"""


def test_mbox_messages_and_markdown(tmp_path):
    f = tmp_path / "inbox.mbox"
    f.write_text(MBOX)
    ms = messages(f)
    assert [m["subject"] for m in ms] == ["Q3 invoice 4471", "Re: Q3 invoice 4471"]
    assert ms[0]["from"] == "Alice Rao <alice@example.com>" and "EUR 12,400" in ms[0]["body"]
    assert "We will pay on" in ms[1]["body"] and "28 October" in ms[1]["body"] and "<b>" not in ms[1]["body"]
    md = tmp_path / "inbox.md"
    md.write_text(to_markdown(f))
    chunks = [c for seg in read_text(md) for c in seg.chunks]
    assert {c.section for c in chunks} == {"Q3 invoice 4471", "Re: Q3 invoice 4471"}


def test_single_eml(tmp_path):
    f = tmp_path / "one.eml"
    f.write_text(MBOX.split("\n\nFrom bob")[0].split("\n", 1)[1])
    ms = messages(f)
    assert len(ms) == 1 and ms[0]["date"] == "05 Oct 2026 09:12"
