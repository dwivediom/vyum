import { t, tStatus } from "./i18n";
import { Fragment, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import {
  api, openExternal, type AnswerResult, type ChatResult, type Citation, type CompareResult, type FindHit, type Job, type Result,
  type Sentence, type CalcStep, type TodoItem,
} from "./api";
import * as I from "./icons";

export type Live = { status: string; thinking: string; thinkingSeconds?: number; draft: string; citations: Citation[]; started: number;
                     chat?: boolean; calc?: CalcStep[] };
type Cite = (c: Citation) => void;

/** "p. 12" for pages, "line 3,410" for text files, whose passages are located by line. */
export const where = (docName: string, n: number) =>
  /\.(txt|md|markdown|eml|mbox)$/i.test(docName) ? `line ${n.toLocaleString()}` : `p. ${n.toLocaleString()}`;

/** Indexing progress for a document that is still being read. */
export const indexing = (d: { status?: string; checkpoint?: number; pages: number; unit?: string; eta_s?: number | null }) => {
  if (d.status !== "indexing") return null;
  const pct = d.pages ? Math.floor((100 * (d.checkpoint || 0)) / d.pages) : 0;
  const eta = d.eta_s ? ` · ${d.eta_s >= 90 ? `${Math.round(d.eta_s / 60)} min` : `${d.eta_s} s`} left` : "";
  return `Indexing ${pct}% · ${(d.checkpoint || 0).toLocaleString()} of ${d.pages.toLocaleString()} ${d.unit === "line" ? "lines" : "pages"}${eta}`;
};

/** Where a citation points: "site.com" for the web, "file.pdf · p. 4" for a document. */
export const citeLabel = (c: Citation) =>
  c.kind === "web" ? c.section : `${c.doc_name.split("/").pop()} · ${where(c.doc_name, c.page)}${c.kind === "figure" ? " · figure" : ""}`;

const stripCites = (s: string) => s
  .replace(/\b(?:according to|as per|as stated in|see)\s+(?:source\s+)?\[\d+\]\s*,?\s*/gi, "")  // the chip shows the source
  .replace(/\s*\[\d+(?:\s*,\s*\d+)*\]/g, "")
  .replace(/\*\*|__/g, "")
  .replace(/^\s*([a-z])/, (m) => m.toUpperCase());
const isBullet = (s: string) => /^\s*[-•*]\s+/.test(s);

export function UserMessage({ text }: { text: string }) {
  return <div className={`msg-user ${text.startsWith("/") ? "command" : ""}`}>{text}</div>;
}

function Cites({ nums, all, onCite }: { nums: number[]; all: Citation[]; onCite: Cite }) {
  return (
    <>
      {nums.map((n) => {
        const c = all[n - 1];
        return c ? (
          <button key={n} className="cite" title={c.kind === "web" ? `${c.doc_name} — ${c.section}` : `${c.doc_name}, ${where(c.doc_name, c.page)}`} onClick={() => onCite(c)}>{n}</button>
        ) : null;
      })}
    </>
  );
}

function Sentences({ items, all, onCite }: { items: Sentence[]; all: Citation[]; onCite: Cite }) {
  const bullets = items.filter((s) => isBullet(s.text));
  if (bullets.length >= 2 && bullets.length >= items.length - 1) {
    const intro = items.filter((s) => !isBullet(s.text));
    return (
      <div className="prose">
        {intro.map((s, i) => <p key={i}>{stripCites(s.text)}<Cites nums={s.citations} all={all} onCite={onCite} /></p>)}
        <ul>
          {bullets.map((s, i) => (
            <li key={i}>{stripCites(s.text.replace(/^\s*[-•*]\s+/, ""))}<Cites nums={s.citations} all={all} onCite={onCite} /></li>
          ))}
        </ul>
      </div>
    );
  }
  return (
    <p className="prose">
      {items.map((s, i) => (
        <span key={i}>{stripCites(s.text)}<Cites nums={s.citations} all={all} onCite={onCite} />{" "}</span>
      ))}
    </p>
  );
}

export function Thinking({ text, seconds, live }: { text: string; seconds?: number; live?: boolean }) {
  const body = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(!!live);
  useEffect(() => { if (live) setOpen(seconds === undefined); }, [live, seconds]);
  useEffect(() => { if (live && body.current) body.current.scrollTop = body.current.scrollHeight; }, [text, live]);
  if (!text && !live) return null;
  return (
    <details className="thinking" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>
        <span className="chev"><I.Chevron size={14} /></span>
        <I.Bulb size={15} className={live && seconds === undefined ? "pulse" : ""} />
        {live && seconds === undefined ? t("Thinking…") : `${t("Thought for")} ${seconds ?? "a few"} s`}
      </summary>
      <div className="body" ref={body}>{text || "…"}</div>
    </details>
  );
}

/** Vyum's plan as a to-do list: each item ticks off as it is done, like Claude's task lists. */
export function TodoList({ items, attempt, done }: { items: TodoItem[]; attempt?: number; done?: boolean }) {
  if (!items.length) return null;
  const finished = items.filter((i) => i.state === "done").length;
  return (
    <div className={`todo ${done ? "complete" : ""}`}>
      <div className="todo-head">
        <I.Check size={13} /> {t("Plan")}{attempt && attempt > 1 ? ` · ${t("try")} ${attempt}` : ""}
        <span className="muted">{finished}/{items.length}</span>
      </div>
      <ol>
        {items.map((it, i) => (
          <li key={i} className={it.state}>
            <span className="mark">{it.state === "done" ? <I.Check size={12} /> : it.state === "running" ? <span className="spinner sm" />
              : it.state === "failed" ? "✕" : ""}</span>
            <span className="txt">{it.text}{it.note && <span className="note">{it.note}</span>}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** What Vyum's calculator computed for this answer, like a tool call: label, the expression, the exact result. */
export function Calculations({ steps }: { steps?: CalcStep[] }) {
  const ok = (steps || []).filter((s) => !s.error);
  if (!ok.length) return null;
  return (
    <details className="calc-panel">
      <summary><span className="chev"><I.Chevron size={14} /></span><I.Calculator size={15} />
        {t("Calculated")} · {ok.length} {ok.length === 1 ? t("step") : t("steps")}</summary>
      <div className="calc-steps">
        {ok.map((s, i) => (
          <div key={i} className="calc-step">
            <span className="label">{s.label}</span>
            <code>{s.expr}</code>
            <span className="eq">=</span>
            <strong>{s.result}</strong>
          </div>
        ))}
      </div>
    </details>
  );
}

function SourceChips({ result, onCite }: { result: { sentences: Sentence[]; citations: Citation[] }; onCite: Cite }) {
  const used = Array.from(new Set(result.sentences.flatMap((s) => s.citations))).sort((a, b) => a - b);
  if (!used.length) return null;
  return (
    <div className="sources">
      {used.map((n) => {
        const c = result.citations[n - 1];
        return c ? (
          <SourceChip key={n} c={c} onCite={onCite} />
        ) : null;
      })}
    </div>
  );
}

function SourceChip({ c, onCite }: { c: Citation; onCite: Cite }) {
  return (
    <button className={`source-chip ${c.kind === "web" ? "web" : ""}`} onClick={() => onCite(c)} title={c.kind === "web" ? c.doc_name : c.section}>
      <span className="n">{c.n}</span>
      {c.kind === "web" && <I.Globe size={13} />}
      <span className="t">{citeLabel(c)}</span>
    </button>
  );
}

const CODE = /(```[\s\S]*?(?:```|$)|`[^`\n]*`)/;
const CITE_RUN = /(\s*)(?<![\w\]])((?:\[\d+(?:\s*,\s*\d+)*\])+)/g;

/** Turns [1][2] into links the Markdown renderer hands back as citation buttons. Code is left untouched. */
function linkCites(text: string, n: number) {
  if (!n) return text;
  return text.split(CODE).map((part, i) => (i % 2 ? part : part.replace(CITE_RUN, (_m, space: string, run: string) => {
    const nums = (run.match(/\d+/g) || []).map(Number).filter((x) => x >= 1 && x <= n);
    return nums.length ? space + Array.from(new Set(nums)).map((x) => `[${x}](#cite-${x})`).join("") : "";
  }))).join("");
}

/** Formulas render ($…$, $$…$$, \(…\), \[…\]); a dollar sign before a number stays a price ("$500 to $600"). */
const mathText = (s: string) => s
  .replace(/\\\[([\s\S]+?)\\\]/g, (_, m) => `$$${m}$$`)
  .replace(/\\\(([\s\S]+?)\\\)/g, (_, m) => `$${m}$`)
  .replace(/\$(?=\d[\d,]*(?:\.\d+)?(?![\w^$.]))/g, "\\$");  // "$500" is a price; "$3x^2$" is maths

export function Markdown({ text, citations, onCite }: { text: string; citations: Citation[]; onCite: Cite }) {
  return (
    <div className="prose md">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]} components={{
        a: ({ href, children }) => {
          const m = href?.match(/^#cite-(\d+)$/);
          if (m) {
            const c = citations[Number(m[1]) - 1];
            return c ? <button className="cite" title={citeLabel(c)} onClick={() => onCite(c)}>{m[1]}</button> : null;
          }
          return <a href={href} onClick={(e) => { e.preventDefault(); if (href) openExternal(href); }}>{children}</a>;
        },
      }}>{mathText(linkCites(text, citations.length))}</ReactMarkdown>
    </div>
  );
}

