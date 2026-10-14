"""Mail sign-in: provider detection, the OAuth (PKCE) link, the code exchange and the XOAUTH2 login string."""
import base64
import hashlib
import json
import urllib.parse

import httpx
import pytest

from engine import config, mailauth


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    vault = {}
    monkeypatch.setattr(mailauth.keyring, "set_password", lambda s, k, v: vault.__setitem__((s, k), v))
    monkeypatch.setattr(mailauth.keyring, "get_password", lambda s, k: vault.get((s, k)))
    monkeypatch.setattr(mailauth, "_mx", lambda d: {"acme.test": ["aspmx.l.google.com"], "corp.test": ["corp-test.mail.protection.outlook.com"]}.get(d, []))
    monkeypatch.setattr(mailauth, "_autoconfig", lambda d: None)
    mailauth._cache.clear()
    return vault


def test_detects_providers_and_hosted_domains(env):
    assert mailauth.detect("a@gmail.com")["method"] == "oauth"
    assert mailauth.detect("a@acme.test")["provider"] == "google"          # Google Workspace found through MX
    assert mailauth.detect("a@corp.test")["provider"] == "microsoft"       # Microsoft 365 found through MX
    z = mailauth.detect("a@zoho.com")
    assert z["method"] == "password" and z["imap"] == ("imap.zoho.com", 993) and "app password" in z["hint"]
    o = mailauth.detect("a@example.test")
    assert o["guessed"] and o["imap"] == ("imap.example.test", 993)
    assert "error" in mailauth.detect("not-an-address")


def test_oauth_link_uses_pkce_and_exchange_stores_refresh_token(env, monkeypatch):
    mailauth.save_clients({"google": {"client_id": "cid", "client_secret": "sec"}})
    r = mailauth.start("google", "om@gmail.com", "http://127.0.0.1:5555/mail/oauth/callback")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r["url"]).query))
    assert q["code_challenge_method"] == "S256" and q["login_hint"] == "om@gmail.com" and q["access_type"] == "offline"
    verifier = mailauth._pending[r["state"]]["verifier"]
    assert q["code_challenge"] == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    claims = base64.urlsafe_b64encode(json.dumps({"email": "Om@Gmail.com"}).encode()).decode().rstrip("=")
    sent = {}

    def fake_post(url, data, timeout):
        sent.update(data)
        return httpx.Response(200, json={"access_token": "AT", "refresh_token": "RT", "expires_in": 3600, "id_token": f"h.{claims}.s"})
    monkeypatch.setattr(mailauth.httpx, "post", fake_post)
    got = mailauth.finish(r["state"], "CODE", None)
    assert got == {"provider": "google", "email": "om@gmail.com"} and sent["code_verifier"] == verifier
    assert json.loads(env[("Vyum Mail", "oauth:om@gmail.com")])["refresh_token"] == "RT"
    assert mailauth.xoauth2("om@gmail.com") == "user=om@gmail.com\x01auth=Bearer AT\x01\x01"
    assert mailauth.poll(r["state"])["status"] == "done"


def test_unknown_or_expired_state_is_refused(env):
    with pytest.raises(ValueError):
        mailauth.finish("nope", "CODE", None)


def test_start_without_app_id_explains(env):
    with pytest.raises(ValueError, match="not set up"):
        mailauth.start("microsoft", "a@outlook.com", "http://localhost:1/cb")
