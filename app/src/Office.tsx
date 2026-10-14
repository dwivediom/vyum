import { t } from "./i18n";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  api, inTauri, type Doc, type OfficeFile, type OfficeKind, type OfficeResult, type OfficeStatus, type StreamEvent,
} from "./api";
import { Thinking } from "./Messages";
import { DocChat, DocHeader } from "./Workspace";
import * as I from "./icons";

/** The six file types Vyum can write, as the Office screen shows them. */
export const KINDS: { kind: OfficeKind; label: string; ext: string; tab: string; badge: string; color: string }[] = [
  { kind: "docx", label: "AI Docs", ext: ".docx", tab: "Docs", badge: "W", color: "#2B6BE6" },
  { kind: "xlsx", label: "AI Sheets", ext: ".xlsx", tab: "Sheets", badge: "X", color: "#2E9E5B" },
  { kind: "pptx", label: "AI Slides", ext: ".pptx", tab: "Slides", badge: "P", color: "#D9502C" },
  { kind: "md", label: "AI Markdown", ext: ".md", tab: "Markdown", badge: "M↓", color: "#8B5CF6" },
  { kind: "html", label: "AI HTML", ext: ".html", tab: "HTML", badge: "</>", color: "#0E9AA7" },
  { kind: "pdf", label: "AI PDF", ext: ".pdf", tab: "PDF", badge: "PDF", color: "#E5484D" },
];
const kindInfo = (k: string) => KINDS.find((x) => x.kind === k) || KINDS[0];

const IDEAS: Record<OfficeKind, string[]> = {
  docx: ["A one-page project proposal for a new customer portal", "A polite late-payment reminder letter", "Meeting minutes from my notes"],
  xlsx: ["A monthly budget with planned vs actual and totals", "A sales tracker by region and quarter with growth %", "A project timeline with owners and due dates"],
  pptx: ["A 6-slide pitch for our product", "A 5-slide quarterly business review", "A training deck on information security basics"],
  md: ["A README for a small Python tool", "Weekly sync meeting notes template", "A checklist for onboarding a new employee"],
  html: ["A landing page announcing our new opening hours", "An event invitation page", "A simple FAQ page"],
  pdf: ["A one-page company policy on remote work", "A printable invoice template", "A certificate of completion"],
};
const EDIT_IDEAS: Record<OfficeKind, string[]> = {
  docx: ["Fix spelling and grammar", "Add a short executive summary at the top", "Make it more concise", "Add a footer with page numbers"],
  xlsx: ["Add a totals row", "Add a column with the % change", "Sort by the largest amount"],
  pptx: ["Add a closing slide with next steps", "Shorten every bullet", "Add speaker notes to every slide"],
  md: ["Add a table of contents", "Make the tone friendlier", "Fix spelling and grammar"],
  html: ["Change the heading text", "Add a contact section", "Make the text more concise"],
  pdf: ["Remove page 1", "Rotate all pages 90°", "Convert to Word", "Extract pages 1-2 as a new PDF"],
};

