import { t } from "./i18n";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Command, Source } from "./api";
import * as I from "./icons";

export type Attachment = { key: string; name: string; sub: string; state: "ready" | "busy" | "error"; onRemove?: () => void };
export type Mode = "accurate" | "fast";

type Props = {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop: () => void;
  busy: boolean;
  think: boolean;
  setThink: (v: boolean) => void;
  mode: Mode;
  setMode: (m: Mode) => void;
  source: Source;
  setSource: (s: Source) => void;
  web: boolean;
  setWeb: (w: boolean) => void;
  commands: Command[];
  files: string[];
  attachments: Attachment[];
  onFiles: (files: File[]) => void;
  onNewProject: () => void;
  placeholder: string;
  autoFocus?: boolean;
};

const ACCEPT = ".pdf,.docx,.pptx,.html,.htm,.txt,.md,.png,.jpg,.jpeg,.webp,.tif,.tiff,.bmp,.eml,.mbox";

/** The name a file is typed as in a command: its base name without extension, quoted if it has spaces. */
export const shortName = (name: string) => {
  const base = name.split("/").pop()!.replace(/\.[^.]+$/, "");
  return /\s/.test(base) ? `"${base}"` : base;
};

/** The answer modes, as the picker shows them. */
const MODES: { key: string; source: Source; mode?: Mode; label: string; desc: string; icon: (p: { size?: number }) => JSX.Element }[] = [
  { key: "chat", source: "chat", label: "Chat", icon: I.Chat,
    desc: "General assistant. Uses its own knowledge, your attached files and, if Web is on, the web" },
  { key: "docs", source: "docs", mode: "accurate", label: "Documents", icon: I.Shield,
    desc: "Answers only from your files. Every sentence cited and verified, or it says not found" },
  { key: "docs-fast", source: "docs", mode: "fast", label: "Documents · Fast", icon: I.Sparkle,
    desc: "Only from your files, one search, cited but not verified" },
  { key: "compare", source: "compare", label: "Compare", icon: I.Columns,
    desc: "Side-by-side table of 2 to 5 files. Type what to compare" },
];

type Item = { key: string; label: string; desc?: string; icon?: JSX.Element; apply: () => void };

