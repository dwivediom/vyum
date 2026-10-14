import { lang, t } from "./i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import "./pdf-text-layer.css";
import { api, type Citation, type LiveStep, type OfficeFile } from "./api";
import { DocChat, DocHeader } from "./Workspace";
import * as I from "./icons";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

/** One page: the rendered canvas plus pdf.js's text layer, so text can be selected and copied. */
function PdfPage({ doc, n, scale, flash, onVisible }: { doc: pdfjs.PDFDocumentProxy; n: number; scale: number; flash: boolean; onVisible: (n: number) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const text = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    let task: { cancel: () => void } | null = null;
    doc.getPage(n).then(async (page) => {
      if (cancelled || !canvas.current || !text.current) return;
      const viewport = page.getViewport({ scale });
      const ratio = window.devicePixelRatio || 1;
      const c = canvas.current;
      c.width = Math.floor(viewport.width * ratio);
      c.height = Math.floor(viewport.height * ratio);
      setSize({ w: viewport.width, h: viewport.height });
      const render = page.render({ canvas: c, canvasContext: c.getContext("2d")!, viewport, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined });
      task = render;
      await render.promise.catch(() => undefined);
      if (cancelled || !text.current) return;
      text.current.replaceChildren();
      text.current.style.setProperty("--scale-factor", String(scale));
      await new pdfjs.TextLayer({ textContentSource: page.streamTextContent(), container: text.current, viewport }).render().catch(() => undefined);
    });
    return () => { cancelled = true; task?.cancel(); };
  }, [doc, n, scale]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const io = new IntersectionObserver((e) => { if (e[0].isIntersecting) onVisible(n); }, { threshold: 0.5 });
    io.observe(el);
    return () => io.disconnect();
  }, [n, onVisible]);

  return (
    <div ref={box} className={`pdf-page ${flash ? "flash" : ""}`} data-page={n} style={size ? { width: size.w, height: size.h } : undefined}>
      <canvas ref={canvas} style={size ? { width: size.w, height: size.h } : undefined} />
      <div ref={text} className="textLayer" />
    </div>
  );
}

function Thumb({ doc, n, on, onClick }: { doc: pdfjs.PDFDocumentProxy; n: number; on: boolean; onClick: () => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    let cancelled = false;
    doc.getPage(n).then((page) => {
      if (cancelled || !canvas.current) return;
      const viewport = page.getViewport({ scale: 0.22 });
      canvas.current.width = viewport.width * 2;
      canvas.current.height = viewport.height * 2;
      page.render({ canvas: canvas.current, canvasContext: canvas.current.getContext("2d")!, viewport, transform: [2, 0, 0, 2, 0, 0] });
    });
    return () => { cancelled = true; };
  }, [doc, n]);
  return (
    <button className={`pdf-thumb ${on ? "on" : ""}`} onClick={onClick} title={`Page ${n}`}>
      <canvas ref={canvas} /><span>{n}</span>
    </button>
  );
}

