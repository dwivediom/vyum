import { lang, t, tStatus } from "./i18n";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, openExternal, type MailAccount, type MailDetect, type MailFolder, type MailMsg, type StreamEvent } from "./api";
import { Markdown, Thinking } from "./Messages";
import * as I from "./icons";

const AVATAR = ["#1a73e8", "#e8710a", "#188038", "#d93025", "#9334e6", "#007b83", "#b06000", "#c5221f"];
const color = (s: string) => AVATAR[[...s].reduce((n, c) => n + c.charCodeAt(0), 0) % AVATAR.length];
const initial = (n: string) => (n.trim()[0] || "?").toUpperCase();
const when = (ts: number) => {
  const d = new Date(ts * 1000), now = new Date();
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return d.getFullYear() === now.getFullYear() ? d.toLocaleDateString([], { month: "short", day: "numeric" }) : d.toLocaleDateString();
};
const ROLE_LABEL: Record<string, string> = { inbox: "Inbox", sent: "Sent", drafts: "Drafts", junk: "Spam", trash: "Trash", archive: "Archive", all: "All mail" };
const ROLE_ICON: Record<string, (p: { size?: number }) => JSX.Element> = { inbox: I.Inbox, sent: I.Send, drafts: I.FileIcon, junk: I.Warn, trash: I.Trash, archive: I.Archive, all: I.Mail };

type Compose = { to: string; cc: string; bcc: string; subject: string; body: string; replyTo?: number; files: File[]; min?: boolean; showCc?: boolean };

// sign in: one question first ("what's your email?"), then the right way in for that provider ------------------------
function OAuthSetup({ provider, onSaved }: { provider: string; onSaved: () => void }) {
  const [id, setId] = useState("");
  const [secret, setSecret] = useState("");
  const [err, setErr] = useState("");
  const google = provider === "google";
  return (
    <div className="oauth-setup">
      <p><b>One-time setup:</b> {google ? "Google" : "Microsoft"} only lets apps it knows ask for sign-in. Whoever runs Vyum registers it once:</p>
      {google ? (
        <ol>
          <li>Open <a href="#" onClick={(e) => { e.preventDefault(); openExternal("https://console.cloud.google.com/apis/credentials"); }}>Google Cloud → Credentials</a> and create a project.</li>
          <li>Enable the <b>Gmail API</b>; on the OAuth consent screen add your Gmail address as a test user.</li>
          <li><b>Create credentials → OAuth client ID → Desktop app</b>, then paste the client ID and secret here.</li>
        </ol>
      ) : (
        <ol>
          <li>Open <a href="#" onClick={(e) => { e.preventDefault(); openExternal("https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"); }}>Microsoft Entra → App registrations</a> → New registration.</li>
          <li>Account types: <b>any organisation and personal Microsoft accounts</b>. Redirect URI: platform <b>Mobile and desktop</b>, value <code>http://localhost</code>.</li>
          <li>API permissions → add <b>IMAP.AccessAsUser.All</b> and <b>SMTP.Send</b> (Office 365 Exchange Online). Paste the Application (client) ID here.</li>
        </ol>
      )}
      <input value={id} onChange={(e) => setId(e.target.value)} placeholder="Client ID" aria-label="Client ID" />
      {google && <input value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="Client secret" aria-label="Client secret" />}
      {err && <div className="signin-error">{err}</div>}
      <button className="btn secondary" disabled={!id.trim()} onClick={async () => {
        try { await api.oauthClientsSave({ [provider]: { client_id: id.trim(), ...(google ? { client_secret: secret.trim() } : {}) } }); onSaved(); }
        catch (e) { setErr(String((e as Error).message)); }
      }}>Save</button>
    </div>
  );
}