function ChatView({ r, onCite }: { r: ChatResult; onCite: Cite }) {
  const used = Array.from(new Set(Array.from(r.answer.matchAll(/\[(\d+)\]/g), (m) => Number(m[1]))))
    .filter((n) => n >= 1 && n <= r.citations.length).sort((a, b) => a - b);
  return (
    <>
      {r.thinking && <Thinking text={r.thinking} seconds={r.timings?.think_s} />}
      <Calculations steps={r.calculations} />
      {r.answer ? <Markdown text={r.answer} citations={r.citations} onCite={onCite} />
        : r.stopped ? <p className="muted">{t("Stopped before the answer was written.")}</p> : null}
      {used.length > 0 && <div className="sources">{used.map((n) => <SourceChip key={n} c={r.citations[n - 1]} onCite={onCite} />)}</div>}
      <div className="foot">
        {r.web_queries?.length > 0 && (
          <span className="badge" title="Only these search words were sent to the search provider">
            <I.Globe size={13} /> Searched the web for {r.web_queries.map((q) => `“${q}”`).join(" · ")}
          </span>
        )}
        {r.used_files > 0 && <span className="badge" title="Chat mode uses your files but does not verify each sentence. Switch to Documents for verified answers.">
          <I.FileIcon size={13} /> {t("Used your files · not verified")}</span>}
        {r.web_error && <span className="warn-text" title={r.web_error}>Web search failed</span>}
        {r.stopped && <span className="badge stopped"><I.Stop size={11} /> {t(r.unverified ? "Stopped · not verified" : "Stopped")}</span>}
        {r.timings?.total_s !== undefined && <span>{r.timings.total_s}s</span>}
        <CopyButton text={r.answer.replace(/\s*\[\d+\]/g, "")} />
      </div>
    </>
  );
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button className="ghost" onClick={() => { navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }); }}>
      {done ? <I.Check size={14} /> : <I.Copy size={14} />} {t(done ? "Copied" : "Copy")}
    </button>
  );
}