export const ago = (ts?: number | null) => {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
};
const bytes = (n?: number) => (n === undefined ? "" : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`);

export function KindIcon({ kind, size = 34 }: { kind: string; size?: number }) {
  const k = kindInfo(kind);
  return <span className="kind-icon" style={{ background: k.color, width: size, height: size, fontSize: size * (k.badge.length > 2 ? 0.3 : 0.42) }}>{k.badge}</span>;
}

type Live = { status: string; thinking: string; thinkingSeconds?: number; draft: string };

/** Streams a create/edit call into live status, thinking and draft text. Resolves with the result. */
async function runStream(start: (onEvent: (e: StreamEvent) => void) => Promise<void>, setLive: (f: (l: Live) => Live) => void) {
  let result: OfficeResult | { kind: "error"; error: string } | null = null;
  await start((ev) => {
    if (ev.type === "status") setLive((l) => ({ ...l, status: ev.text }));
    else if (ev.type === "thinking") setLive((l) => ({ ...l, thinking: l.thinking + ev.text, status: "Thinking" }));
    else if (ev.type === "thinking_done") setLive((l) => ({ ...l, thinkingSeconds: ev.seconds }));
    else if (ev.type === "draft") setLive((l) => ({ ...l, draft: l.draft + ev.text }));
    else if (ev.type === "result") result = ev.result as OfficeResult;
  });
  return result as OfficeResult | { kind: "error"; error: string } | null;
}

// home -------------------------------------------------------------------------------------------------------------
export function OfficeHome({ onOpen, docs, think }: { onOpen: (f: OfficeFile) => void; docs: Doc[]; think: boolean }) {
  const [tab, setTab] = useState<string>("all");
  const [q, setQ] = useState("");
  const [list, setList] = useState<OfficeFile[]>([]);
  const [status, setStatus] = useState<OfficeStatus | null>(null);
  const [creating, setCreating] = useState<OfficeKind | null>(null);
  const [error, setError] = useState("");
  const picker = useRef<HTMLInputElement>(null);

  const load = () => api.officeFiles(tab === "all" ? undefined : tab, q.trim() || undefined)
    .then((r) => { setList(r.files); setStatus(r.status); }).catch((e) => setError(String(e.message)));
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t); }, [tab, q]);

  const openLocal = async () => {
    setError("");
    try {
      if (inTauri()) {
        const { open } = await import("@tauri-apps/plugin-dialog");
        const picked = await open({ multiple: false, title: "Open a document",
          filters: [{ name: "Documents", extensions: ["docx", "xlsx", "pptx", "md", "html", "pdf"] }] });
        if (typeof picked === "string") onOpen(await api.officeOpenLocal(picked));
      } else picker.current?.click();
    } catch (e) { setError(String((e as Error).message || e)); }
  };

  return (
    <div className="scroll"><div className="column wide">
      <div className="page-head"><h1>{t("What shall we make?")}</h1></div>
      <p className="muted lead">Describe it and Vyum writes it with the local model, as real Word, Excel and PowerPoint files.
        Open any file to change it by asking. Nothing leaves this Mac.</p>
      {status && !status.installed && (
        <div className="card warn"><h3>The office engine is not installed</h3>
          <p className="muted">Office needs Vyum's office engine. Run <span className="kbd">scripts/build_genoffice.sh</span> once, then reopen this page.</p></div>
      )}
      <div className="office-cards">
        {KINDS.map((k) => (
          <button key={k.kind} className="office-card" disabled={status ? !status.installed : false}
                  onClick={async () => {
                    if (k.kind === "pdf") { openLocal(); return; }
                    try { onOpen(await api.officeNew(k.kind)); } catch (e) { setError(String((e as Error).message)); }
                  }}>
            <KindIcon kind={k.kind} />
            <span className="t">{k.label} <span className="ai">AI</span></span>
            <span className="e">{k.kind === "pdf" ? "Open a PDF" : `Blank ${k.ext}`}</span>
          </button>
        ))}
        <button className="office-card" onClick={openLocal}>
          <span className="kind-icon outline"><I.Folder size={18} /></span>
          <span className="t">{t("Open local")}</span>
          <span className="e">.docx / .xlsx / .pptx / .pdf …</span>
        </button>
        <input ref={picker} type="file" hidden accept=".docx,.xlsx,.pptx,.md,.html,.pdf"
               onChange={async (e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) { try { onOpen(await api.officeUpload(f)); } catch (err) { setError(String((err as Error).message)); } } }} />
      </div>
      {error && <div className="error-text">{error}</div>}

      <div className="office-toolbar">
        <div className="segmented">
          {[{ key: "all", tab: "All" }, ...KINDS.map((k) => ({ key: k.kind, tab: k.tab }))].map((x) => (
            <button key={x.key} className={tab === x.key ? "on" : ""} onClick={() => setTab(x.key)}>{t(x.tab)}</button>
          ))}
        </div>
        <label className="search-box">
          <I.Search size={15} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("Search file names and contents…")} aria-label="Search files" />
        </label>
        <span className="muted count">Recent · {list.length} file{list.length === 1 ? "" : "s"}</span>
      </div>

      {list.length === 0 ? (
        <div className="empty-state small"><p className="muted">{q ? `Nothing matches “${q}”.` : t("Files you create or open appear here.")}</p></div>
      ) : (
        <div className="file-list">
          {list.map((f) => (
            <button key={f.id} className="file-row office-row" onClick={() => onOpen(f)} disabled={!f.exists}>
              <KindIcon kind={f.kind} size={26} />
              <span className="name-col">
                <span className="name" title={f.path}>{f.name}</span>
                {f.match ? <span className="snip">{f.match}</span> : f.prompt && f.origin === "created" ? <span className="snip">“{f.prompt}”</span> : null}
              </span>
              {f.origin === "created" && <span className="tag">AI</span>}
              <span className="meta">{f.exists ? `${ago(f.modified_at)} · ${bytes(f.size)}` : "file moved or deleted"}</span>
            </button>
          ))}
        </div>
      )}
      {creating && <CreateDialog kind={creating} docs={docs} think={think} onClose={() => setCreating(null)}
                                 onDone={(f) => { setCreating(null); onOpen(f); }} />}
    </div></div>
  );
}

// create -----------------------------------------------------------------------------------------------------------
export function DocPicker({ docs, value, onChange }: { docs: Doc[]; value: string[]; onChange: (ids: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const shown = useMemo(() => docs.filter((d) => d.name.toLowerCase().includes(q.toLowerCase())).slice(0, 50), [docs, q]);
  if (!docs.length) return null;
  return (
    <div className="doc-picker">
      <button type="button" className="tool" onClick={() => setOpen((o) => !o)}>
        <I.FileIcon size={15} /> {value.length ? `Using ${value.length} document${value.length > 1 ? "s" : ""}` : "Use my documents"} <I.ChevronDown size={14} />
      </button>
      {open && (
        <div className="popover down" onMouseLeave={() => setOpen(false)}>
          <input className="mini" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter…" autoFocus />
          <div className="picker-list">
            {shown.map((d) => (
              <label key={d.id} className="menu-item">
                <input type="checkbox" checked={value.includes(d.id)}
                       onChange={(e) => onChange(e.target.checked ? [...value, d.id] : value.filter((x) => x !== d.id))} />
                <span className="name">{d.name}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CreateDialog({ kind, docs, think: thinkDefault, onClose, onDone }: {
  kind: OfficeKind; docs: Doc[]; think: boolean; onClose: () => void; onDone: (f: OfficeFile) => void;
}) {
  const k = kindInfo(kind);
  const [prompt, setPrompt] = useState("");
  const [docIds, setDocIds] = useState<string[]>([]);
  const [think, setThink] = useState(thinkDefault);
  const [live, setLive] = useState<Live | null>(null);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);

  const create = async () => {
    setError("");
    setLive({ status: "Starting", thinking: "", draft: "" });
    abort.current = new AbortController();
    try {
      const r = await runStream((onEvent) => api.officeCreate({ kind, prompt, doc_ids: docIds, think }, onEvent, abort.current!.signal),
                                (f) => setLive((l) => (l ? f(l) : l)));
      if (r?.kind === "office") onDone(r.file);
      else { setError(r && "error" in r ? r.error : "Nothing was created."); setLive(null); }
    } catch (e) {
      setError(abort.current?.signal.aborted ? "Stopped." : String((e as Error).message)); setLive(null);
    }
  };

  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget && !live) onClose(); }}>
      <div className="dialog wide" role="dialog" aria-label={`New ${k.label}`}>
        <div className="row-head"><KindIcon kind={kind} size={30} /><h2>New {k.label.replace("AI ", "")} <span className="muted">{k.ext}</span></h2></div>
        {!live ? (
          <>
            <textarea className="prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} autoFocus rows={4}
                      placeholder="Describe what you want: the purpose, the audience, what it must include…"
                      onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && prompt.trim()) create(); }} />
            <div className="examples">{IDEAS[kind].map((x) => <button key={x} className="example plain" onClick={() => setPrompt(x)}>{x}</button>)}</div>
            <div className="dialog-tools">
              <DocPicker docs={docs} value={docIds} onChange={setDocIds} />
              <button className={`tool ${think ? "on" : ""}`} aria-pressed={think} onClick={() => setThink(!think)}><I.Bulb size={15} /> Think</button>
            </div>
            {error && <div className="error-text">{error}</div>}
            <div className="actions">
              <button className="btn secondary" onClick={onClose}>Cancel</button>
              <button className="btn primary" disabled={!prompt.trim()} onClick={create}><I.Sparkle size={15} /> Create</button>
            </div>
          </>
        ) : (
          <>
            {(live.thinking || live.thinkingSeconds !== undefined) && <Thinking text={live.thinking} seconds={live.thinkingSeconds} live />}
            {live.draft && <div className="draft-box">{live.draft}</div>}
            <div className="status-line"><span className="spinner" />{live.status}</div>
            <div className="actions"><button className="btn secondary" onClick={() => abort.current?.abort()}>Stop</button></div>
          </>
        )}
      </div>
    </div>
  );
}

// one file (preview canvas until its own editor exists) ------------------------------------------------------------
export function OfficeFileView({ file: initial, think, setThink, onBack }: {
  file: OfficeFile; files: OfficeFile[]; think: boolean; setThink: (t: boolean) => void; onBack: () => void; onOpen: (f: OfficeFile) => void;
}) {
  const [file, setFile] = useState(initial);
  const [pages, setPages] = useState<number | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [stamp, setStamp] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    setPages(null); setPreviewError("");
    api.officePreview(file.id).then((r) => setPages(r.pages)).catch((e) => setPreviewError(String(e.message)));
  }, [file.id, stamp]);

  const onChanged = (f: OfficeFile) => { setFile(f); setStamp(Date.now()); setFlash(true); window.setTimeout(() => setFlash(false), 2600); };
  const undo = async () => { setFile(await api.officeUndo(file.id)); setStamp(Date.now()); };
  const empty = pages === 0;

  return (
    <div className="workspace">
      <DocHeader file={file} save="idle" onBack={onBack} onUndo={undo} />
      <div className="workspace-body">
        <DocChat file={file} think={think} setThink={setThink} empty={empty} onChanged={onChanged} busy={busy} setBusy={setBusy} />
        <div className={`doc-canvas preview ${busy ? "busy" : ""}`}>
          {previewError ? <div className="card warn"><h3>No preview</h3><p className="muted">{previewError}</p></div>
            : pages === null ? <div className="status-line"><span className="spinner" />Rendering…</div>
            : Array.from({ length: pages }, (_, i) => (
              <img key={`${stamp}-${i}`} className={`page-img ${file.kind === "pptx" ? "slide" : ""} ${flash ? "flash" : ""}`}
                   alt={`Page ${i + 1}`} src={api.officePreviewUrl(file.id, i + 1, stamp)} loading="lazy" />
            ))}
        </div>
      </div>
    </div>
  );
}