function SignIn({ onDone, onCancel }: { onDone: () => void; onCancel?: () => void }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [d, setD] = useState<MailDetect | null>(null);
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [usePassword, setUsePassword] = useState(false);
  const [servers, setServers] = useState(false);
  const [srv, setSrv] = useState({ imap_host: "", imap_port: 993, smtp_host: "", smtp_port: 465, smtp_security: "ssl" });
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [waiting, setWaiting] = useState<string | null>(null);

  const look = async (addr = email) => {
    setError(""); setBusy("Looking up your mail provider…");
    try {
      const r = await api.mailDetect(addr);
      if (r.error) { setError(r.error); return; }
      setD(r); setUsePassword(false);
      if (r.imap && r.smtp) setSrv({ imap_host: r.imap[0], imap_port: r.imap[1], smtp_host: r.smtp[0], smtp_port: r.smtp[1], smtp_security: r.smtp[2] });
      setServers(!!r.guessed);
    } catch (e) { setError(String((e as Error).message)); } finally { setBusy(""); }
  };

  const oauth = async () => {
    if (!d?.provider) return;
    setError("");
    try {
      const r = await api.oauthStart(d.provider, d.email || email, name);
      await openExternal(r.url);
      setWaiting(r.state);
      for (let i = 0; i < 400; i++) {
        await new Promise((res) => setTimeout(res, 1500));
        const p = await api.oauthPoll(r.state);
        if (p.status === "done") { setWaiting(null); onDone(); return; }
        if (p.status === "error") { setWaiting(null); setError(p.error || "Sign-in did not work."); return; }
      }
      setWaiting(null); setError("Sign-in timed out. Try again.");
    } catch (e) { setWaiting(null); setError(String((e as Error).message)); }
  };

  const signInWithPassword = async () => {
    setBusy("Checking your sign-in…"); setError("");
    try {
      await api.mailAddAccount({ email: d?.email || email, password, provider: d?.provider === "google" ? "gmail" : (d?.provider || "other"), name, ...srv });
      setPassword(""); onDone();
    } catch (e) { setError(String((e as Error).message)); } finally { setBusy(""); }
  };

  const brand = d?.provider === "google" ? "google" : d?.provider === "microsoft" ? "microsoft" : null;
  const oauthReady = d?.method === "oauth" && d.configured;
  return (
    <div className="mail-signin">
      <div className="signin-card">
        <div className="signin-head"><I.Logo size={36} /><div><h1>{t("Add your email")}</h1>
          <p className="muted">Gmail, Outlook, Zoho, iCloud, Yahoo, your company's mail — any account. Your mail stays on this Mac.</p></div></div>

        <form className="email-step" onSubmit={(e) => { e.preventDefault(); look(); }}>
          <input type="email" value={email} onChange={(e) => { setEmail(e.target.value); setD(null); }} placeholder="you@example.com" autoFocus aria-label="Email address" />
          {!d && <button className="btn primary mail" disabled={!email.includes("@") || !!busy}>{busy ? <span className="spinner" /> : t("Continue")}</button>}
        </form>

        {d && (
          <>
            <label className="field">Your name <span className="muted">(shown to people you email)</span><input value={name} onChange={(e) => setName(e.target.value)} /></label>
            {d.method === "oauth" && !usePassword && (
              oauthReady ? (
                waiting ? <div className="signin-wait"><span className="spinner" /> Finish signing in in your browser. This window updates by itself.</div>
                  : <button className={`btn-oauth ${brand}`} onClick={oauth}>
                      <span className="mark">{brand === "google" ? "G" : "⊞"}</span> Continue with {d.label}</button>
              ) : <OAuthSetup provider={d.provider!} onSaved={() => look(d.email)} />
            )}
            {(d.method === "password" || usePassword) && (
              <>
                {d.found && <div className="muted small">Found the settings for {d.found}.</div>}
                <label className="field">Password
                  <div className="pw"><input type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="off" autoFocus />
                    <button type="button" className="ghost" onClick={() => setShow(!show)}>{show ? "Hide" : "Show"}</button></div>
                </label>
                {d.hint && <div className="signin-help"><I.Shield size={15} /><span>{d.hint} {d.help && <a href="#" onClick={(e) => { e.preventDefault(); openExternal(d.help!); }}>Open that page</a>}</span></div>}
                <button type="button" className="link-btn" onClick={() => setServers(!servers)}>{servers ? "Hide server settings" : "Server settings"}</button>
                {servers && (
                  <div className="custom-grid">
                    <label className="field">Incoming (IMAP) server<input value={srv.imap_host} onChange={(e) => setSrv({ ...srv, imap_host: e.target.value })} /></label>
                    <label className="field">Port<input type="number" value={srv.imap_port} onChange={(e) => setSrv({ ...srv, imap_port: Number(e.target.value) })} /></label>
                    <label className="field">Outgoing (SMTP) server<input value={srv.smtp_host} onChange={(e) => setSrv({ ...srv, smtp_host: e.target.value })} /></label>
                    <label className="field">Port<input type="number" value={srv.smtp_port} onChange={(e) => setSrv({ ...srv, smtp_port: Number(e.target.value), smtp_security: Number(e.target.value) === 587 ? "starttls" : "ssl" })} /></label>
                  </div>
                )}
                {d.guessed && <div className="muted small">Vyum guessed these server names from your address; your mail provider's help pages list the right ones.</div>}
                <button className="btn primary mail" disabled={!password || !!busy} onClick={signInWithPassword}>{busy ? <><span className="spinner" /> {busy}</> : "Sign in"}</button>
              </>
            )}
            {d.provider === "google" && !usePassword && <button className="link-btn" onClick={() => setUsePassword(true)}>Use an app password instead</button>}
          </>
        )}
        {error && <div className="signin-error">{error}</div>}
        <div className="actions">
          {d && <button className="btn secondary" onClick={() => { setD(null); setError(""); }}>Use a different address</button>}
          {onCancel && <button className="btn secondary" onClick={onCancel}>Cancel</button>}
        </div>
      </div>
    </div>
  );
}

// message body ------------------------------------------------------------------------------------------------------
function Body({ m, images, onShowImages }: { m: MailMsg; images: boolean; onShowImages: () => void }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [doc, setDoc] = useState<{ html: string; blocked: number } | null>(null);
  useEffect(() => { api.mailHtml(m.id, images).then(setDoc).catch(() => setDoc({ html: `<pre>${m.text || ""}</pre>`, blocked: 0 })); }, [m.id, images]);
  const fit = () => {
    const d = frame.current?.contentDocument;
    if (d && frame.current) frame.current.style.height = `${Math.min(6000, d.documentElement.scrollHeight + 8)}px`;
  };
  return (
    <>
      {doc && doc.blocked > 0 && !images && (
        <div className="img-banner"><I.Shield size={14} /> {doc.blocked} image{doc.blocked > 1 ? "s" : ""} hidden to protect your privacy.
          <button className="ghost" onClick={onShowImages}>Show images</button></div>
      )}
      {/* no allow-scripts: the message cannot run code; same origin only so the frame can be sized to its content */}
      <iframe ref={frame} className="mail-frame" title="Message" sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
              srcDoc={doc?.html || ""} onLoad={() => { fit(); window.setTimeout(fit, 400); }} />
    </>
  );
}

// the app -----------------------------------------------------------------------------------------------------------
export default function Mail({ think }: { docs?: unknown; think: boolean; onAskMail?: () => void }) {
  const [accounts, setAccounts] = useState<MailAccount[] | null>(null);
  const [aid, setAid] = useState<string>("");
  const [adding, setAdding] = useState(false);
  const [folders, setFolders] = useState<MailFolder[]>([]);
  const [folder, setFolder] = useState<string>("INBOX");
  const [starred, setStarred] = useState(false);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [list, setList] = useState<{ total: number; messages: MailMsg[] }>({ total: 0, messages: [] });
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [open, setOpen] = useState<MailMsg | null>(null);
  const [thread, setThread] = useState<MailMsg[]>([]);
  const [images, setImages] = useState(false);
  const [compose, setCompose] = useState<Compose | null>(null);
  const [panel, setPanel] = useState(true);
  const [toast, setToast] = useState("");
  const acct = accounts?.find((a) => a.id === aid);

  const loadAccounts = useCallback(async () => {
    const a = await api.mailAccounts().catch(() => []);
    setAccounts(a);
    if (a.length && !a.some((x) => x.id === aid)) setAid(a[0].id);
    return a;
  }, [aid]);
  useEffect(() => { loadAccounts(); }, []);

  const loadList = useCallback(async () => {
    if (!aid) return;
    const r = await api.mailMessages(aid, { folder: starred ? undefined : folder, q: query || (starred ? "is:starred" : ""), page });
    setList({ total: r.total, messages: r.messages });
    setFolders(await api.mailFolders(aid));
  }, [aid, folder, starred, query, page]);
  useEffect(() => { loadList(); }, [loadList]);

  // keep the list fresh while the account syncs, and every 30 s after
  useEffect(() => {
    if (!aid) return;
    const t = window.setInterval(async () => { const a = await loadAccounts(); await loadList(); if (a.find((x) => x.id === aid)?.status?.state !== "syncing") window.clearInterval(t); }, 3000);
    const slow = window.setInterval(loadList, 30000);
    return () => { window.clearInterval(t); window.clearInterval(slow); };
  }, [aid]);

  const flash = (t: string) => { setToast(t); window.setTimeout(() => setToast(""), 2500); };
  const act = async (ids: number[], action: string, label: string) => {
    if (!ids.length) return;
    try { await api.mailAct(aid, ids, action); flash(label); } catch (e) { flash(String((e as Error).message)); }
    setSel(new Set());
    if (open && ["archive", "delete", "spam"].includes(action)) setOpen(null);
    await loadList();
    if (open && !["archive", "delete", "spam"].includes(action)) setThread(await api.mailThread(aid, open.thread));
  };
  const openThread = async (m: MailMsg) => {
    setOpen(m); setImages(false);
    const t = await api.mailThread(aid, m.thread);
    setThread(t);
    const unread = t.filter((x) => !x.seen).map((x) => x.id);
    if (unread.length) { api.mailAct(aid, unread, "read").then(loadList); }
  };

  const folderList = useMemo(() => folders.filter((f) => f.role !== "all" || !folders.some((x) => x.role === "archive")), [folders]);

  if (accounts === null) return <div className="scroll"><div className="column"><div className="status-line"><span className="spinner" />Loading…</div></div></div>;
  if (!accounts.length || adding) return <SignIn onDone={async () => { setAdding(false); const a = await loadAccounts(); setAid(a[a.length - 1].id); }} onCancel={accounts.length ? () => setAdding(false) : undefined} />;

  const reply = (m: MailMsg, all = false) => {
    const others = all ? [...m.to, ...m.cc].filter((x) => x.email !== acct?.email).map((x) => x.email) : [];
    setCompose({ to: m.from_addr, cc: others.join(", "), bcc: "", showCc: others.length > 0, files: [],
                 subject: m.subject.toLowerCase().startsWith("re:") ? m.subject : `Re: ${m.subject}`, replyTo: m.id,
                 body: `\n\nOn ${new Date(m.date_ts * 1000).toLocaleString()}, ${m.from_name || m.from_addr} wrote:\n${(m.text || "").split("\n").map((l) => `> ${l}`).join("\n")}` });
  };
  const forward = (m: MailMsg) => setCompose({ to: "", cc: "", bcc: "", files: [], subject: `Fwd: ${m.subject}`,
    body: `\n\n---------- Forwarded message ----------\nFrom: ${m.from_name} <${m.from_addr}>\nDate: ${new Date(m.date_ts * 1000).toLocaleString()}\nSubject: ${m.subject}\n\n${m.text || ""}` });
  const allSel = list.messages.length > 0 && list.messages.every((m) => sel.has(m.id));
  const selIds = [...sel];

  return (
    <div className={`gmail ${panel ? "with-panel" : ""}`}>
      <header className="gm-top">
        <div className="gm-brand"><I.Mail size={22} /><span>{t("Mail")}</span></div>
        <form className="gm-search" onSubmit={(e) => { e.preventDefault(); setQuery(q); setPage(0); setOpen(null); }}>
          <I.Search size={18} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search mail   (try from:anna  is:unread  has:attachment)" aria-label="Search mail" />
          {query && <button type="button" className="ghost" onClick={() => { setQ(""); setQuery(""); }}>Clear</button>}
        </form>
        <span className="gm-sync">{acct?.status?.state === "syncing" ? <><span className="spinner" /> {acct.status.step || "Syncing"}</> :
          acct?.status?.state === "error" ? <span className="error-text" title={acct.status.error}>Sync problem</span> : null}</span>
        <button className="icon-btn" title="Check for new mail" onClick={() => { api.mailSync(aid); loadAccounts(); }}><I.Refresh size={17} /></button>
        <button className={`pill ${panel ? "violet" : "plain"}`} onClick={() => setPanel(!panel)}><I.Sparkle size={14} /> {t("Ask Vyum")}</button>
        <AccountMenu accounts={accounts} aid={aid} setAid={(id) => { setAid(id); setOpen(null); setFolder("INBOX"); }}
                     onAdd={() => setAdding(true)} onRemove={async (id) => { await api.mailRemoveAccount(id); const a = await loadAccounts(); if (!a.length) setAid(""); }} />
      </header>

      <nav className="gm-nav">
        <button className="gm-compose" onClick={() => setCompose({ to: "", cc: "", bcc: "", subject: "", body: "", files: [] })}><I.Pencil size={18} /> {t("Compose")}</button>
        {folderList.map((f) => {
          const Icon = ROLE_ICON[f.role || ""] || I.Folder;
          const on = !starred && !query && folder === f.name;
          return (
            <button key={f.name} className={`gm-folder ${on ? "on" : ""}`} onClick={() => { setStarred(false); setQuery(""); setQ(""); setFolder(f.name); setPage(0); setOpen(null); }}>
              <Icon size={17} /><span className="label">{t(ROLE_LABEL[f.role || ""] || f.name)}</span>
              {f.unread > 0 && f.role !== "sent" && f.role !== "trash" && <span className="count">{f.unread}</span>}
            </button>
          );
        }).flatMap((el, i) => i === 0 ? [el, (
          <button key="__starred" className={`gm-folder ${starred ? "on" : ""}`} onClick={() => { setStarred(true); setQuery(""); setPage(0); setOpen(null); }}>
            <I.Star size={17} /><span className="label">{t("Starred")}</span></button>)] : [el])}
      </nav>

      <main className="gm-main">
        {!open ? (
          <>
            <div className="gm-toolbar">
              <input type="checkbox" checked={allSel} onChange={() => setSel(allSel ? new Set() : new Set(list.messages.map((m) => m.id)))} aria-label="Select all" />
              {sel.size > 0 ? (
                <>
                  <button className="icon-btn" title="Archive" onClick={() => act(selIds, "archive", "Archived")}><I.Archive size={17} /></button>
                  <button className="icon-btn" title="Report spam" onClick={() => act(selIds, "spam", "Moved to Spam")}><I.Warn size={17} /></button>
                  <button className="icon-btn" title="Delete" onClick={() => act(selIds, "delete", "Moved to Trash")}><I.Trash size={17} /></button>
                  <button className="icon-btn" title="Mark as read" onClick={() => act(selIds, "read", "Marked as read")}><I.MailOpen size={17} /></button>
                  <button className="icon-btn" title="Mark as unread" onClick={() => act(selIds, "unread", "Marked as unread")}><I.Mail size={17} /></button>
                  <span className="muted small">{sel.size} selected</span>
                </>
              ) : <button className="icon-btn" title="Refresh" onClick={loadList}><I.Refresh size={17} /></button>}
              <span className="spacer" />
              <span className="muted small">{list.total ? `${page * 50 + 1}–${Math.min(list.total, page * 50 + 50)} of ${list.total.toLocaleString()}` : ""}</span>
              <button className="icon-btn" disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="Newer"><I.Chevron size={16} className="flip" /></button>
              <button className="icon-btn" disabled={(page + 1) * 50 >= list.total} onClick={() => setPage(page + 1)} aria-label="Older"><I.Chevron size={16} /></button>
            </div>
            <div className="gm-list">
              {list.messages.length === 0 && (
                <div className="gm-empty">{acct?.status?.state === "syncing" ? <><span className="spinner" /> Downloading your mail…</> : query ? `No messages match “${query}”.` : "Nothing here."}</div>
              )}
              {list.messages.map((m) => (
                <div key={m.id} className={`gm-row ${m.seen ? "" : "unread"} ${sel.has(m.id) ? "sel" : ""}`} onClick={() => openThread(m)}>
                  <input type="checkbox" checked={sel.has(m.id)} onClick={(e) => e.stopPropagation()}
                         onChange={() => { const s = new Set(sel); s.has(m.id) ? s.delete(m.id) : s.add(m.id); setSel(s); }} aria-label="Select" />
                  <button className={`star ${m.flagged ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); act([m.id], m.flagged ? "unstar" : "star", m.flagged ? "Unstarred" : "Starred"); }} aria-label="Star"><I.Star size={17} /></button>
                  <span className="from">{m.from_name || m.from_addr}{(m.count || 1) > 1 && <span className="n"> {m.count}</span>}</span>
                  <span className="subj"><b>{m.subject}</b><span className="snip"> — {m.snippet}</span></span>
                  {m.has_att && <I.Paperclip size={14} />}
                  <span className="date">{when(m.date_ts)}</span>
                  <span className="hover-acts" onClick={(e) => e.stopPropagation()}>
                    <button className="icon-btn" title="Archive" onClick={() => act([m.id], "archive", "Archived")}><I.Archive size={16} /></button>
                    <button className="icon-btn" title="Delete" onClick={() => act([m.id], "delete", "Moved to Trash")}><I.Trash size={16} /></button>
                    <button className="icon-btn" title={m.seen ? "Mark as unread" : "Mark as read"} onClick={() => act([m.id], m.seen ? "unread" : "read", m.seen ? "Marked as unread" : "Marked as read")}>{m.seen ? <I.Mail size={16} /> : <I.MailOpen size={16} />}</button>
                  </span>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="gm-thread">
            <div className="gm-toolbar">
              <button className="icon-btn" title="Back" onClick={() => setOpen(null)}><I.Chevron size={18} className="flip" /></button>
              <button className="icon-btn" title="Archive" onClick={() => act(thread.map((m) => m.id), "archive", "Archived")}><I.Archive size={17} /></button>
              <button className="icon-btn" title="Report spam" onClick={() => act(thread.map((m) => m.id), "spam", "Moved to Spam")}><I.Warn size={17} /></button>
              <button className="icon-btn" title="Delete" onClick={() => act(thread.map((m) => m.id), "delete", "Moved to Trash")}><I.Trash size={17} /></button>
              <button className="icon-btn" title="Mark as unread" onClick={() => { act([thread[thread.length - 1].id], "unread", "Marked as unread"); setOpen(null); }}><I.Mail size={17} /></button>
            </div>
            <div className="gm-thread-body">
              <h2 className="gm-subject">{open.subject} <span className="chip">{ROLE_LABEL[folders.find((f) => f.name === open.folder)?.role || ""] || open.folder}</span></h2>
              <ThreadSummary aid={aid} thread={open.thread} think={think} />
              {thread.map((m, i) => (
                <article key={m.id} className="gm-msg">
                  <div className="gm-msg-head">
                    <span className="avatar" style={{ background: color(m.from_addr) }}>{initial(m.from_name || m.from_addr)}</span>
                    <div className="who"><b>{m.from_name || m.from_addr}</b> <span className="muted small">&lt;{m.from_addr}&gt;</span>
                      <div className="muted small">to {m.to.map((x) => (x.email === acct?.email ? "me" : x.name || x.email)).join(", ")}{m.cc.length ? `, cc ${m.cc.map((x) => x.name || x.email).join(", ")}` : ""}</div></div>
                    <span className="spacer" />
                    <span className="muted small">{new Date(m.date_ts * 1000).toLocaleString()}</span>
                    <button className={`star ${m.flagged ? "on" : ""}`} onClick={() => act([m.id], m.flagged ? "unstar" : "star", m.flagged ? "Unstarred" : "Starred")} aria-label="Star"><I.Star size={17} /></button>
                    <button className="icon-btn" title="Reply" onClick={() => reply(m)}><I.Reply size={17} /></button>
                  </div>
                  {i === thread.length - 1 || thread.length < 3 ? <Body m={m} images={images} onShowImages={() => setImages(true)} /> : <p className="muted gm-collapsed">{m.snippet}</p>}
                  {(m.attachments || []).filter((x) => !x.inline).length > 0 && (
                    <div className="gm-atts">
                      {(m.attachments || []).filter((x) => !x.inline).map((x) => (
                        <a key={x.index} className="att" href={api.mailAttachmentUrl(m.id, x.index)} download={x.name}>
                          <I.Paperclip size={14} /><span className="nm">{x.name}</span><span className="muted small">{Math.max(1, Math.round(x.size / 1024))} KB</span></a>
                      ))}
                    </div>
                  )}
                </article>
              ))}
              <div className="gm-reply-bar">
                <button className="btn secondary" onClick={() => reply(thread[thread.length - 1])}><I.Reply size={15} /> {t("Reply")}</button>
                {thread[thread.length - 1] && [...thread[thread.length - 1].to, ...thread[thread.length - 1].cc].length > 1 &&
                  <button className="btn secondary" onClick={() => reply(thread[thread.length - 1], true)}><I.Reply size={15} /> {t("Reply all")}</button>}
                <button className="btn secondary" onClick={() => forward(thread[thread.length - 1])}><I.Forward size={15} /> {t("Forward")}</button>
              </div>
            </div>
          </div>
        )}
      </main>

      {panel && <AskPanel aid={aid} think={think} open={open} onOpen={async (id, key) => { const t = await api.mailThread(aid, key); const m = t.find((x) => x.id === id) || t[0]; if (m) openThread(m); }}
                          onDraftReply={(text) => open && setCompose((c) => ({ ...(c || { to: open.from_addr, cc: "", bcc: "", files: [], subject: open.subject.toLowerCase().startsWith("re:") ? open.subject : `Re: ${open.subject}`, replyTo: thread[thread.length - 1]?.id ?? open.id, body: "" }), body: text }))} />}
      {compose && <ComposeWindow aid={aid} c={compose} setC={setCompose} think={think} onSent={() => { flash("Message sent"); setCompose(null); window.setTimeout(loadList, 2500); }} />}
      {toast && <div className="gm-toast">{toast}</div>}
    </div>
  );
}

function AccountMenu({ accounts, aid, setAid, onAdd, onRemove }: { accounts: MailAccount[]; aid: string; setAid: (id: string) => void; onAdd: () => void; onRemove: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const me = accounts.find((a) => a.id === aid);
  return (
    <div style={{ position: "relative" }}>
      <button className="avatar lg" style={{ background: color(me?.email || "?") }} onClick={() => setOpen(!open)} title={me?.email}>{initial(me?.name || me?.email || "?")}</button>
      {open && (
        <div className="popover down menu-pop right acct-menu" onMouseLeave={() => setOpen(false)}>
          {accounts.map((a) => (
            <div key={a.id} className={`menu-item ${a.id === aid ? "on" : ""}`} onClick={() => { setAid(a.id); setOpen(false); }}>
              <span className="avatar" style={{ background: color(a.email) }}>{initial(a.name || a.email)}</span>
              <span><span>{a.name || a.email}</span><span className="desc">{a.email}</span></span>
            </div>
          ))}
          <div className="menu-sep" />
          <button className="menu-item" onClick={() => { onAdd(); setOpen(false); }}><I.Plus size={15} /> Add another account</button>
          {me && <button className="menu-item danger" onClick={() => { if (window.confirm(`Sign out of ${me.email}? Its downloaded mail is removed from this Mac; nothing is deleted on the server.`)) onRemove(me.id); setOpen(false); }}>Sign out of {me.email}</button>}
        </div>
      )}
    </div>
  );
}

function useAssist() {
  const [live, setLive] = useState<{ status: string; thinking: string; thinkingSeconds?: number; draft: string } | null>(null);
  const runRef = useRef("");
  const stop = () => { if (runRef.current) { api.stop(runRef.current); setLive((l) => l && { ...l, status: "Stopping" }); } };
  const run = async (body: Record<string, unknown>) => {
    setLive({ status: "Starting", thinking: "", draft: "" });
    let result: any = null;
    runRef.current = crypto.randomUUID();
    try {
      await api.mailAssist({ ...body, run_id: runRef.current }, (ev: StreamEvent) => {
        if (ev.type === "status") setLive((l) => l && { ...l, status: ev.text });
        else if (ev.type === "thinking") setLive((l) => l && { ...l, thinking: l.thinking + ev.text, status: "Thinking" });
        else if (ev.type === "thinking_done") setLive((l) => l && { ...l, thinkingSeconds: ev.seconds });
        else if (ev.type === "draft") setLive((l) => l && { ...l, draft: l.draft + ev.text });
        else if (ev.type === "result") result = ev.result;
        else if (ev.type === "stopped") result = { stopped: true, text: "" };
      });
    } finally { setLive(null); runRef.current = ""; }
    return result;
  };
  useEffect(() => {
    if (!live) return;
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); stop(); } };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [!!live]);
  return { live, run, stop };
}

function ThreadSummary({ aid, thread, think }: { aid: string; thread: string; think: boolean }) {
  const { live, run } = useAssist();
  const [text, setText] = useState("");
  useEffect(() => setText(""), [thread]);
  if (text) return <div className="gm-summary"><div className="hd"><I.Sparkle size={14} /> Summary</div><Markdown text={text} citations={[]} onCite={() => undefined} /></div>;
  if (live) return <div className="gm-summary"><div className="status-line"><span className="spinner" />{live.status}</div>{live.draft && <div className="muted small">{live.draft}</div>}</div>;
  return <button className="pill violet" style={{ marginBottom: 10 }} onClick={async () => { const r = await run({ account: aid, mode: "summarize", thread, think, ui_lang: lang }); if (r?.text) setText(r.text); }}><I.Sparkle size={14} /> {t("Summarize this conversation")}</button>;
}

function AskPanel({ aid, think, open, onOpen, onDraftReply }: {
  aid: string; think: boolean; open: MailMsg | null; onOpen: (id: number, thread: string) => void; onDraftReply: (text: string) => void;
}) {
  const { live, run, stop } = useAssist();
  const [msgs, setMsgs] = useState<{ role: "user" | "ai"; text: string; refs?: any[] }[]>([]);
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs.length, live?.status]);
  const ask = async (question: string) => {
    if (!question.trim() || live) return;
    setText("");
    setMsgs((m) => [...m, { role: "user", text: question }]);
    const r = await run({ account: aid, mode: "ask", question, think });
    setMsgs((m) => [...m, { role: "ai", text: r?.stopped ? t("Stopped") : r?.text || r?.error || "No answer came back.", refs: r?.refs || [] }]);
  };
  const draft = async () => {
    if (!open || live) return;
    const instructions = text.trim();
    setText("");
    setMsgs((m) => [...m, { role: "user", text: instructions ? `Draft a reply: ${instructions}` : "Draft a reply" }]);
    const r = await run({ account: aid, mode: "reply", thread: open.thread, instructions, think });
    if (r?.stopped) setMsgs((m) => [...m, { role: "ai", text: t("Stopped") }]);
    else if (r?.text) { onDraftReply(r.text); setMsgs((m) => [...m, { role: "ai", text: "I put a draft reply in the compose window. Change anything before you send it." }]); }
  };
  return (
    <aside className="gm-panel">
      <div className="gm-panel-head"><I.Logo size={20} /> Ask Vyum <span className="muted small">· reads your mail on this Mac</span></div>
      <div className="gm-panel-body">
        {msgs.length === 0 && (
          <div className="examples">
            {["What needs my reply?", "Summarize today's emails", "Any invoices or payments due?", "What did my manager ask for this week?"].map((x) =>
              <button key={x} className="example plain" onClick={() => ask(t(x))}>{t(x)}</button>)}
            {open && <button className="example plain strong" onClick={draft}>Draft a reply to this email</button>}
          </div>
        )}
        {msgs.map((m, i) => m.role === "user" ? <div key={i} className="dc-user">{m.text}</div> : (
          <div key={i} className="dc-ai">
            <Markdown text={m.text.replace(/\s*\[(\d+)\]/g, "")} citations={[]} onCite={() => undefined} />
            {m.refs && m.refs.length > 0 && (
              <div className="gm-refs">{m.refs.filter((r) => m.text.includes(`[${r.n}]`)).slice(0, 5).map((r) => (
                <button key={r.n} className="source-chip" onClick={() => onOpen(r.id, r.thread)}><span className="n">{r.n}</span><span className="t">{r.from} · {r.subject}</span></button>
              ))}</div>
            )}
          </div>
        ))}
        {live && <div className="dc-ai">{(live.thinking || live.thinkingSeconds !== undefined) && <Thinking text={live.thinking} seconds={live.thinkingSeconds} live />}<div className="status-line"><span className="spinner" />{tStatus(live.status)}</div></div>}
        <div ref={end} />
      </div>
      <div className="doc-chat-input">
        <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder={open ? "Ask about your mail, or say how to reply…" : "Ask anything about your mail…"}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(text); } }} />
        <div className="row">
          {open && <button className="tool" disabled={!!live} onClick={draft}><I.Reply size={14} /> {t("Draft reply")}</button>}
          <span className="spacer" />
          {live
            ? <button className="send stop" aria-label="Stop" title={`${t("Stop")} (Esc)`} onClick={stop}><I.Stop size={12} /></button>
            : <button className="send" aria-label="Ask" disabled={!text.trim()} onClick={() => ask(text)}><I.ArrowUp size={16} /></button>}
        </div>
      </div>
    </aside>
  );
}

function ComposeWindow({ aid, c, setC, think, onSent }: { aid: string; c: Compose; setC: (c: Compose | null) => void; think: boolean; onSent: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [help, setHelp] = useState(false);
  const [prompt, setPrompt] = useState("");
  const { live, run } = useAssist();
  const file = useRef<HTMLInputElement>(null);
  const up = (patch: Partial<Compose>) => setC({ ...c, ...patch });
  const send = async () => {
    setBusy(true); setError("");
    const f = new FormData();
    f.set("to", c.to); f.set("cc", c.cc); f.set("bcc", c.bcc); f.set("subject", c.subject); f.set("text", c.body);
    if (c.replyTo) f.set("reply_to_id", String(c.replyTo));
    c.files.forEach((x) => f.append("files", x));
    try { await api.mailSend(aid, f); onSent(); } catch (e) { setError(String((e as Error).message)); } finally { setBusy(false); }
  };
  const write = async () => {
    if (!prompt.trim()) return;
    const r = await run({ account: aid, mode: c.replyTo ? "write" : "write", instructions: prompt, think });
    if (r?.text) up({ body: r.text + (c.body.startsWith("\n\nOn ") ? c.body : ""), subject: c.subject || r.subject || "" });
    setHelp(false); setPrompt("");
  };
  if (c.min) return <div className="gm-compose-min" onClick={() => up({ min: false })}>{c.subject || "New Message"}<button className="x" onClick={(e) => { e.stopPropagation(); setC(null); }}>×</button></div>;
  return (
    <div className="gm-compose-win">
      <div className="hd"><span>{c.replyTo ? "Reply" : "New Message"}</span><span className="spacer" />
        <button onClick={() => up({ min: true })} aria-label="Minimize">–</button><button onClick={() => setC(null)} aria-label="Close">×</button></div>
      <div className="fld"><span>To</span><input value={c.to} onChange={(e) => up({ to: e.target.value })} autoFocus={!c.to} />
        {!c.showCc && <button className="ghost small" onClick={() => up({ showCc: true })}>Cc Bcc</button>}</div>
      {c.showCc && <><div className="fld"><span>Cc</span><input value={c.cc} onChange={(e) => up({ cc: e.target.value })} /></div>
        <div className="fld"><span>Bcc</span><input value={c.bcc} onChange={(e) => up({ bcc: e.target.value })} /></div></>}
      <div className="fld"><input className="subject" value={c.subject} onChange={(e) => up({ subject: e.target.value })} placeholder="Subject" /></div>
      {help && (
        <div className="help-write">
          <input value={prompt} onChange={(e) => setPrompt(e.target.value)} autoFocus placeholder="What should it say? e.g. ask Anna to move our call to Friday"
                 onKeyDown={(e) => { if (e.key === "Enter") write(); }} />
          <button className="btn primary sm mail" disabled={!prompt.trim() || !!live} onClick={write}>{live ? "Writing…" : "Write"}</button>
        </div>
      )}
      <textarea className="body" value={live ? live.draft || "…" : c.body} onChange={(e) => up({ body: e.target.value })} />
      {c.files.length > 0 && <div className="gm-atts">{c.files.map((x, i) => (
        <span key={i} className="att"><I.Paperclip size={13} /><span className="nm">{x.name}</span>
          <button className="x" onClick={() => up({ files: c.files.filter((_, j) => j !== i) })}>×</button></span>))}</div>}
      {error && <div className="signin-error">{error}</div>}
      <div className="ft">
        <button className="btn primary mail" disabled={busy || !c.to.trim()} onClick={send}>{busy ? "Sending…" : t("Send")}</button>
        <button className="icon-btn" title="Attach files" onClick={() => file.current?.click()}><I.Paperclip size={17} /></button>
        <button className="pill violet" onClick={() => setHelp(!help)}><I.Sparkle size={14} /> {t("Help me write")}</button>
        <input ref={file} type="file" multiple hidden onChange={(e) => { if (e.target.files) up({ files: [...c.files, ...Array.from(e.target.files)] }); e.target.value = ""; }} />
        <span className="spacer" />
        <button className="icon-btn" title="Discard" onClick={() => setC(null)}><I.Trash size={16} /></button>
      </div>
    </div>
  );
}
