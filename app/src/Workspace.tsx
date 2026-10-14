import { t, tStatus } from "./i18n";
import { useEffect, useRef, useState } from "react";
import { api, type AnswerResult, type Citation, type LiveStep, type OfficeFile, type SheetTarget, type StreamEvent, type TodoItem } from "./api";
import { AnswerView, Markdown, Thinking, TodoList } from "./Messages";
import { KindIcon } from "./Office";
import * as I from "./icons";

export type SaveState = "saved" | "saving" | "error" | "idle";
type Msg = { role: "user" | "assistant"; text: string; changes?: string[]; error?: boolean; seconds?: number;
             plan?: TodoItem[]; thinking?: string; cited?: AnswerResult; marked?: number };
type Live = { status: string; thinking: string; thinkingSeconds?: number; draft: string; plan?: TodoItem[]; attempt?: number };

const SUGGEST: Record<string, string[]> = {
  docx: ["Summarize this document", "Fix spelling and grammar", "Add an executive summary at the top", "Make it more formal"],
  xlsx: ["What is the total?", "Add a totals row", "Add a column with the % change", "Sort by the largest amount"],
  pptx: ["Add a closing slide with next steps", "Shorten every bullet", "What is this deck about?"],
  pdf: ["Summarize this PDF", "What are the key dates?", "Remove page 1", "Convert to Word"],
  md: ["Summarize this note", "Add a table of contents", "Fix spelling and grammar"],
  html: ["Summarize this page", "Make the text more concise"],
};
const EMPTY_HINT: Record<string, string> = {
  docx: "What should this document be? e.g. a one-page proposal for a customer portal, for our CFO",
  md: "What should this note be?", html: "What should this page say?",
  xlsx: "What should this spreadsheet track? e.g. a monthly budget with planned vs actual",
  pptx: "What is the presentation about? e.g. 6 slides introducing our product to investors",
};

/** The title bar every editor shares: file name, save state, and the actions on the file. */
export function DocHeader({ file, save, onBack, onUndo, children }: {
  file: OfficeFile; save: SaveState; onBack: () => void; onUndo?: () => void; children?: React.ReactNode;
}) {
  return (
    <header className="doc-header">
      <button className="icon-btn" onClick={onBack} title="All files"><I.Chevron size={16} className="flip" /></button>
      <KindIcon kind={file.kind} size={28} />
      <div className="doc-title">
        <div className="name-row">
          <span className="name" title={file.path}>{file.name}</span>
          <span className="sub">
            {save === "saving" ? t("Saving…") : save === "error" ? <span className="error-text">{t("Could not save")}</span>
              : save === "saved" ? <><I.Check size={12} /> {t("Saved to this Mac")}</> : t("On this Mac")}
          </span>
        </div>
        {children}
      </div>
      <span className="spacer" />
      {onUndo && file.versions?.length > 0 && <button className="btn secondary sm" onClick={onUndo} title="Restore the version before the last change"><I.Undo size={14} /> {t("Undo change")}</button>}
      <button className="btn secondary sm" onClick={() => api.officeReveal(file.id)}><I.Folder size={14} /> {t("Show in Finder")}</button>
    </header>
  );
}