export default function PdfWorkspace({ file: initial, think, setThink, onBack, onOpen }: {
  file: OfficeFile; think: boolean; setThink: (t: boolean) => void; onBack: () => void; onOpen: (f: OfficeFile) => void;
}) {
  const [file, setFile] = useState(initial);
  const [doc, setDoc] = useState<pdfjs.PDFDocumentProxy | null>(null);
  const [stamp, setStamp] = useState(Date.now());
  const [scale, setScale] = useState(1.2);
  const [current, setCurrent] = useState(1);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const main = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    setError("");
    const task = pdfjs.getDocument({ url: api.officeFileUrl(file.id, stamp) });
    task.promise.then((d) => { if (live) setDoc(d); }).catch((e) => { if (live) setError(String(e.message || e)); });
    return () => { live = false; task.destroy(); };
  }, [file.id, stamp]);

  const goTo = (n: number) => main.current?.querySelector(`[data-page="${n}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  const onVisible = useCallback((n: number) => setCurrent(n), []);
  const reload = (f: OfficeFile, pages: number[] = []) => {
    setFile(f); setStamp(Date.now());
    setFlash(new Set(pages)); window.setTimeout(() => setFlash(new Set()), 2600);
  };

  const op = async (action: "rotate" | "delete" | "extract", degrees = 90) => {
    if (action === "delete" && !window.confirm(`Delete page ${current}? You can undo this.`)) return;
    setBusy(true);
    try {
      const out = await api.pdfOp(file.id, action, [current], degrees);
      if (action === "extract") onOpen(out); else reload(out, action === "rotate" ? [current] : []);
    } catch (e) { setError(String((e as Error).message)); } finally { setBusy(false); }
  };
  const undo = async () => reload(await api.officeUndo(file.id));
  const pages = doc ? Array.from({ length: doc.numPages }, (_, i) => i + 1) : [];

  // The full editor (GenOffice's PDF module, hosted by the engine). Reopened after Vyum's chat changes the file.
  const [editor, setEditor] = useState<{ view: number; src: string } | null>(null);
  const [editorOff, setEditorOff] = useState("");
  useEffect(() => {
    let view = 0, live = true;
    const theme = window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    api.pdfxOpen(file.id, lang === "hi" ? "hi" : "en", theme)
      .then((r) => { view = r.view; if (live) setEditor({ view: r.view, src: api.engineUrl(r.path) }); })
      .catch((e) => live && setEditorOff(String((e as Error).message)));
    return () => { live = false; if (view) api.pdfxClose(view); };
  }, [file.id, stamp]);

  // The editor's AI buttons and "ask about the selection" come to Vyum's chat here (one assistant, on this Mac).
  const [inject, setInject] = useState<{ text: string; n: number } | null>(null);
  useEffect(() => {
    if (!editor) return;
    const origin = new URL(editor.src).origin;
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== origin || e.data?.type !== "vyum-ai") return;
      const { kind, text, selection } = e.data as { kind: string; text?: string; selection?: string };
      const quoted = selection ? `\n\n“${selection}”` : "";
      const ask = kind === "summary" ? t("Summarize this PDF")
        : kind === "keypoints" ? t("What are the key points of this PDF?")
        : kind === "ask" ? `${text || t("Explain this")}${quoted}` : "";
      if (ask) setInject({ text: ask, n: Date.now() });
      else document.querySelector<HTMLTextAreaElement>(".doc-chat textarea")?.focus();
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [editor]);

  // Citations: the editor scrolls to the cited passage and highlights it; "Highlight sources" makes them real marks.
  const frame = useRef<HTMLIFrameElement>(null);
  const toEditor = (msg: object) => frame.current?.contentWindow?.postMessage(msg, editor ? new URL(editor.src).origin : "*");
  const onCite = (c: Citation) => toEditor({ type: "vyum-cite", page: c.page, text: c.text });
  const onMark = (cs: Citation[]) => new Promise<number>((resolve) => {
    const id = crypto.randomUUID();
    const done = (e: MessageEvent) => {
      if (e.data?.type !== "vyum-marked" || e.data.id !== id) return;
      window.removeEventListener("message", done);
      resolve(Number(e.data.count) || 0);
    };
    window.addEventListener("message", done);
    toEditor({ type: "vyum-mark", id, items: cs.map((c) => ({ page: c.page, text: c.text })) });
    setTimeout(() => { window.removeEventListener("message", done); resolve(0); }, 8000);
  });

  // Live editing: one planned step at a time, made by the editor on screen; it answers when the step is done.
  const onLive = (step: LiveStep) => new Promise<{ ok: boolean; note?: string }>((resolve) => {
    const id = crypto.randomUUID();
    const done = (e: MessageEvent) => {
      if (e.data?.type !== "vyum-live-done" || e.data.id !== id) return;
      window.removeEventListener("message", done);
      clearTimeout(timer);
      resolve({ ok: !!e.data.ok, note: e.data.note });
    };
    const timer = setTimeout(() => { window.removeEventListener("message", done); resolve({ ok: false, note: t("The editor did not answer.") }); }, 60000);
    window.addEventListener("message", done);
    toEditor({ type: "vyum-live", id, step });
  });

  if (editor) {
    return (
      <div className="workspace">
        <DocHeader file={file} save="idle" onBack={onBack} onUndo={undo} />
        <div className="workspace-body pdf-editor">
          <DocChat file={file} think={think} setThink={setThink} empty={false} busy={busy} setBusy={setBusy} inject={inject}
                   onCite={onCite} onMark={onMark} onLive={onLive}
                   onChanged={(f) => { if (f.id !== file.id) { onOpen(f); return; } reload(f); }} />
          <iframe ref={frame} key={editor.src} className={`pdf-editor-frame ${busy ? "busy" : ""}`} src={editor.src} title={file.name} />
        </div>
      </div>
    );
  }

  return (
    <div className="workspace">
      <DocHeader file={file} save="idle" onBack={onBack} onUndo={undo} />
      <div className="toolbar">
        {editorOff && <span className="tb-label muted" title={editorOff}>{t("Simple viewer")}</span>}
        <span className="tb-label">Page {current} / {doc?.numPages ?? "…"}</span>
        <span className="tb-sep" />
        <button className="tb" title="Zoom out" onClick={() => setScale((s) => Math.max(0.5, +(s - 0.2).toFixed(1)))}>−</button>
        <span className="tb-label">{Math.round(scale * 100)}%</span>
        <button className="tb" title="Zoom in" onClick={() => setScale((s) => Math.min(3, +(s + 0.2).toFixed(1)))}>+</button>
        <button className="tb wide" title="Fit width" onClick={() => setScale(main.current ? Math.min(2.5, (main.current.clientWidth - 64) / 612) : 1.2)}>Fit</button>
        <span className="tb-sep" />
        <button className="tb wide" disabled={busy} title="Rotate this page" onClick={() => op("rotate")}><I.Refresh size={15} /> {t("Rotate")}</button>
        <button className="tb wide" disabled={busy} title="Save this page as a new PDF" onClick={() => op("extract")}><I.External size={15} /> {t("Extract page")}</button>
        <button className="tb wide" disabled={busy || (doc?.numPages ?? 0) < 2} title="Delete this page" onClick={() => op("delete")}><I.Trash size={15} /> {t("Delete page")}</button>
      </div>
      <div className="workspace-body pdf">
        <DocChat file={file} think={think} setThink={setThink} empty={false} busy={busy} setBusy={setBusy}
                 onChanged={(f, changes) => {
                   if (f.id !== file.id) { onOpen(f); return; }
                   const nums = changes.flatMap((c) => (c.match(/\d+/g) || []).map(Number)).filter((n) => n >= 1 && n <= (doc?.numPages ?? 0));
                   reload(f, nums);
                 }} />
        <nav className="pdf-thumbs">{doc && pages.map((n) => <Thumb key={`${stamp}-${n}`} doc={doc} n={n} on={n === current} onClick={() => goTo(n)} />)}</nav>
        <div ref={main} className={`pdf-main ${busy ? "busy" : ""}`}>
          {error && <div className="card error">{error}</div>}
          {!doc && !error && <div className="status-line"><span className="spinner" />Opening…</div>}
          {doc && pages.map((n) => <PdfPage key={`${stamp}-${n}`} doc={doc} n={n} scale={scale} flash={flash.has(n)} onVisible={onVisible} />)}
        </div>
      </div>
    </div>
  );
}