export default function Composer(p: Props) {
  const area = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [plusOpen, setPlusOpen] = useState(false);
  const [modeOpen, setModeOpen] = useState(false);
  const [drag, setDrag] = useState(false);
  const [index, setIndex] = useState(0);
  const [dismissed, setDismissed] = useState("");

  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [p.value]);

  useEffect(() => {
    if (p.autoFocus) area.current?.focus();
  }, [p.autoFocus]);

  const items: Item[] = useMemo(() => {
    const cmd = p.value.match(/^\/([a-z]*)$/);
    if (cmd) {
      return p.commands.filter((c) => c.name.startsWith(cmd[1])).map((c) => ({
        key: c.name, label: `/${c.name}`, desc: c.description, apply: () => p.onChange(`/${c.name} `),
      }));
    }
    const ctx = p.value.match(/^\/(compare|summarize)\s+([\s\S]*)$/);
    if (ctx && p.files.length) {
      const parts = ctx[2].split(/\s+/);
      const last = parts[parts.length - 1];
      const used = new Set(parts.slice(0, -1).map((x) => x.toLowerCase()));
      if (ctx[1] === "summarize" && used.size >= 1) return [];
      if (ctx[1] === "compare" && used.size >= 5) return [];
      return p.files
        .filter((f) => !used.has(shortName(f).toLowerCase()))
        .filter((f) => !last || f.split("/").pop()!.toLowerCase().includes(last.toLowerCase().replace(/"/g, "")))
        .slice(0, 8)
        .map((f) => ({
          key: f, label: f, icon: <I.FileIcon size={15} />,
          apply: () => p.onChange(p.value.slice(0, p.value.length - last.length) + shortName(f) + " "),
        }));
    }
    return [];
  }, [p.value, p.commands, p.files]);

  const menuOpen = items.length > 0 && dismissed !== p.value;
  useEffect(() => setIndex(0), [items.length, p.value]);
  useEffect(() => {  // Esc stops a running answer, like Claude
    if (!p.busy) return;
    const onEsc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); p.onStop(); } };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [p.busy, p.onStop]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (menuOpen) {
      if (e.key === "ArrowDown") { e.preventDefault(); setIndex((i) => (i + 1) % items.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setIndex((i) => (i - 1 + items.length) % items.length); return; }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { e.preventDefault(); items[index]?.apply(); return; }
      if (e.key === "Escape") { e.preventDefault(); setDismissed(p.value); return; }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (!p.busy && p.value.trim()) p.onSend();
    }
  };

  const hint = p.value.match(/^\/(compare)\s+\S+\s+\S+/) && !menuOpen
    ? "Add what to compare after the files, or press Enter for the main terms" : null;

  return (
    <div className="composer-wrap">
      {menuOpen && (
        <div className="popover up slash-menu" role="listbox">
          <div className="menu-title">{p.value.startsWith("/") && !/\s/.test(p.value) ? "Commands" : "Files"}</div>
          {items.map((it, i) => (
            <button key={it.key} role="option" aria-selected={i === index}
                    className={`menu-item ${i === index ? "on" : ""}`}
                    onMouseEnter={() => setIndex(i)} onMouseDown={(e) => { e.preventDefault(); it.apply(); }}>
              {it.icon}
              <span className={it.desc ? "cmd" : ""}>{it.label}</span>
              {it.desc && <span className="desc">{it.desc}</span>}
            </button>
          ))}
        </div>
      )}
      <div className={`composer ${drag ? "drag" : ""}`}
           onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
           onDragLeave={() => setDrag(false)}
           onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) p.onFiles(Array.from(e.dataTransfer.files)); }}>
        {p.attachments.length > 0 && (
          <div className="attachments">
            {p.attachments.map((a) => (
              <div key={a.key} className={`attach ${a.state === "error" ? "error" : ""}`}>
                {a.state === "busy" ? <span className="spinner" /> : <I.FileIcon size={16} />}
                <div className="meta">
                  <span className="name" title={a.name}>{a.name}</span>
                  <span className="sub" title={a.sub}>{a.sub}</span>
                </div>
                {a.onRemove && <button className="x" aria-label={`Remove ${a.name}`} onClick={a.onRemove}><I.Close size={14} /></button>}
              </div>
            ))}
          </div>
        )}
        <textarea ref={area} rows={1} value={p.value} placeholder={p.placeholder} aria-label="Message"
                  onChange={(e) => p.onChange(e.target.value)} onKeyDown={onKeyDown} />
        <div className="composer-row">
          <div style={{ position: "relative" }}>
            <button className="tool round" aria-label="Add files or a project" title="Add files, a folder, or a command"
                    onClick={() => setPlusOpen((o) => !o)}><I.Plus /></button>
            {plusOpen && (
              <div className="popover up" style={{ left: 0 }} onMouseLeave={() => setPlusOpen(false)}>
                <button className="menu-item" onClick={() => { setPlusOpen(false); picker.current?.click(); }}>
                  <I.Paperclip /> <span>{t("Upload files")}<span className="desc">{t("PDF, Word, PowerPoint, text, images")}</span></span>
                </button>
                <button className="menu-item" onClick={() => { setPlusOpen(false); p.onNewProject(); }}>
                  <I.Folder /> <span>{t("New project from a folder")}<span className="desc">{t("Every file in it is read and kept in sync")}</span></span>
                </button>
                <div className="menu-sep" />
                <button className="menu-item" onClick={() => { setPlusOpen(false); p.setWeb(!p.web); if (!p.web) p.setSource("chat"); }}>
                  <I.Globe /> <span>{t("Web search")}<span className="desc">{t(p.web ? "On · only search words leave this Mac" : "Off · nothing leaves this Mac")}</span></span>
                  <span className={`switch ${p.web ? "on" : ""}`} aria-hidden="true" />
                </button>
                <button className="menu-item" onClick={() => { setPlusOpen(false); p.onChange("/"); area.current?.focus(); }}>
                  <I.Slash /> <span>{t("Commands")}<span className="desc">/compare, /summarize, /find…</span></span>
                </button>
              </div>
            )}
          </div>
          <input ref={picker} type="file" multiple accept={ACCEPT} hidden
                 onChange={(e) => { if (e.target.files?.length) p.onFiles(Array.from(e.target.files)); e.target.value = ""; }} />
          {p.source === "chat" && (
            <button className={`tool ${p.web ? "on" : ""}`} aria-pressed={p.web}
                    title={p.web ? "Web search is on: Vyum may search the web. Only the search words are sent; your files never leave this Mac."
                                 : "Web search is off: nothing leaves this Mac"}
                    onClick={() => p.setWeb(!p.web)}>
              <I.Globe size={15} /> {t("Web")}
            </button>
          )}
          <button className={`tool ${p.think ? "on" : ""}`} aria-pressed={p.think}
                  title="Thinking: the model reasons step by step before answering. Slower, better on hard questions."
                  onClick={() => p.setThink(!p.think)}>
            <I.Bulb size={16} /> {t("Think")}
          </button>
          <div style={{ position: "relative" }}>
            {(() => {
              const cur = MODES.find((m) => m.source === p.source && (!m.mode || m.mode === p.mode)) || MODES[0];
              return (
                <button className="tool" onClick={() => setModeOpen((o) => !o)} aria-haspopup="menu" title={t(cur.desc)}>
                  <cur.icon size={15} /> {t(cur.label)} <I.ChevronDown size={14} />
                </button>
              );
            })()}
            {modeOpen && (
              <div className="popover up" style={{ right: 0, width: "min(340px, 80vw)" }} onMouseLeave={() => setModeOpen(false)}>
                {MODES.map((m) => {
                  const on = m.source === p.source && (!m.mode || m.mode === p.mode);
                  return (
                    <button key={m.key} className={`menu-item ${on ? "on" : ""}`}
                            onClick={() => { p.setSource(m.source); if (m.mode) p.setMode(m.mode); setModeOpen(false); area.current?.focus(); }}>
                      <m.icon size={17} /> <span>{t(m.label)}<span className="desc">{t(m.desc)}</span></span>
                      {on && <I.Check size={15} />}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <span className="spacer" />
          {p.busy ? (
            <button className="send stop" aria-label="Stop" title={`${t("Stop")} (Esc)`} onClick={p.onStop}><I.Stop size={14} /></button>
          ) : (
            <button className="send" aria-label="Send" disabled={!p.value.trim()} onClick={p.onSend}><I.ArrowUp size={18} /></button>
          )}
        </div>
      </div>
      {hint && <div className="hint-row">{hint}</div>}
    </div>
  );
}
