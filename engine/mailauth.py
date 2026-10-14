"""Sign in to any mailbox: find the provider and its servers from the address, then sign in with OAuth in the browser
(Google, Microsoft) or with a password (everything else).

OAuth uses the desktop-app flow: the system browser shows the provider's own sign-in page, which redirects to this
engine on 127.0.0.1 with a one-time code (PKCE, so the code is useless to anyone else). Refresh tokens are kept in the
macOS Keychain. The app IDs ("clients") belong to whoever ships Vyum and are set once in Settings."""
import base64
import hashlib
import json
import re
import secrets
import subprocess
import time
import urllib.parse
import xml.etree.ElementTree as ET

import httpx
import keyring

from . import config

KEYCHAIN = "Vyum Mail"

OAUTH = {
    "google": {"label": "Google", "auth": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
               "scope": "https://mail.google.com/ openid email profile",
               "imap": ("imap.gmail.com", 993), "smtp": ("smtp.gmail.com", 465, "ssl"),
               "extra": {"access_type": "offline", "prompt": "consent"},
               "setup": "https://console.cloud.google.com/apis/credentials"},
    "microsoft": {"label": "Microsoft", "auth": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                  "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                  "scope": "https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send offline_access openid email profile",
                  "imap": ("outlook.office365.com", 993), "smtp": ("smtp.office365.com", 587, "starttls"),
                  "extra": {"prompt": "select_account"},
                  "setup": "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"},
}

KNOWN = {  # domain or MX suffix -> provider id
    "gmail.com": "google", "googlemail.com": "google", "google.com": "google",
    "outlook.com": "microsoft", "hotmail.com": "microsoft", "live.com": "microsoft", "msn.com": "microsoft",
    "outlook.in": "microsoft", "hotmail.co.uk": "microsoft", "protection.outlook.com": "microsoft",
    "zoho.com": "zoho", "zohomail.com": "zoho", "zoho.in": "zoho", "zoho.eu": "zoho",
    "yahoo.com": "yahoo", "ymail.com": "yahoo", "rocketmail.com": "yahoo", "yahoodns.net": "yahoo",
    "icloud.com": "icloud", "me.com": "icloud", "mac.com": "icloud",
    "fastmail.com": "fastmail", "messagingengine.com": "fastmail",
    "aol.com": "aol", "proton.me": "proton", "protonmail.com": "proton", "gmx.com": "gmx", "gmx.net": "gmx",
}
PASSWORD_HINTS = {
    "zoho": ("Zoho", "Use your Zoho password. If you use two-step verification, create an app password: Zoho Accounts → Security → App passwords.",
             "https://accounts.zoho.com/home#security/app_password"),
    "yahoo": ("Yahoo", "Yahoo needs an app password: Account security → Generate app password.", "https://login.yahoo.com/account/security"),
    "icloud": ("iCloud", "iCloud needs an app-specific password: Apple Account → Sign-In and Security → App-Specific Passwords.",
               "https://account.apple.com"),
    "aol": ("AOL", "AOL needs an app password: Account security → Generate app password.", "https://login.aol.com/account/security"),
    "fastmail": ("Fastmail", "Create an app password in Fastmail Settings → Privacy & Security.", "https://app.fastmail.com/settings/security/apps"),
    "proton": ("Proton", "Proton Mail works with other apps only through Proton Mail Bridge, which must be running.", "https://proton.me/mail/bridge"),
    "google": ("Google", "Without Google sign-in set up, use an app password: Google Account → Security → 2-Step Verification → App passwords.",
               "https://myaccount.google.com/apppasswords"),
}
SERVERS = {
    "zoho": (("imap.zoho.com", 993), ("smtp.zoho.com", 465, "ssl")),
    "yahoo": (("imap.mail.yahoo.com", 993), ("smtp.mail.yahoo.com", 465, "ssl")),
    "icloud": (("imap.mail.me.com", 993), ("smtp.mail.me.com", 587, "starttls")),
    "fastmail": (("imap.fastmail.com", 993), ("smtp.fastmail.com", 465, "ssl")),
    "aol": (("imap.aol.com", 993), ("smtp.aol.com", 465, "ssl")),
    "gmx": (("imap.gmx.com", 993), ("mail.gmx.com", 465, "ssl")),
    "proton": (("127.0.0.1", 1143), ("127.0.0.1", 1025, "starttls")),
    "google": (("imap.gmail.com", 993), ("smtp.gmail.com", 465, "ssl")),
    "microsoft": (("outlook.office365.com", 993), ("smtp.office365.com", 587, "starttls")),
}


