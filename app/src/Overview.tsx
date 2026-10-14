import { t } from "./i18n";
import { useEffect, useState } from "react";
import { api, type Overview as Data } from "./api";
import { ago } from "./Office";
import * as I from "./icons";

const gb = (n: number) => (n < 1e6 ? `${Math.max(1, Math.round(n / 1e3))} KB` : n < 1e9 ? `${(n / 1e6).toFixed(0)} MB` : `${(n / 1e9).toFixed(1)} GB`);
const plural = (n: number, w: string) => `${n.toLocaleString()} ${w}${n === 1 ? "" : "s"}`;

export default function Overview({ web, onGo }: {
  web: boolean;
  onGo: (where: "chat" | "office" | "mail" | "projects" | { chat: string } | "newProject") => void;
}) {
  const [d, setD] = useState<Data | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api.overview().then(setD).catch((e) => setError(String(e.message))); }, []);
  if (error) return <div className="scroll"><div className="column"><div className="card error">{error}</div></div></div>;
  if (!d) return <div className="scroll"><div className="column"><div className="status-line"><span className="spinner" />Loading</div></div></div>;
  const max = Math.max(1, ...d.activity_14d);

  return (
    <div className="scroll"><div className="column wide">
      <div className="page-head"><h1>{t("Overview")}</h1></div>
      <div className="stats">
        <Stat label={t("Documents")} value={d.documents} sub={`${plural(d.pages, "page")} · ${plural(d.passages, "passage")}`} />
        <Stat label={t("Questions asked")} value={d.questions} sub={`${plural(d.chats, "chat")} · ${plural(d.projects, "project")}`} />
        <Stat label={t("Office files")} value={d.office.installed ? undefined : "—"} sub={d.office.installed ? "Office engine ready" : "Office engine not installed"} officeCount />
        <Stat label="On this Mac" value={gb(d.storage_bytes)} sub="index, files and chats" />
      </div>

      <div className="ov-grid">
        <section className="card">
          <h3>{t("Activity · last 14 days")}</h3>
          <div className="bars" aria-label="Questions per day">
            {d.activity_14d.map((n, i) => <span key={i} title={`${n} question${n === 1 ? "" : "s"}`} style={{ height: `${Math.max(4, (n / max) * 100)}%` }} className={n ? "" : "zero"} />)}
          </div>
          <div className="muted small">{d.activity_14d.reduce((a, b) => a + b, 0)} questions in the last two weeks</div>
        </section>

        <section className="card">
          <h3>{t("Library by type")}</h3>
          {Object.keys(d.by_type).length === 0 ? <p className="muted">No documents yet.</p> : (
            <div className="type-rows">
              {Object.entries(d.by_type).map(([t, n]) => (
                <div key={t} className="type-row"><span className="ext">{t.toUpperCase()}</span>
                  <span className="track"><span style={{ width: `${(100 * n) / d.documents}%` }} /></span><span className="n">{n}</span></div>
              ))}
            </div>
          )}
          {d.indexing > 0 && <p className="muted small"><span className="spinner" style={{ display: "inline-block", verticalAlign: -2 }} /> {d.indexing} still indexing</p>}
        </section>

        <section className="card">
          <h3>{t("Privacy and status")}</h3>
          <div className="status-rows">
            <Row ok label="Models run on this Mac" detail={`${d.models.generator} · ${d.models.vision} · ${d.models.embeddings}`} />
            <Row ok label="Documents never leave this Mac" detail="indexing, search and answers are local" />
            <Row ok={!web || d.web.running} warn={web} label={web ? "Web search on (Chat mode)" : "Web search off"}
                 detail={web ? `only search words go to SearXNG at ${d.web.searxng_url}${d.web.running ? "" : " (starts on first search)"}` : "nothing is sent anywhere"} />
            <Row ok={d.office.installed} label={d.office.installed ? "Office engine ready" : "Office engine not installed"}
                 detail={d.office.installed ? "no usage analytics, no auto-updater" : "run scripts/build_genoffice.sh"} />
          </div>
        </section>

        <section className="card">
          <h3>{t("Quick actions")}</h3>
          <div className="quick">
            <button className="chip" onClick={() => onGo("chat")}><I.Chat size={15} /> {t("New chat")}</button>
            <button className="chip" onClick={() => onGo("office")}><I.FileIcon size={15} /> {t("Create a document")}</button>
            <button className="chip" onClick={() => onGo("mail")}><I.Mail size={15} /> {t("Write an email")}</button>
            <button className="chip" onClick={() => onGo("newProject")}><I.Folder size={15} /> {t("Open a folder as a project")}</button>
          </div>
        </section>

        <section className="card">
          <h3>{t("Recent documents")}</h3>
          {d.recent_documents.length === 0 ? <p className="muted">Attach files in a chat, or open a folder as a project.</p> : (
            <div className="file-list">{d.recent_documents.map((x) => (
              <div key={x.id} className="file-row"><span className="ext">{(x.ext || "").replace(".", "").toUpperCase()}</span>
                <span className="name">{x.name}</span><span className="meta">{ago(x.added_at)}</span></div>))}</div>
          )}
        </section>

        <section className="card">
          <h3>{t("Recent chats")}</h3>
          {d.recent_chats.length === 0 ? <p className="muted">No chats yet.</p> : (
            <div className="file-list">{d.recent_chats.map((c) => (
              <button key={c.id} className="file-row" onClick={() => onGo({ chat: c.id })}><I.Chat size={14} />
                <span className="name">{c.title}</span><span className="meta">{ago(c.updated_at)}</span></button>))}</div>
          )}
        </section>
      </div>
    </div></div>
  );
}

function Stat({ label, value, sub, officeCount }: { label: string; value?: number | string; sub: string; officeCount?: boolean }) {
  const [n, setN] = useState<number | null>(null);
  useEffect(() => { if (officeCount) api.officeFiles().then((r) => setN(r.files.length)).catch(() => setN(0)); }, [officeCount]);
  const shown = officeCount && value === undefined ? (n ?? "…") : value;
  return (
    <div className="stat card">
      <div className="label">{label}</div>
      <div className="value">{typeof shown === "number" ? shown.toLocaleString() : shown}</div>
      <div className="muted small">{sub}</div>
    </div>
  );
}

function Row({ ok, warn, label, detail }: { ok: boolean; warn?: boolean; label: string; detail: string }) {
  return (
    <div className="status-row">
      <span className={`dot ${ok ? (warn ? "web" : "ok") : ""}`} />
      <div><div>{label}</div><div className="muted small">{detail}</div></div>
    </div>
  );
}
