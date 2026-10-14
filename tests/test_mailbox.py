"""Mail: sync, search, threads, actions and safe HTML, against a fake IMAP server that answers like imaplib does."""
import re
from email.message import EmailMessage

import pytest

from engine import config, mailbox


def mime(uid, subject, frm, to, body, html=None, msgid=None, refs=None, attach=None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, to, subject
    m["Message-ID"] = msgid or f"<m{uid}@test>"
    m["Date"] = f"Mon, 0{min(uid, 9)} Oct 2026 10:00:00 +0000"
    if refs:
        m["In-Reply-To"] = refs
        m["References"] = refs
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    if attach:
        m.add_attachment(attach, maintype="application", subtype="pdf", filename="invoice.pdf")
    return m.as_bytes()


class FakeIMAP:
    def __init__(self, folders):
        self.folders = folders           # name -> {"uv": int, "msgs": {uid: [flags, raw]}, "flags": "\\Sent" ...}
        self.cur = None
        self.capabilities = ("IMAP4REV1", "MOVE")

    def list(self):
        return "OK", [f'({f.get("flags", "")}) "/" "{n}"'.encode() for n, f in self.folders.items()] + \
            [b'(\\Noselect \\HasChildren) "/" "[Gmail]"']

    def select(self, name, readonly=False):
        self.cur = name.strip('"')
        return ("OK", [str(len(self.folders[self.cur]["msgs"])).encode()]) if self.cur in self.folders else ("NO", [b""])

    def response(self, code):
        return code, [str(self.folders[self.cur]["uv"]).encode()]

    def _uids(self, s):
        return [int(x) for x in s.split(",")]

    def uid(self, cmd, *args):
        msgs = self.folders[self.cur]["msgs"]
        if cmd == "SEARCH":
            return "OK", [" ".join(str(u) for u in sorted(msgs)).encode()]
        if cmd == "FETCH":
            out = []
            for u in self._uids(args[0]):
                fl, raw = msgs[u]
                if "BODY.PEEK[]" in args[1]:
                    out += [(f"{u} (UID {u} FLAGS ({fl}) RFC822.SIZE {len(raw)} BODY[] {{{len(raw)}}}".encode(), raw), b")"]
                else:
                    out.append(f"{u} (UID {u} FLAGS ({fl}))".encode())
            return "OK", out
        if cmd == "STORE":
            for u in self._uids(args[0]):
                fl = set(msgs[u][0].split())
                flag = args[2].strip("()")
                fl.add(flag) if args[1].startswith("+") else fl.discard(flag)
                msgs[u][0] = " ".join(sorted(fl))
            return "OK", [b""]
        if cmd == "MOVE":
            dest = self.folders[args[1].strip('"')]["msgs"]
            for u in self._uids(args[0]):
                dest[max(dest, default=0) + 1] = msgs.pop(u)
            return "OK", [b""]
        raise AssertionError(cmd)

    def expunge(self):
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    folders = {
        "INBOX": {"uv": 7, "msgs": {
            1: ["\\Seen", mime(1, "Q3 invoice 4471", "Alice Rao <alice@example.com>", "om@vyum.ai", "Invoice for EUR 12,400 is due 30 October.", attach=b"%PDF-1")],
            2: ["", mime(2, "Re: Q3 invoice 4471", "Bob <bob@example.com>", "om@vyum.ai", "Approved, we pay on 28 October.", refs="<m1@test>")],
            3: ["\\Flagged", mime(3, "Team lunch", "Priya <priya@example.com>", "om@vyum.ai", "Lunch on Friday?",
                                  html="<p onclick='x()'>Lunch on <b>Friday</b>?</p><img src='https://tracker.example/p.gif'><script>alert(1)</script>")],
        }},
        "Sent": {"uv": 3, "flags": "\\Sent", "msgs": {}},
        "Trash": {"uv": 4, "flags": "\\Trash", "msgs": {}},
    }
    fake = FakeIMAP(folders)
    monkeypatch.setattr(mailbox, "_imap", lambda a, password=None: fake)
    acct = {"id": "acc1", "email": "om@vyum.ai", "provider": "other", "imap_host": "x", "imap_port": 993,
            "smtp_host": "x", "smtp_port": 465}
    mailbox._save_accounts([acct])
    mailbox.sync(acct)
    return fake, acct


def test_sync_folders_threads_and_counts(server):
    fake, acct = server
    names = {f["name"]: f for f in mailbox.folders("acc1")}
    assert names["INBOX"]["role"] == "inbox" and names["Sent"]["role"] == "sent" and names["INBOX"]["unread"] == 2
    inbox = mailbox.search_messages("acc1", "INBOX")
    assert inbox["total"] == 2                                      # the invoice and its reply are one conversation
    conv = next(m for m in inbox["messages"] if "invoice" in m["subject"].lower())
    assert conv["count"] == 2 and conv["from_name"] == "Bob"        # shown by its latest message
    th = mailbox.thread("acc1", conv["thread"])
    assert [m["from_addr"] for m in th] == ["alice@example.com", "bob@example.com"]
    assert th[0]["attachments"][0]["name"] == "invoice.pdf"


def test_gmail_style_search(server):
    s = lambda q: {m["subject"] for m in mailbox.search_messages("acc1", "INBOX", q)["messages"]}
    assert s("from:priya") == {"Team lunch"}
    assert s("is:starred") == {"Team lunch"}
    assert s("has:attachment") == {"Q3 invoice 4471"}
    assert s("is:unread") == {"Re: Q3 invoice 4471", "Team lunch"}
    assert s("12,400") == {"Q3 invoice 4471"} or s("12") == {"Q3 invoice 4471"}
    assert s("friday") == {"Team lunch"}


def test_actions_change_server_and_cache(server):
    fake, acct = server
    lunch = next(m for m in mailbox.search_messages("acc1", "INBOX")["messages"] if m["subject"] == "Team lunch")
    mailbox.act("acc1", [lunch["id"]], "unstar")
    assert "\\Flagged" not in fake.folders["INBOX"]["msgs"][3][0]
    monkey_sync = mailbox.start_sync
    mailbox.start_sync = lambda aid=None: None
    try:
        mailbox.act("acc1", [lunch["id"]], "delete")
    finally:
        mailbox.start_sync = monkey_sync
    assert 3 not in fake.folders["INBOX"]["msgs"] and len(fake.folders["Trash"]["msgs"]) == 1
    assert all(m["subject"] != "Team lunch" for m in mailbox.search_messages("acc1", "INBOX")["messages"])


def test_html_is_made_safe(server):
    lunch = next(m for m in mailbox.search_messages("acc1", "INBOX")["messages"] if m["subject"] == "Team lunch")
    html, blocked = mailbox.safe_html(lunch["id"], images=False)
    assert blocked == 1 and "tracker.example/p.gif" not in re.sub(r'data-blocked="[^"]*"', "", html)
    assert "<script" not in html and "onclick" not in html and "<b>Friday</b>" in html
    html2, blocked2 = mailbox.safe_html(lunch["id"], images=True)
    assert blocked2 == 0 and "https://tracker.example/p.gif" in html2


def test_folder_names_round_trip_modified_utf7():
    for name in ("INBOX", "Envoyés", "[Gmail]/Sent Mail", "Ünïcødé & Co"):
        assert mailbox._decode_name(mailbox._encode_name(name)) == name