# app IDs -------------------------------------------------------------------------------------------------------------
def _clients_file():
    p = config.DATA_DIR / "mail"
    p.mkdir(parents=True, exist_ok=True)
    return p / "oauth_clients.json"


def clients() -> dict:
    try:
        return json.loads(_clients_file().read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_clients(patch: dict) -> dict:
    cur = clients()
    for prov in ("google", "microsoft"):
        if isinstance(patch.get(prov), dict):
            cur[prov] = {k: str(v).strip() for k, v in patch[prov].items() if k in ("client_id", "client_secret") and v is not None}
    _clients_file().write_text(json.dumps(cur, indent=1))
    return public_clients()


def public_clients() -> dict:
    c = clients()
    return {p: {"configured": bool((c.get(p) or {}).get("client_id")), "client_id": (c.get(p) or {}).get("client_id", ""),
                "setup": OAUTH[p]["setup"]} for p in OAUTH}


# detection -----------------------------------------------------------------------------------------------------------
def _mx(domain: str) -> list[str]:
    try:
        out = subprocess.run(["dig", "+short", "+time=3", "+tries=1", "MX", domain], capture_output=True, text=True, timeout=6).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [l.split()[-1].rstrip(".").lower() for l in out.splitlines() if l.strip()]


def _autoconfig(domain: str) -> dict | None:
    """Thunderbird's public directory of mail settings, then the domain's own autoconfig file."""
    for url in (f"https://autoconfig.thunderbird.net/v1.1/{domain}", f"https://autoconfig.{domain}/mail/config-v1.1.xml"):
        try:
            r = httpx.get(url, timeout=6, follow_redirects=True)
            if r.status_code != 200 or "<clientConfig" not in r.text:
                continue
            root = ET.fromstring(r.text)
            imap = root.find(".//incomingServer[@type='imap']")
            smtp = root.find(".//outgoingServer[@type='smtp']")
            if imap is None or smtp is None:
                continue
            sock = (smtp.findtext("socketType") or "SSL").upper()
            return {"imap": (imap.findtext("hostname"), int(imap.findtext("port") or 993)),
                    "smtp": (smtp.findtext("hostname"), int(smtp.findtext("port") or 465), "starttls" if sock == "STARTTLS" else "ssl"),
                    "name": root.findtext(".//displayName") or domain}
        except (httpx.HTTPError, ET.ParseError, ValueError):
            continue
    return None


def detect(email_addr: str) -> dict:
    """What to show after the user types their address: an OAuth button, or a password box with servers filled in."""
    email_addr = email_addr.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email_addr):
        return {"error": "Enter a full email address, like name@example.com."}
    domain = email_addr.split("@")[1]
    provider = KNOWN.get(domain)
    mx = [] if provider else _mx(domain)
    if not provider:
        for host in mx:
            provider = next((p for suffix, p in KNOWN.items() if host == suffix or host.endswith("." + suffix)), None)
            if provider:
                break
    out = {"email": email_addr, "provider": provider or "other"}
    if provider in OAUTH:
        out |= {"method": "oauth", "label": OAUTH[provider]["label"], "configured": public_clients()[provider]["configured"]}
    else:
        out["method"] = "password"
    servers = SERVERS.get(provider) if provider else None
    if servers:
        out |= {"imap": servers[0], "smtp": servers[1]}
    else:
        ac = _autoconfig(domain)
        if not ac and mx:
            ac = _autoconfig(".".join(mx[0].split(".")[-2:]))  # a domain hosted elsewhere: look up the host's settings
        if ac:
            out |= {"imap": ac["imap"], "smtp": ac["smtp"], "found": ac["name"]}
        else:
            out |= {"imap": (f"imap.{domain}", 993), "smtp": (f"smtp.{domain}", 465, "ssl"), "guessed": True}
    if provider in PASSWORD_HINTS:
        label, note, link = PASSWORD_HINTS[provider]
        out |= {"hint": note, "help": link, "label": out.get("label") or label}
    return out


# OAuth ---------------------------------------------------------------------------------------------------------------
_pending: dict[str, dict] = {}