/** The chat on the left of an open file: questions are answered from the file, requests change it (the canvas shows it). */
export function DocChat({ file, think, setThink, empty, onChanged, onDraft, onProgress, onFocus, onDashboard, inject, busy, setBusy, onCite, onMark, onLive }: {
  file: OfficeFile; think: boolean; setThink: (t: boolean) => void; empty: boolean;
  onChanged: (f: OfficeFile, changes: string[]) => void;
  onDraft?: (text: string | null, format?: "html" | "markdown", base?: string) => void;
  busy: boolean; setBusy: (b: boolean) => void;
  onProgress?: () => void;   // a step of the plan wrote to the file: show it on the canvas now
  onFocus?: (t: SheetTarget | null) => void;   // where the running step works: the canvas shows that sheet and range
  onDashboard?: (id: string) => void;          // a dashboard was built: show it
  inject?: { text: string; n: number } | null;   // a request sent from elsewhere (the "+ Dashboard" tab), run like a typed one
  onCite?: (c: Citation) => void;                // a citation was clicked: the file view shows and highlights that passage
  onMark?: (cs: Citation[]) => Promise<number>;  // keep the cited passages as highlights in the file
  onLive?: (step: LiveStep) => Promise<{ ok: boolean; note?: string }>;  // the open editor makes one planned edit
}) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [live, setLive] = useState<Live | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const runRef = useRef("");
  const stopRef = useRef(false);  // Stop pressed while the editor is making planned edits: the rest are skipped
  useEffect(() => { setMsgs([]); }, [file.id]);
  const stop = () => {  // the engine stops at its next safe point; a file being saved always finishes first
    if (!runRef.current) return;
    stopRef.current = true;
    api.stop(runRef.current);
    setLive((l) => l && { ...l, status: "Stopping" });
  };
  useEffect(() => {
    if (!busy) return;
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); stop(); } };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [busy]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs.length, live?.status, live?.draft]);

  const send = async (message = text) => {
    if (!message.trim() || busy) return;
    setText("");
    const history = msgs.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.text }));
    setMsgs((m) => [...m, { role: "user", text: message }]);
    setLive({ status: "Starting", thinking: "", draft: "" });
    setBusy(true);
    const t0 = Date.now();
    let result: any = null;
    let draft = "";
    let fmt: "html" | "markdown" = "markdown";
    let base: string | undefined;
    let stopped = false;
    let plan: TodoItem[] = [], thinking = "";
    runRef.current = crypto.randomUUID();
    stopRef.current = false;
    try {
      await api.officeChat(file.id, { message, history, think, run_id: runRef.current, live: !!onLive }, (ev: StreamEvent) => {
        if (ev.type === "status") setLive((l) => l && { ...l, status: ev.text });
        else if (ev.type === "thinking") { thinking += ev.text; setLive((l) => l && { ...l, thinking: l.thinking + ev.text }); }
        else if (ev.type === "file_restored") onProgress?.();  // a failed try was undone: show the file as it is
        else if (ev.type === "plan") {
          plan = ev.items.map((text) => ({ text, state: "pending" as const }));
          setLive((l) => l && { ...l, plan, attempt: ev.attempt });
        }
        else if (ev.type === "plan_step") {
          plan = plan.map((it, i) => (i === ev.index ? { ...it, state: ev.state, note: ev.note } : it));
          setLive((l) => l && { ...l, plan });
          if (ev.state === "running" && ev.target) onFocus?.(ev.target);
          if (ev.state === "done" && plan.length - ev.index > 3) onProgress?.();  // a writing step, not one of the 3 checks
        }
        else if (ev.type === "thinking_done") setLive((l) => l && { ...l, thinkingSeconds: ev.seconds });
        else if (ev.type === "draft_format") { fmt = ev.format; base = ev.base; }
        else if (ev.type === "draft") { draft += ev.text; setLive((l) => l && { ...l, draft: l.draft + ev.text }); onDraft?.(draft, fmt, base); }
        else if (ev.type === "result") result = ev.result;
        else if (ev.type === "stopped") stopped = true;
      });
      const seconds = (Date.now() - t0) / 1000;
      if (stopped && !result) setMsgs((m) => [...m, { role: "assistant", text: draft && fmt === "markdown" ? `${draft.trim()}\n\n*${t("Stopped")}*` : `${t("Stopped")}. ${t("The file was not changed.")}`, seconds }]);
      else if (!result) setMsgs((m) => [...m, { role: "assistant", text: "No reply came back.", error: true }]);
      else if (result.kind === "error") setMsgs((m) => [...m, { role: "assistant", text: result.error, error: true, plan, thinking }]);
      else if (result.kind === "office_answer") setMsgs((m) => [...m, { role: "assistant", text: result.answer, seconds }]);
      else if (result.kind === "pdf_live" && onLive) {
        // the editor makes each edit on screen; the list ticks as it goes, then the PDF is saved
        const steps: LiveStep[] = [...result.steps, { do: "save", label: t("Save the PDF") }];
        let items: TodoItem[] = steps.map((s) => ({ text: s.label, state: "pending" as const }));
        const show = () => setLive((l) => l && { ...l, status: "Editing the PDF", plan: items });
        for (let i = 0; i < steps.length; i++) {
          if (stopRef.current && steps[i].do !== "save") { items = items.map((it, j) => (j === i ? { ...it, state: "failed", note: t("Stopped") } : it)); continue; }
          items = items.map((it, j) => (j === i ? { ...it, state: "running" } : it)); show();
          const r = await onLive(steps[i]);
          items = items.map((it, j) => (j === i ? { ...it, state: r.ok ? "done" : "failed", note: r.note } : it)); show();
        }
        const missing = (result.missing as string[] | undefined)?.length
          ? `\n\n${t("Not found in the PDF")}: ${(result.missing as string[]).map((m) => `“${m}”`).join(", ")}` : "";
        // the planned summary is only true when every edit was made; otherwise say exactly what happened
        const edits = items.slice(0, -1);
        const made = edits.filter((it) => it.state === "done").length;
        const saved = items[items.length - 1].state === "done";
        const text = made === edits.length && saved ? (result.summary || t("Done."))
          : made === 0 ? t("Nothing was changed.") + " " + edits.filter((it) => it.state === "failed").map((it) => `${it.text}: ${it.note ?? ""}`).join(" ")
          : t("Made {made} of {n} changes; the list shows which ones did not work.").replace("{made}", String(made)).replace("{n}", String(edits.length))
            + (saved ? "" : " " + t("The PDF was not saved."));
        setMsgs((m) => [...m, { role: "assistant", seconds: (Date.now() - t0) / 1000, plan: items, text: text + missing }]);
      }
      else if (result.kind === "office_cited") {
        const r = { ...result, kind: "answer" } as AnswerResult;
        setMsgs((m) => [...m, { role: "assistant", text: result.answer, seconds, cited: r }]);
        const first = r.sentences.flatMap((s) => s.citations)[0];
        if (first && r.citations[first - 1]) onCite?.(r.citations[first - 1]);  // show where the answer comes from
      }
      else if (result.kind === "dashboard") {
        setMsgs((m) => [...m, { role: "assistant", text: result.summary, changes: result.changes, seconds, plan, thinking }]);
        onDashboard?.(result.dashboard);
      }
      else if (result.kind === "office") {
        setMsgs((m) => [...m, { role: "assistant", text: result.summary || (result.changes?.length ? "Done. The changed parts are highlighted." : "Done."), changes: result.changes, seconds, plan, thinking }]);
        onChanged(result.file, result.changes || []);
      }
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", text: String((e as Error).message), error: true }]);
    } finally {
      onFocus?.(null);
      runRef.current = "";
      onDraft?.(null);
      setLive(null);
      setBusy(false);
    }
  };

  useEffect(() => { if (inject?.text) send(inject.text); }, [inject?.n]);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <aside className="doc-chat">
      <div className="doc-chat-head"><I.Logo size={20} /> Vyum <span className="muted small">· {t("reads and edits this file")}</span></div>
      <div className="doc-chat-body">
        {msgs.length === 0 && !live && (
          <div className="doc-chat-empty">
            <p className="muted">{empty ? (EMPTY_HINT[file.kind] || "What should I write?") : t("Ask about this file, or tell me what to change. You will see the change in the file.")}</p>
            {!empty && <div className="examples">{(SUGGEST[file.kind] || []).map((s) => <button key={s} className="example plain" onClick={() => send(t(s))}>{t(s)}</button>)}</div>}
          </div>
        )}
        {msgs.map((m, i) => m.role === "user"
          ? <div key={i} className="dc-user">{m.text}</div>
          : (
            <div key={i} className={`dc-ai ${m.error ? "error" : ""}`}>
              {m.thinking && <Thinking text={m.thinking} />}
              {m.plan && m.plan.length > 0 && <TodoList items={m.plan} done />}
              {m.error ? <span className="error-text">{m.text}</span>
                : m.cited ? <AnswerView r={m.cited} onCite={(c) => onCite?.(c)} />
                : <Markdown text={m.text} citations={[]} onCite={() => undefined} />}
              {m.cited && onMark && m.cited.status === "answered" && (
                <button className="tb-btn small mark-btn" disabled={m.marked !== undefined} onClick={async () => {
                  const used = new Set(m.cited!.sentences.flatMap((s) => s.citations));
                  const n = await onMark(m.cited!.citations.filter((c) => used.has(c.n)));
                  setMsgs((all) => all.map((x, j) => (j === i ? { ...x, marked: n } : x)));
                }}>
                  <I.Highlighter size={14} /> {m.marked === undefined ? t("Highlight sources in the PDF")
                    : t("{n} passages highlighted. Save the PDF to keep them.").replace("{n}", String(m.marked))}
                </button>
              )}
              {m.changes && m.changes.length > 0 && <ul className="dc-changes">{m.changes.slice(0, 12).map((c, j) => <li key={j}>{c}</li>)}</ul>}
              {m.seconds !== undefined && <div className="muted small">{m.seconds.toFixed(1)} s</div>}
            </div>
          ))}
        {live && (
          <div className="dc-ai">
            {(live.thinking || live.thinkingSeconds !== undefined) && <Thinking text={live.thinking} seconds={live.thinkingSeconds} live />}
            {live.plan && <TodoList items={live.plan} attempt={live.attempt} />}
            {live.draft && !onDraft && <div className="draft-box">{live.draft}</div>}
            <div className="status-line"><span className="spinner" />{tStatus(live.status)}</div>
          </div>
        )}
        <div ref={end} />
      </div>
      <div className="doc-chat-input">
        <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} disabled={busy}
                  placeholder={t(empty ? "Describe what to write…" : "Ask or tell Vyum what to change…")}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
        <div className="row">
          <button className={`tool ${think ? "on" : ""}`} aria-pressed={think} onClick={() => setThink(!think)}><I.Bulb size={14} /> {t("Think")}</button>
          <span className="spacer" />
          {busy && live
            ? <button className="send stop" aria-label="Stop" title={`${t("Stop")} (Esc)`} onClick={stop}><I.Stop size={12} /></button>
            : <button className="send" aria-label="Send" disabled={!text.trim() || busy} onClick={() => send()}><I.ArrowUp size={16} /></button>}
        </div>
      </div>
    </aside>
  );
}