export function AnswerView({ r, onCite }: { r: AnswerResult; onCite: Cite }) {
  if (r.status === "not_found") {
    return (
      <>
        {r.thinking && <Thinking text={r.thinking} seconds={r.timings?.think_s} />}
        <div className="card warn">
          <h3>{t("Not found in these documents")}</h3>
          <p className="muted">{r.reason}</p>
          {r.searched?.length > 0 && (
            <details><summary className="muted">Searched for {r.searched.length} phrasing{r.searched.length > 1 ? "s" : ""}</summary>
              <ul className="muted">{r.searched.map((s, i) => <li key={i}>{s}</li>)}</ul>
            </details>
          )}
        </div>
      </>
    );
  }
  const copyText = r.sentences.map((s) => stripCites(s.text)).join(" ") + "\n\nSources:\n" +
    r.citations.map((c) => `[${c.n}] ${c.doc_name}, p. ${c.page}`).join("\n");
  return (
    <>
      {r.thinking && <Thinking text={r.thinking} seconds={r.timings?.think_s} />}
      {r.title && <h3 className="ai-title">{r.title}</h3>}
      <Sentences items={r.sentences} all={r.citations} onCite={onCite} />
      <SourceChips result={r} onCite={onCite} />
      <div className="foot">
        {r.confidence === "unverified"
          ? <span className="badge">{t("Fast mode · not verified")}</span>
          : <span className={`badge ${r.confidence}`}><I.Shield size={14} /> {t("Verified")} · {t(`${r.confidence} confidence`)}</span>}
        <span>{r.timings?.total_s}s</span>
        {r.resolved_question && <span title="Your follow-up, rewritten using the conversation">Searched as “{r.resolved_question}”</span>}
        {r.dropped?.length > 0 && (
          <details className="removed">
            <summary>{r.dropped.length} unsupported sentence{r.dropped.length > 1 ? "s" : ""} removed</summary>
            <ul>{r.dropped.map((d, i) => <li key={i}>{d.text} <em>({d.why})</em></li>)}</ul>
          </details>
        )}
        <CopyButton text={copyText} />
      </div>
    </>
  );
}

