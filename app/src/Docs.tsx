import { useCallback, useEffect, useRef, useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { mergeAttributes, Node } from "@tiptap/core";
import { EditorContent, useEditor, useEditorState, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import { TextAlign } from "@tiptap/extension-text-align";
import { Highlight } from "@tiptap/extension-highlight";
import { Color, TextStyle } from "@tiptap/extension-text-style";
import { api, type OfficeFile } from "./api";
import { DocChat, DocHeader, type SaveState } from "./Workspace";
import * as I from "./icons";

/** A part of the .docx the editor cannot show (picture, chart, table of contents): kept in place, never edited here. */
const VyumBlock = Node.create({
  name: "vyumBlock",
  group: "block",
  atom: true,
  selectable: true,
  addAttributes() {
    return {
      block: { default: null, parseHTML: (el) => el.getAttribute("data-vyum-block"), renderHTML: (a) => ({ "data-vyum-block": a.block }) },
      label: { default: "Object", parseHTML: (el) => el.getAttribute("data-label"), renderHTML: (a) => ({ "data-label": a.label }) },
    };
  },
  parseHTML() { return [{ tag: "div[data-vyum-block]" }]; },
  renderHTML({ HTMLAttributes }) { return ["div", mergeAttributes(HTMLAttributes, { class: "vyum-block" })]; },
});

const mdToHtml = (md: string) => renderToStaticMarkup(<ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>);
const blockTexts = (html: string) => {
  const div = document.createElement("div");
  div.innerHTML = html;
  return Array.from(div.children).map((e) => (e.textContent || "").replace(/\s+/g, " ").trim());
};

export default function DocsWorkspace({ file: initial, think, setThink, onBack }: {
  file: OfficeFile; think: boolean; setThink: (t: boolean) => void; onBack: () => void;
}) {
  const [file, setFile] = useState(initial);
  const [save, setSave] = useState<SaveState>("idle");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [empty, setEmpty] = useState(false);
  const [zoom, setZoom] = useState(100);
  const timer = useRef<number | null>(null);
  const applying = useRef(false);   // setContent from the server must not trigger a save
  const lastSaved = useRef("");

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ link: { openOnClick: false } }),
      Table.configure({ resizable: false }), TableRow, TableHeader, TableCell,
      TextAlign.configure({ types: ["heading", "paragraph"] }), Highlight, TextStyle, Color, VyumBlock,
    ],
    content: "",
    editorProps: { attributes: { class: "doc-page-content", spellcheck: "true" } },
    onUpdate: ({ editor }) => {
      if (applying.current) return;
      setEmpty(editor.getText().trim().length === 0);
      if (timer.current) window.clearTimeout(timer.current);
      setSave("saving");
      timer.current = window.setTimeout(() => saveNow(editor), 1200);
    },
  });

  const saveNow = useCallback(async (ed: Editor) => {
    const html = ed.getHTML();
    if (html === lastSaved.current) { setSave("saved"); return; }
    try {
      const r = await api.docSave(file.id, html);
      lastSaved.current = html;
      if (r.file) setFile(r.file);
      setSave("saved");
    } catch { setSave("error"); }
  }, [file.id]);

  const load = useCallback(async (flash = false) => {
    if (!editor) return;
    const before = blockTexts(editor.getHTML());
    const d = await api.docLoad(file.id);
    applying.current = true;
    editor.commands.setContent(d.html, { emitUpdate: false });
    applying.current = false;
    lastSaved.current = editor.getHTML();
    setEmpty(editor.getText().trim().length === 0);
    setLoaded(true);
    if (flash) {
      const after = blockTexts(d.html);
      const nodes = Array.from(editor.view.dom.children) as HTMLElement[];
      nodes.forEach((el, i) => { if (before[i] !== after[i]) { el.classList.add("flash"); window.setTimeout(() => el.classList.remove("flash"), 2600); } });
      nodes.find((el) => el.classList.contains("flash"))?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [editor, file.id]);

  useEffect(() => { setFile(initial); }, [initial.id]);
  useEffect(() => { if (editor) load(); }, [editor, file.id]);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  // While Vyum writes an empty document, the text appears in the page as it is generated.
  const onDraft = useCallback((text: string | null, format: "html" | "markdown" = "markdown", base?: string) => {
    if (!editor) return;
    if (text === null) { editor.setEditable(true); return; }
    editor.setEditable(false);
    let html = format === "html" ? text : mdToHtml(text);
    if (format === "html" && base) {
      // rewritten blocks so far, then the original blocks the rewrite has not reached yet
      const fresh = new DOMParser().parseFromString(text, "text/html").body.children.length;
      const rest = Array.from(new DOMParser().parseFromString(base, "text/html").body.children).slice(fresh);
      html = text + rest.map((e) => e.outerHTML).join("");
    }
    applying.current = true;
    editor.commands.setContent(html, { emitUpdate: false });
    applying.current = false;
  }, [editor]);

  const onChanged = async (f: OfficeFile) => {
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null; }
    setFile(f);
    await load(true);
    setSave("saved");
  };

  const undo = async () => { setFile(await api.officeUndo(file.id)); await load(true); };

  return (
    <div className="workspace">
      <DocHeader file={file} save={save} onBack={onBack} onUndo={undo}>
        {editor && <DocsMenus editor={editor} file={file} zoom={zoom} setZoom={setZoom} />}
      </DocHeader>
      {editor && <DocsToolbar editor={editor} zoom={zoom} setZoom={setZoom} disabled={busy} />}
      <div className="workspace-body">
        <DocChat file={file} think={think} setThink={setThink} empty={empty && loaded} onChanged={onChanged} onDraft={onDraft}
                 busy={busy} setBusy={(b) => { setBusy(b); editor?.setEditable(!b); }} />
        <div className="doc-canvas">
          {!loaded && <div className="status-line"><span className="spinner" />Opening…</div>}
          <div className={`doc-page ${busy ? "busy" : ""}`} style={{ zoom: zoom / 100 }}>
            <EditorContent editor={editor} />
          </div>
        </div>
      </div>
    </div>
  );
}