def start(provider: str, email_addr: str, redirect_uri: str) -> dict:
    c = clients().get(provider) or {}
    if not c.get("client_id"):
        raise ValueError(f"{OAUTH[provider]['label']} sign-in is not set up yet.")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    _pending[state] = {"provider": provider, "email": email_addr, "verifier": verifier, "redirect": redirect_uri,
                       "status": "waiting", "created": time.time()}
    o = OAUTH[provider]
    params = {"client_id": c["client_id"], "redirect_uri": redirect_uri, "response_type": "code", "scope": o["scope"],
              "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "login_hint": email_addr, **o["extra"]}
    return {"state": state, "url": o["auth"] + "?" + urllib.parse.urlencode(params)}


def finish(state: str, code: str | None, error: str | None) -> dict:
    """Called by the browser redirect. Exchanges the code for tokens and returns the signed-in address."""
    p = _pending.get(state)
    if not p or time.time() - p["created"] > 900:
        raise ValueError("This sign-in link has expired. Start again in Vyum.")
    if error or not code:
        p["status"], p["error"] = "error", error or "Sign-in was cancelled."
        raise ValueError(p["error"])
    c = clients()[p["provider"]]
    data = {"client_id": c["client_id"], "code": code, "redirect_uri": p["redirect"], "grant_type": "authorization_code",
            "code_verifier": p["verifier"]}
    if c.get("client_secret"):
        data["client_secret"] = c["client_secret"]
    r = httpx.post(OAUTH[p["provider"]]["token"], data=data, timeout=20)
    if r.status_code != 200:
        p["status"], p["error"] = "error", f"The provider refused the sign-in: {r.text[:200]}"
        raise ValueError(p["error"])
    tok = r.json()
    email_addr = _email_from_id_token(tok.get("id_token")) or p["email"]
    if not tok.get("refresh_token"):
        p["status"], p["error"] = "error", "No long-term access was granted. Remove Vyum's access in your account settings and try again."
        raise ValueError(p["error"])
    keyring.set_password(KEYCHAIN, f"oauth:{email_addr}", json.dumps({"refresh_token": tok["refresh_token"], "provider": p["provider"]}))
    _cache[email_addr] = (tok["access_token"], time.time() + int(tok.get("expires_in", 3000)) - 120)
    p |= {"status": "done", "email": email_addr}
    return {"provider": p["provider"], "email": email_addr}


def poll(state: str) -> dict:
    p = _pending.get(state) or {"status": "unknown"}
    return {k: p.get(k) for k in ("status", "error", "email", "provider")}


def _email_from_id_token(idt: str | None) -> str | None:
    if not idt:
        return None
    try:
        payload = idt.split(".")[1] + "=" * (-len(idt.split(".")[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return (claims.get("email") or claims.get("preferred_username") or "").lower() or None
    except (ValueError, IndexError):
        return None


_cache: dict[str, tuple[str, float]] = {}


def access_token(email_addr: str) -> str:
    """A fresh access token for IMAP/SMTP, refreshed from the Keychain's refresh token when it has expired."""
    tok, exp = _cache.get(email_addr, ("", 0))
    if tok and time.time() < exp:
        return tok
    saved = keyring.get_password(KEYCHAIN, f"oauth:{email_addr}")
    if not saved:
        raise ValueError("Sign in again: the saved sign-in for this account is missing.")
    s = json.loads(saved)
    c = clients().get(s["provider"]) or {}
    data = {"client_id": c.get("client_id", ""), "refresh_token": s["refresh_token"], "grant_type": "refresh_token"}
    if c.get("client_secret"):
        data["client_secret"] = c["client_secret"]
    if s["provider"] == "microsoft":
        data["scope"] = OAUTH["microsoft"]["scope"]
    r = httpx.post(OAUTH[s["provider"]]["token"], data=data, timeout=20)
    if r.status_code != 200:
        raise ValueError("Your sign-in has expired. Sign in again.")
    j = r.json()
    if j.get("refresh_token"):  # some providers rotate refresh tokens
        keyring.set_password(KEYCHAIN, f"oauth:{email_addr}", json.dumps({**s, "refresh_token": j["refresh_token"]}))
    _cache[email_addr] = (j["access_token"], time.time() + int(j.get("expires_in", 3000)) - 120)
    return j["access_token"]


def xoauth2(email_addr: str) -> str:
    return f"user={email_addr}\x01auth=Bearer {access_token(email_addr)}\x01\x01"


def forget(email_addr: str):
    _cache.pop(email_addr, None)
    try:
        keyring.delete_password(KEYCHAIN, f"oauth:{email_addr}")
    except keyring.errors.PasswordDeleteError:
        pass