function CompareView({ r, onCite }: { r: CompareResult; onCite: Cite }) {
  if (r.status === "not_found") return <div className="card warn"><h3>Nothing to compare</h3><p className="muted">{r.reason}</p></div>;
  return (
    <>
      <h3 className="ai-title">Comparing {r.documents.map((d) => d.name.split("/").pop()).join(" · ")}</h3>
      <table className="grid">
        <thead><tr><th>Topic</th>{r.documents.map((d) => <th key={d.id}>{d.name.split("/").pop()}</th>)}</tr></thead>
        <tbody>
          {r.rows.map((row, i) => (
            <tr key={i}>
              <th scope="row">{row.topic}{row.differs && <span className="tag">differs</span>}</th>
              {r.documents.map((d) => {
                const c = row.cells[d.id];
                const muted = !c || c.text === "Not stated" || c.text === "Not verified";
                return <td key={d.id} className={muted ? "muted" : ""}>
                  {c ? <>{stripCites(c.text)}<Cites nums={c.citations} all={r.citations} onCite={onCite} /></> : "Not stated"}
                </td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {r.summary.length > 0 && <Sentences items={r.summary} all={r.citations} onCite={onCite} />}
      <div className="foot"><span className="badge high"><I.Shield size={14} /> Each document searched separately · cells verified</span><span>{r.timings?.total_s}s</span></div>
    </>
  );
}

function Highlight({ text, query }: { text: string; query: string }) {
  const words = query.split(/\s+/).filter((w) => w.length > 2).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!words.length) return <>{text}</>;
  const re = new RegExp(`(${words.join("|")})`, "gi");
  return <>{text.split(re).map((part, i) => (i % 2 ? <mark key={i}>{part}</mark> : part))}</>;
}

function FindView({ query, hits, onCite }: { query: string; hits: FindHit[]; onCite: Cite }) {
  if (!hits.length) return <div className="card"><h3>No matches for “{query}”</h3><p className="muted">Exact keyword search. Try fewer or different words, or ask a question instead.</p></div>;
  return (
    <>
      <div className="muted">{hits.length} passage{hits.length > 1 ? "s" : ""} containing “{query}”</div>
      <div className="hits">
        {hits.map((h, i) => (
          <button key={i} className="hit" onClick={() => onCite({ n: i + 1, doc_id: h.doc_id, doc_name: h.doc_name, page: h.page,
                   page_end: h.page, section: h.section, kind: h.kind, text: h.text, score: 0 })}>
            <div className="where">{h.doc_name} · page {h.page}{h.section ? ` · ${h.section}` : ""}</div>
            <div className="snip"><Highlight text={h.snippet} query={query} /></div>
          </button>
        ))}
      </div>
    </>
  );
}

function JobView({ job, text, onDone }: { job: Job; text: string; onDone?: () => void }) {
  const [j, setJ] = useState(job);
  useEffect(() => {
    if (j.status === "done" || j.status === "error") return;
    const t = setTimeout(async () => {
      const next = await api.job(j.id).catch(() => j);
      setJ(next);
      if (next.status === "done" || next.status === "error") onDone?.();
    }, 1200);
    return () => clearTimeout(t);
  }, [j]);
  if (j.status === "error") return <div className="card error">{text} failed: {j.error}</div>;
  if (j.status !== "done") return <div className="status-line"><span className="spinner" />{text} · {j.step}</div>;
  const r = j.result || {};
  return (
    <div className="card">
      <h3><I.Refresh size={15} /> Folder rescanned</h3>
      <p className="muted">{r.added} added · {r.changed} changed · {r.removed} removed · {r.unchanged} unchanged{r.failed ? ` · ${r.failed} could not be read` : ""}</p>
    </div>
  );
}

type ResultProps = { result: Result; onCite: Cite; onInsert: (text: string) => void; onJobDone?: () => void };

export function ResultView({ result: r, onCite, onInsert, onJobDone }: ResultProps) {
  switch (r.kind) {
    case "answer": return <AnswerView r={r} onCite={onCite} />;
    case "chat": return <ChatView r={r} onCite={onCite} />;
    case "compare": return <CompareView r={r} onCite={onCite} />;
    case "find": return <FindView query={r.query} hits={r.hits} onCite={onCite} />;
    case "job": return <JobView job={r.job} text={r.text} onDone={onJobDone} />;
    case "error": return <div className="card error">Something went wrong: {r.error}</div>;
    case "outline": return <OutlineView r={r} />;
    case "files": return (
      <div className="card">
        <h3>{r.files.length} file{r.files.length !== 1 ? "s" : ""} in scope</h3>
        <FileRows files={r.files} />
      </div>
    );
    case "help": return (
      <div className="card">
        <h3>Commands</h3>
        <div className="cmd-list">
          {r.commands.map((c) => (
            <Fragment key={c.name}>
              <span className="c" onClick={() => onInsert(`/${c.name} `)}>{c.usage}</span><span>{c.description}</span>
            </Fragment>
          ))}
        </div>
      </div>
    );
    case "instructions": return (
      <div className="card">
        {r.command ? <h3 className="mono">/{r.command}</h3> : <h3>{r.title || "Nothing to search yet"}</h3>}
        {r.description && <p>{r.description}</p>}
        {r.note && <p className="muted">{r.note}</p>}
        {r.usage && <div className="usage">{r.usage}</div>}
        {r.examples?.length > 0 && <div className="examples">{r.examples.map((e) => <button key={e} className="example" onClick={() => onInsert(e)}>{e}</button>)}</div>}
        {r.files?.length > 0 && r.command && ["compare", "summarize"].includes(r.command) && (
          <>
            <div className="section-title">Files you can use</div>
            <div className="examples">{r.files.map((f) => {
              const short = f.split("/").pop()!.replace(/\.[^.]+$/, "");
              return <button key={f} className="example" onClick={() => onInsert(`/${r.command} ${short} `)}>{f}</button>;
            })}</div>
          </>
        )}
        {!r.command && r.commands && <p className="muted">Type <span className="kbd">/</span> to see commands.</p>}
      </div>
    );
  }
}

function OutlineView({ r }: { r: Extract<Result, { kind: "outline" }> }) {
  const unit = r.unit === "line" ? "lines" : "pages";
  return (
    <div className="card">
      <h3>Structure of {r.doc_name}</h3>
      <p className="muted">{r.sections.toLocaleString()} top-level sections across {r.pages?.toLocaleString()} {unit}</p>
      {r.series.length > 0 && (
        <>
          <div className="section-title">Repeating sections</div>
          <div className="file-list">
            {r.series.map((s) => (
              <div key={s.pattern} className="file-row">
                <span className="ext">{s.count.toLocaleString()}×</span>
                <span className="name" title={`${s.first_title} … ${s.last_title}`}>{s.pattern}</span>
                <span className="meta">{unit} {s.first.toLocaleString()}–{s.last.toLocaleString()}</span>
              </div>
            ))}
          </div>
        </>
      )}
      {r.parts.length > 0 && (
        <>
          <div className="section-title">Parts</div>
          <div className="file-list">
            {r.parts.map((p, i) => (
              <div key={i} className="file-row">
                <span className="name" title={p.title}>{p.title}</span>
                <span className="meta">{unit} {p.first.toLocaleString()}–{p.last.toLocaleString()} · {p.passages.toLocaleString()} passages</span>
              </div>
            ))}
            {r.more_parts > 0 && <div className="muted" style={{ padding: "4px 8px" }}>…and {r.more_parts.toLocaleString()} more</div>}
          </div>
        </>
      )}
    </div>
  );
}

export function FileRows({ files }: { files: { name: string; ext: string; pages: number; chunks: number; figures: number; quarantined?: number;
                                              status?: "indexing" | "ready"; checkpoint?: number; unit?: "page" | "line"; eta_s?: number | null }[] }) {
  return (
    <div className="file-list">
      {files.map((f) => (
        <div key={f.name} className="file-row">
          <span className="ext">{f.ext.replace(".", "").toUpperCase()}</span>
          <span className="name" title={f.name}>{f.name}</span>
          {indexing(f)
            ? <span className="meta"><span className="spinner" style={{ display: "inline-block", verticalAlign: -2, marginRight: 6 }} />{indexing(f)}</span>
            : <span className="meta">{f.pages.toLocaleString()} {f.unit === "line" ? "lines" : "p"} · {f.chunks.toLocaleString()} passages{f.figures ? ` · ${f.figures} figures` : ""}</span>}
          {!!f.quarantined && <span className="meta warn" title="Text addressed to AI systems, kept out of answers"><I.Warn size={13} /> {f.quarantined} quarantined</span>}
        </div>
      ))}
    </div>
  );
}

export function LiveView({ live, onCite }: { live: Live; onCite: Cite }) {
  const thinkingActive = live.thinking.length > 0 || live.thinkingSeconds !== undefined;
  return (
    <>
      {thinkingActive && <Thinking text={live.thinking} seconds={live.thinkingSeconds} live />}
      <Calculations steps={live.calc} />
      {live.draft && (live.chat
        ? <Markdown text={live.draft} citations={live.citations} onCite={onCite} />
        : <div className="draft">{stripCites(live.draft)}</div>)}
      {live.citations.length > 0 && !live.draft && (
        <div className="sources">
          {live.citations.slice(0, 8).map((c) => <SourceChip key={c.n} c={c} onCite={onCite} />)}
        </div>
      )}
      <div className="status-line"><span className="spinner" />{tStatus(live.status)}</div>
    </>
  );
}