function DocsMenus({ editor, file, zoom, setZoom }: { editor: Editor; file: OfficeFile; zoom: number; setZoom: (z: number) => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const words = editor.getText().split(/\s+/).filter(Boolean).length;
  const menus: Record<string, { label: string; run: () => void; hint?: string }[]> = {
    File: [
      { label: "Show in Finder", run: () => api.officeReveal(file.id) },
    ],
    Edit: [
      { label: "Undo", hint: "⌘Z", run: () => editor.chain().focus().undo().run() },
      { label: "Redo", hint: "⇧⌘Z", run: () => editor.chain().focus().redo().run() },
      { label: "Select all", hint: "⌘A", run: () => editor.chain().focus().selectAll().run() },
    ],
    View: [50, 75, 90, 100, 125, 150].map((z) => ({ label: `${z}%${z === zoom ? "  ✓" : ""}`, run: () => setZoom(z) })),
    Insert: [
      { label: "Table", run: () => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run() },
      { label: "Horizontal line", run: () => editor.chain().focus().setHorizontalRule().run() },
      { label: "Link…", hint: "⌘K", run: () => { const url = window.prompt("Link address"); if (url) editor.chain().focus().setLink({ href: url }).run(); } },
    ],
    Format: [
      { label: "Bold", hint: "⌘B", run: () => editor.chain().focus().toggleBold().run() },
      { label: "Italic", hint: "⌘I", run: () => editor.chain().focus().toggleItalic().run() },
      { label: "Underline", hint: "⌘U", run: () => editor.chain().focus().toggleUnderline().run() },
      { label: "Strikethrough", run: () => editor.chain().focus().toggleStrike().run() },
      { label: "Clear formatting", run: () => editor.chain().focus().unsetAllMarks().clearNodes().run() },
    ],
    Tools: [{ label: `Word count: ${words.toLocaleString()} words`, run: () => undefined }],
  };
  return (
    <nav className="menubar" onMouseLeave={() => setOpen(null)}>
      {Object.entries(menus).map(([name, items]) => (
        <div key={name} className="menubar-item">
          <button className={open === name ? "on" : ""} onClick={() => setOpen(open === name ? null : name)}
                  onMouseEnter={() => open && setOpen(name)}>{name}</button>
          {open === name && (
            <div className="popover down menu-pop">
              {items.map((it) => <button key={it.label} className="menu-item" onClick={() => { it.run(); setOpen(null); }}>
                <span>{it.label}</span>{it.hint && <span className="desc kbd-hint">{it.hint}</span>}</button>)}
            </div>
          )}
        </div>
      ))}
    </nav>
  );
}

function DocsToolbar({ editor, zoom, setZoom, disabled }: { editor: Editor; zoom: number; setZoom: (z: number) => void; disabled: boolean }) {
  const st = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e.isActive("bold"), italic: e.isActive("italic"), underline: e.isActive("underline"), strike: e.isActive("strike"),
      bullet: e.isActive("bulletList"), ordered: e.isActive("orderedList"),
      h: [1, 2, 3].find((l) => e.isActive("heading", { level: l })) || 0,
      align: (["left", "center", "right", "justify"] as const).find((a) => e.isActive({ textAlign: a })) || "left",
      canUndo: e.can().undo(), canRedo: e.can().redo(),
    }),
  });
  const c = () => editor.chain().focus();
  const B = ({ on, title, onClick, children }: { on?: boolean; title: string; onClick: () => void; children: React.ReactNode }) => (
    <button className={`tb ${on ? "on" : ""}`} title={title} aria-label={title} aria-pressed={on} disabled={disabled}
            onMouseDown={(e) => e.preventDefault()} onClick={onClick}>{children}</button>
  );
  return (
    <div className="toolbar">
      <B title="Undo (⌘Z)" onClick={() => c().undo().run()}><I.Undo size={16} /></B>
      <B title="Redo (⇧⌘Z)" onClick={() => c().redo().run()}><I.Undo size={16} className="flip-x" /></B>
      <span className="tb-sep" />
      <select className="tb-select" value={zoom} onChange={(e) => setZoom(Number(e.target.value))} aria-label="Zoom">
        {[50, 75, 90, 100, 125, 150].map((z) => <option key={z} value={z}>{z}%</option>)}
      </select>
      <span className="tb-sep" />
      <select className="tb-select wide" value={st.h} disabled={disabled} aria-label="Text style"
              onChange={(e) => { const l = Number(e.target.value); if (l) c().toggleHeading({ level: l as 1 | 2 | 3 }).run(); else c().setParagraph().run(); }}>
        <option value={0}>Normal text</option><option value={1}>Heading 1</option><option value={2}>Heading 2</option><option value={3}>Heading 3</option>
      </select>
      <span className="tb-sep" />
      <B title="Bold (⌘B)" on={st.bold} onClick={() => c().toggleBold().run()}><b>B</b></B>
      <B title="Italic (⌘I)" on={st.italic} onClick={() => c().toggleItalic().run()}><i style={{ fontFamily: "Georgia, serif" }}>I</i></B>
      <B title="Underline (⌘U)" on={st.underline} onClick={() => c().toggleUnderline().run()}><u>U</u></B>
      <B title="Strikethrough" on={st.strike} onClick={() => c().toggleStrike().run()}><s>S</s></B>
      <label className="tb color" title="Text colour"><span>A</span>
        <input type="color" disabled={disabled} onChange={(e) => c().setColor(e.target.value).run()} /></label>
      <B title="Highlight" onClick={() => c().toggleHighlight().run()}><span className="hl">ab</span></B>
      <B title="Link (⌘K)" onClick={() => { const url = window.prompt("Link address"); if (url) c().setLink({ href: url }).run(); }}><I.External size={15} /></B>
      <span className="tb-sep" />
      {(["left", "center", "right", "justify"] as const).map((a) => (
        <B key={a} title={`Align ${a}`} on={st.align === a} onClick={() => c().setTextAlign(a).run()}><AlignIcon a={a} /></B>
      ))}
      <span className="tb-sep" />
      <B title="Bulleted list" on={st.bullet} onClick={() => c().toggleBulletList().run()}><ListIcon /></B>
      <B title="Numbered list" on={st.ordered} onClick={() => c().toggleOrderedList().run()}><ListIcon numbered /></B>
      <B title="Clear formatting" onClick={() => c().unsetAllMarks().run()}><span className="tx">T<sub>x</sub></span></B>
    </div>
  );
}

const AlignIcon = ({ a }: { a: "left" | "center" | "right" | "justify" }) => {
  const lines = { left: [[4, 20], [4, 14], [4, 18], [4, 12]], center: [[4, 20], [7, 17], [5, 19], [8, 16]],
                  right: [[4, 20], [10, 20], [6, 20], [12, 20]], justify: [[4, 20], [4, 20], [4, 20], [4, 20]] }[a];
  return <svg width="16" height="16" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" strokeLinecap="round">{lines.map(([x1, x2], i) => <line key={i} x1={x1} x2={x2} y1={5 + i * 4.5} y2={5 + i * 4.5} />)}</svg>;
};
const ListIcon = ({ numbered }: { numbered?: boolean }) => (
  <svg width="16" height="16" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none">
    {[6, 12, 18].map((y) => <line key={y} x1="9" x2="21" y1={y} y2={y} />)}
    {numbered ? <text x="2" y="9" fontSize="7" fill="currentColor" stroke="none">1</text> : [6, 12, 18].map((y) => <circle key={y} cx="4" cy={y} r="1.3" fill="currentColor" />)}
  </svg>
);
