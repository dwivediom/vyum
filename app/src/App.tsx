import { lang as uiLang, setLang, t } from "./i18n";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api, connect, inTauri, openExternal, type Chat, type ChatSummary, type CalcStep, type Citation, type Command, type Doc, type Health, type Job,
  type OfficeFile, type Project, type Result, type Settings, type Source,
} from "./api";
import Composer, { type Attachment, type Mode } from "./Composer";
import DocsWorkspace from "./Docs";
import SheetsWorkspace from "./Sheets";
import PdfWorkspace from "./Pdf";
import Mail from "./Mail";
import { OfficeFileView, OfficeHome } from "./Office";
import Overview from "./Overview";
import Rail, { type Section } from "./Rail";
import { FileRows, LiveView, ResultView, UserMessage, indexing, where, type Live } from "./Messages";
import * as I from "./icons";

type Route =
  | { view: "home" }
  | { view: "chat"; chatId: string }
  | { view: "projects" }
  | { view: "project"; projectId: string; chatId?: string }
  | { view: "office" }
  | { view: "office-file"; file: OfficeFile }
  | { view: "mail" }
  | { view: "overview" };

const load = <T,>(key: string, fallback: T): T => {
  try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
};
const save = (key: string, value: unknown) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const busyJob = (j: Job) => j.status === "queued" || j.status === "running";
const ago = (ts: number | null) => {
  if (!ts) return "never";
  const s = Date.now() / 1000 - ts;
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
};

export default function App() {
  const [phase, setPhase] = useState<"connecting" | "ready" | "failed">("connecting");
  const [failure, setFailure] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [commands, setCommands] = useState<Command[]>([]);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectChats, setProjectChats] = useState<ChatSummary[]>([]);
  const [allDocs, setAllDocs] = useState<Doc[]>([]);
  const [route, setRoute] = useState<Route>({ view: "home" });
  const [chat, setChat] = useState<Chat | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [uploads, setUploads] = useState<Job[]>([]);
  const [draft, setDraft] = useState("");
  const [mode, setModeState] = useState<Mode>(load("vyum.mode", "accurate"));
  // Web search and thinking start switched on at every launch; turning one off lasts until Vyum is closed.
  const [think, setThink] = useState(true);
  const [web, setWeb] = useState(true);
  const [sourcePref, setSourcePref] = useState<Source | null>(null);  // null: picked from what is attached
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [officeFiles, setOfficeFiles] = useState<OfficeFile[]>([]);
  const [source, setSource] = useState<Citation | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [newProject, setNewProject] = useState(false);
  const stopRun = useRef<(() => void) | null>(null);
  const uploadsRef = useRef<Job[]>([]);
  const end = useRef<HTMLDivElement>(null);
  uploadsRef.current = uploads;

  const setMode = (m: Mode) => { setModeState(m); save("vyum.mode", m); };
  const chatId = route.view === "chat" || route.view === "project" ? route.chatId : undefined;
  const projectId = route.view === "project" ? route.projectId : undefined;
  const project = projects.find((p) => p.id === projectId);
  // With files attached (or inside a project) questions go to Documents mode; otherwise Vyum is a general assistant.
  const hasFiles = project ? project.files.some((f) => f.doc) : !!chat?.files?.length || uploads.length > 0;
  const answerMode: Source = sourcePref ?? (hasFiles || projectId ? "docs" : "chat");

  const refreshLists = useCallback(async () => {
    const [c, p, d] = await Promise.all([api.chats(), api.projects(), api.documents()]);
    setChats(c); setProjects(p); setAllDocs(d);
  }, []);
  const refreshProjectChats = useCallback(async (pid?: string) => {
    setProjectChats(pid ? await api.chats(pid) : []);
  }, []);

  useEffect(() => {
    connect()
      .then(async () => {
        const [h, cmds] = await Promise.all([api.health(), api.commands()]);
        setHealth(h); setCommands(cmds);
        await refreshLists();
        setPhase("ready");
      })
      .catch((e) => { setFailure(String(e.message || e)); setPhase("failed"); });
  }, [refreshLists]);

  // Load the open chat (skip when it is already the one in state, so streaming updates are kept).
  useEffect(() => {
    if (!chatId) { setChat(null); return; }
    if (chat?.id === chatId) return;
    api.chat(chatId).then(setChat).catch(() => setRoute({ view: "home" }));
  }, [chatId]);

  useEffect(() => { refreshProjectChats(projectId); }, [projectId, refreshProjectChats]);

  // Poll a project while its folder scan runs.
  useEffect(() => {
    if (!project?.scan) return;
    const t = setTimeout(async () => {
      const fresh = await api.project(project.id).catch(() => null);
      if (fresh) setProjects((ps) => ps.map((p) => (p.id === fresh.id ? fresh : p)));
      if (fresh && !fresh.scan) setAllDocs(await api.documents());
    }, 1500);
    return () => clearTimeout(t);
  }, [project]);

  // Poll file uploads attached to the open chat.
  useEffect(() => {
    if (!uploads.some(busyJob)) return;
    const t = setTimeout(async () => {
      const next = await Promise.all(uploads.map((j) => (busyJob(j) ? api.job(j.id).catch(() => j) : j)));
      setUploads(next);
      if (next.some((j, i) => j.status === "done" && uploads[i].status !== "done") && chatId) {
        setChat(await api.chat(chatId)); setAllDocs(await api.documents());
      }
    }, 1000);
    return () => clearTimeout(t);
  }, [uploads, chatId]);

  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [chat?.messages.length, live?.draft, live?.status]);

  const go = (r: Route) => { setRoute(r); setSource(null); setDraft(""); setUploads([]); setSourcePref(null); };

  const ensureChat = async (): Promise<string> => {
    if (chatId) return chatId;
    const c = await api.createChat(projectId ?? null);
    setChat(c);
    setRoute(projectId ? { view: "project", projectId, chatId: c.id } : { view: "chat", chatId: c.id });
    return c.id;
  };

  const send = async () => {
    const text = draft.trim();
    if (!text || live) return;
    const id = await ensureChat();
    setDraft("");
    setChat((c) => c && { ...c, messages: [...c.messages, { role: "user", text, ts: Date.now() / 1000 }] });
    const asChat = answerMode === "chat" && !text.startsWith("/");
    setLive({ status: "Starting", thinking: "", draft: "", citations: [], started: Date.now(), chat: asChat });
    while (uploadsRef.current.some(busyJob)) {
      setLive((l) => l && { ...l, status: "Waiting for attached files to finish reading" });
      await sleep(800);
    }
    const controller = new AbortController();
    const runId = crypto.randomUUID();
    let partial = "", cites: Citation[] = [], done = false, calcs: CalcStep[] = [];
    stopRun.current = () => {  // Stop: ask the engine to end; if it has not within 3 s, drop the connection
      api.stop(runId);
      setLive((l) => l && { ...l, status: "Stopping" });
      setTimeout(() => { if (!done) controller.abort(); }, 3000);
    };
    const push = (result: Result) =>
      setChat((c) => c && { ...c, messages: [...c.messages, { role: "assistant", result, ts: Date.now() / 1000 }] });
    try {
      await api.send(id, text, { mode, think, source: answerMode, web, run_id: runId }, (ev) => {
        if (ev.type === "draft") partial += ev.text;
        if (ev.type === "sources") cites = ev.citations;
        if (ev.type === "calc") calcs = ev.steps;
        if (ev.type === "result") done = true;
        if (ev.type === "status") setLive((l) => l && { ...l, status: ev.text });
        else if (ev.type === "calc") setLive((l) => l && { ...l, calc: ev.steps });
        else if (ev.type === "sources") setLive((l) => l && { ...l, citations: ev.citations });
        else if (ev.type === "thinking") setLive((l) => l && { ...l, thinking: l.thinking + ev.text, status: "Thinking" });
        else if (ev.type === "thinking_done") setLive((l) => l && { ...l, thinkingSeconds: ev.seconds, status: "Writing the answer" });
        else if (ev.type === "draft") setLive((l) => l && { ...l, draft: l.draft + ev.text, status: "Writing the answer" });
        else if (ev.type === "result") { push(ev.result); setLive(null); }
      }, controller.signal);
    } catch (e) {
      if (!done) push(controller.signal.aborted
        ? { kind: "chat", answer: partial.trim(), citations: cites, web_queries: [], used_files: 0, timings: {}, stopped: true,
            unverified: answerMode !== "chat", calculations: calcs }
        : { kind: "error", error: String((e as Error).message) });
    } finally {
      done = true;
      setLive(null);
      stopRun.current = null;
      projectId ? refreshProjectChats(projectId) : setChats(await api.chats());
    }
  };

  const onFiles = async (files: File[]) => {
    const id = await ensureChat();
    try {
      const jobs = await api.attach(id, files);
      setUploads((u) => [...u.filter(busyJob), ...jobs]);
    } catch (e) {
      setUploads((u) => [...u, { id: crypto.randomUUID(), name: files.map((f) => f.name).join(", "), kind: "ingest",
                                 status: "error", step: "", error: String((e as Error).message) }]);
    }
  };

  const attachments: Attachment[] = [
    ...(chat?.files || []).map((d) => ({
      key: d.id, name: d.name, state: "ready" as const,
      sub: indexing(d) ?? `${d.pages.toLocaleString()} ${d.unit === "line" ? "lines" : "p"} · ${d.chunks.toLocaleString()} passages${d.figures ? ` · ${d.figures} figures` : ""}${d.quarantined ? " · ⚠ quarantined text" : ""}`,
      onRemove: async () => { if (chat) { await api.detach(chat.id, d.id); setChat(await api.chat(chat.id)); } },
    })),
    ...uploads.filter((j) => j.status !== "done").map((j) => ({
      key: j.id, name: j.name, state: (j.status === "error" ? "error" : "busy") as Attachment["state"],
      sub: j.status === "error" ? j.error || "failed" : j.step,
      onRemove: j.status === "error" ? () => setUploads((u) => u.filter((x) => x.id !== j.id)) : undefined,
    })),
  ];

  const scopeFiles = useMemo(() => {
    if (project) return project.files.filter((f) => f.doc).map((f) => f.path);
    if (chat?.files?.length) return chat.files.map((d) => d.name);
    return allDocs.map((d) => d.name);
  }, [project, chat, allDocs]);

  const createProject = async (folder: string, name: string) => {
    const p = await api.createProject(folder, name || undefined);
    setProjects((ps) => [p, ...ps]);
    setNewProject(false);
    go({ view: "project", projectId: p.id });
  };

  const rescan = async () => {
    if (!project) return;
    const job = await api.scanProject(project.id);
    setProjects((ps) => ps.map((p) => (p.id === project.id ? { ...p, scan: job } : p)));
  };

  const removeChat = async (c: ChatSummary) => {
    if (!window.confirm(`Delete “${c.title}”? Its attached files are removed too.`)) return;
    await api.deleteChat(c.id);
    if (c.id === chatId) go(c.project_id ? { view: "project", projectId: c.project_id } : { view: "home" });
    await refreshLists();
    if (c.project_id) refreshProjectChats(c.project_id);
  };

  const removeProject = async (p: Project) => {
    if (!window.confirm(`Remove project “${p.name}”? Its chats and index are deleted. Files in ${p.folder} are not touched.`)) return;
    await api.deleteProject(p.id);
    go({ view: "projects" });
    await refreshLists();
  };

  if (phase !== "ready") {
    return (
      <div className="splash">
        <span className="mark lg">V</span>
        {phase === "connecting" ? <p>Starting the local engine…</p> : <p className="error-text">Could not start: {failure}</p>}
      </div>
    );
  }

  const placeholder = (normal: string) =>
    answerMode === "compare" ? t("What should be compared? e.g. payment terms and warranty (attach 2 to 5 files with +)")
      : answerMode === "docs" && !hasFiles && !projectId ? t("Ask about your documents (attach files with +), or type / for commands")
      : normal;

  const composer = (placeholder: string, autoFocus = false) => (
    <Composer value={draft} onChange={setDraft} onSend={send} onStop={() => stopRun.current?.()} busy={!!live}
              think={think} setThink={setThink} mode={mode} setMode={setMode} source={answerMode} setSource={setSourcePref}
              web={web} setWeb={setWeb} commands={commands} files={scopeFiles}
              attachments={attachments} onFiles={onFiles} onNewProject={() => setNewProject(true)}
              placeholder={placeholder} autoFocus={autoFocus} />
  );

  const thread = chat && (chat.messages.length > 0 || live) ? (
    <div className="scroll">
      <div className="column">
        <div className="messages">
          {chat.messages.map((m, i) => m.role === "user"
            ? <UserMessage key={i} text={m.text} />
            : <div key={i} className="msg-ai"><ResultView result={m.result} onCite={setSource} onInsert={setDraft}
                                                           onJobDone={() => project && api.project(project.id).then((f) => setProjects((ps) => ps.map((p) => (p.id === f.id ? f : p))))} /></div>)}
          {live && <div className="msg-ai"><LiveView live={live} onCite={setSource} /></div>}
          <div ref={end} />
        </div>
      </div>
    </div>
  ) : null;

  const section: Section = route.view === "projects" || route.view === "project" ? "projects"
    : route.view === "office" || route.view === "office-file" ? "office"
    : route.view === "mail" ? "mail" : route.view === "overview" ? "overview" : "chat";
  const sideless = section === "office" || section === "mail" || section === "overview";
  const openOffice = (f: OfficeFile) => { api.officeFiles().then((r) => setOfficeFiles(r.files)).catch(() => undefined); go({ view: "office-file", file: f }); };

  let main: JSX.Element;
  if (route.view === "office") {
    main = <OfficeHome onOpen={openOffice} docs={allDocs} think={think} />;
  } else if (route.view === "office-file") {
    main = route.file.kind === "docx"
      ? <DocsWorkspace key={route.file.id} file={route.file} think={think} setThink={setThink} onBack={() => go({ view: "office" })} />
      : route.file.kind === "xlsx"
      ? <SheetsWorkspace key={route.file.id} file={route.file} think={think} setThink={setThink} onBack={() => go({ view: "office" })} />
      : route.file.kind === "pdf"
      ? <PdfWorkspace key={route.file.id} file={route.file} think={think} setThink={setThink} onBack={() => go({ view: "office" })} onOpen={openOffice} />
      : <OfficeFileView key={route.file.id} file={route.file} files={officeFiles} think={think} setThink={setThink}
                        onBack={() => go({ view: "office" })} onOpen={openOffice} />;
  } else if (route.view === "mail") {
    main = <Mail docs={allDocs} think={think} onAskMail={() => { go({ view: "home" }); setSourcePref("docs"); setDraft("What did "); }} />;
  } else if (route.view === "overview") {
    main = <Overview web={web} onGo={(w) => {
      if (w === "chat") go({ view: "home" });
      else if (w === "newProject") setNewProject(true);
      else if (w === "office" || w === "mail" || w === "projects") go({ view: w });
      else go({ view: "chat", chatId: w.chat });
    }} />;
  } else if (route.view === "projects") {
    main = (
      <div className="scroll"><div className="column">
        <div className="page-head">
          <h1>Projects</h1><span className="spacer" />
          <button className="btn primary" onClick={() => setNewProject(true)}><I.Plus size={16} /> New project</button>
        </div>
        {projects.length === 0 ? (
          <div className="empty-state">
            <h3>Point Vyum at a folder</h3>
            <p>Every PDF, Word file, slide deck, text file and image in it is read, indexed and kept in sync.<br />
               Then ask questions, or use <span className="kbd">/compare</span>, <span className="kbd">/summarize</span> and <span className="kbd">/find</span> across all of it.</p>
            <button className="btn primary" onClick={() => setNewProject(true)}><I.Folder size={16} /> Choose a folder</button>
          </div>
        ) : (
          <div className="cards">
            {projects.map((p) => (
              <button key={p.id} className="pcard" onClick={() => go({ view: "project", projectId: p.id })}>
                <span className="t"><I.Folder size={17} /> {p.name}</span>
                <span className="p" title={p.folder}>{p.folder}</span>
                <span className="s">{p.files.filter((f) => f.doc).length} files · {p.scan ? "scanning…" : `scanned ${ago(p.last_scan)}`}</span>
              </button>
            ))}
          </div>
        )}
      </div></div>
    );
  } else if (route.view === "project" && project) {
    const ok = project.files.filter((f) => f.doc);
    const bad = project.files.filter((f) => f.error);
    main = (
      <>
        <header className="topbar">
          <div className="crumb">
            <button onClick={() => go({ view: "projects" })}>Projects</button><I.Chevron size={13} />
            <button onClick={() => go({ view: "project", projectId: project.id })}>{project.name}</button>
            {chat && route.chatId && <><I.Chevron size={13} /><span className="title">{chat.title}</span></>}
          </div>
        </header>
        {thread ?? (
          <div className="scroll"><div className="column">
            <div className="project-head">
              <div className="row">
                <I.Folder size={20} /><h2>{project.name}</h2><span className="spacer" />
                <button className="btn secondary" onClick={rescan} disabled={!!project.scan}><I.Refresh size={15} /> Rescan</button>
                <button className="icon-btn" title="Remove project" onClick={() => removeProject(project)}><I.Trash size={16} /></button>
              </div>
              <div className="path">{project.folder}</div>
              <div className="scanbar">
                {project.scan
                  ? <><span className="spinner" /> {project.scan.step}</>
                  : <>{ok.length} files indexed · scanned {ago(project.last_scan)}{bad.length ? ` · ${bad.length} could not be read` : ""}</>}
              </div>
            </div>
            {projectChats.length > 0 && (
              <>
                <div className="section-title">Chats in this project</div>
                <div className="file-list">
                  {projectChats.map((c) => (
                    <button key={c.id} className="file-row" onClick={() => go({ view: "project", projectId: project.id, chatId: c.id })}>
                      <I.Chat size={15} /><span className="name">{c.title}</span><span className="meta">{ago(c.updated_at)}</span>
                    </button>
                  ))}
                </div>
              </>
            )}
            <div className="section-title">Files</div>
            {ok.length ? <FileRows files={ok.map((f) => ({ ...f.doc!, name: f.path }))} />
                       : <p className="muted">{project.scan ? "Reading the folder…" : "No supported files in this folder yet."}</p>}
            {bad.map((f) => <div key={f.path} className="file-row"><I.Warn size={14} /><span className="name">{f.path}</span><span className="meta warn">{f.error}</span></div>)}
          </div></div>
        )}
        <div className="composer-dock">{composer(placeholder(`Ask about ${project.name}, or type / for commands`), true)}</div>
      </>
    );
  } else if (route.view === "chat" && chat) {
    main = (
      <>
        <header className="topbar"><span className="title">{chat.title}</span></header>
        {thread ?? <div className="home" />}
        <div className="composer-dock">{composer(placeholder(t("Reply, or type / for commands")), true)}</div>
      </>
    );
  } else {
    const hour = new Date().getHours();
    const greeting = hour < 5 ? "Working late" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    main = (
      <div className="home">
        <div className="greeting"><span className="mark lg">V</span><h1>{t(greeting)}{uiLang === "hi" ? "।" : "."} {t("How can I help?")}</h1></div>
        {composer(placeholder(t("Ask anything, attach files with +, or type / for commands")), true)}
        <div className="suggestions">
          <button className="chip" onClick={() => setNewProject(true)}><I.Folder size={15} /> {t("Open a folder as a project")}</button>
          <button className="chip" onClick={() => setSourcePref("compare")}><I.Columns size={15} /> {t("Compare documents")}</button>
          <button className="chip" onClick={() => setDraft("/summarize ")}><I.FileIcon size={15} /> {t("Summarize a file")}</button>
          <button className="chip" onClick={() => setDraft("/find ")}><I.Search size={15} /> {t("Find exact words")}</button>
        </div>
      </div>
    );
  }

  return (
    <div className={`shell ${collapsed || sideless ? "collapsed" : ""} ${source && !sideless ? "with-source" : ""}`}>
      <Rail section={section} web={web} onHome={() => go({ view: "home" })} onSettings={() => setSettingsOpen(true)}
            onSection={(s) => {
              if (s === "chat") go({ view: "home" });
              else if (s === "projects") go({ view: "projects" });
              else go({ view: s });
              if (s === "chat" || s === "projects") setCollapsed(false);
            }} />
      <aside className="sidebar">
        <div className="side-top">
          <span className="brand">Vyum</span><span className="spacer" />
          <button className="icon-btn" title="Hide sidebar" onClick={() => setCollapsed(true)}><I.Sidebar size={17} /></button>
        </div>
        <button className="new-chat" onClick={() => go({ view: "home" })}><span className="plus"><I.Plus size={15} /></span> {t("New chat")}</button>
        <div className="side-scroll">
          <div className="side-label">{t("Projects")} <button title="New project" onClick={() => setNewProject(true)}><I.Plus size={14} /></button></div>
          {projects.length === 0 && <div className="item muted" style={{ cursor: "default" }}>{t("No projects yet")}</div>}
          {projects.map((p) => (
            <div key={p.id}>
              <button className={`item ${projectId === p.id && !chatId ? "on" : ""}`} onClick={() => go({ view: "project", projectId: p.id })}>
                <I.Folder size={15} /><span className="label">{p.name}</span>{p.scan && <span className="spinner" />}
              </button>
              {projectId === p.id && projectChats.map((c) => (
                <div key={c.id} className={`item sub ${chatId === c.id ? "on" : ""}`} onClick={() => go({ view: "project", projectId: p.id, chatId: c.id })}>
                  <span className="label">{c.title}</span>
                  <button className="x" title="Delete chat" onClick={(e) => { e.stopPropagation(); removeChat(c); }}><I.Trash size={13} /></button>
                </div>
              ))}
            </div>
          ))}
          <div className="side-label">{t("Recents")}</div>
          {chats.length === 0 && <div className="item muted" style={{ cursor: "default" }}>{t("Your chats appear here")}</div>}
          {chats.map((c) => (
            <div key={c.id} className={`item ${chatId === c.id && route.view === "chat" ? "on" : ""}`} onClick={() => go({ view: "chat", chatId: c.id })}>
              <span className="label">{c.title}</span>
              <button className="x" title="Delete chat" onClick={(e) => { e.stopPropagation(); removeChat(c); }}><I.Trash size={13} /></button>
            </div>
          ))}
        </div>
        <div className="side-foot">
          <span className={`dot ${health?.ok ? (web ? "web" : "ok") : ""}`} />
          <span className="spacer-text">{!health?.ok ? "Models missing: run scripts/setup_models.sh"
            : t(web ? "Web search on · files stay on this Mac" : "Offline · models ready")}</span>
        </div>
      </aside>

      <main className="main">
        {collapsed && !sideless && (
          <button className="icon-btn" style={{ position: "absolute", top: 11, left: 12, zIndex: 5 }} title="Show sidebar"
                  onClick={() => setCollapsed(false)}><I.Sidebar size={17} /></button>
        )}
        {main}
      </main>

      {source && !sideless && <SourcePanel citation={source} onClose={() => setSource(null)} />}
      {newProject && <NewProjectDialog onCancel={() => setNewProject(false)} onCreate={createProject} />}
      {settingsOpen && <SettingsDialog onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}

function NewProjectDialog({ onCancel, onCreate }: { onCancel: () => void; onCreate: (folder: string, name: string) => Promise<void> }) {
  const [folder, setFolder] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const browse = async () => {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const picked = await open({ directory: true, multiple: false, title: "Choose a folder for this project" });
      if (typeof picked === "string") {
        setFolder(picked);
        if (!name) setName(picked.split("/").filter(Boolean).pop() || "");
      }
    } catch (e) { setError(String((e as Error).message || e)); }
  };

  const submit = async () => {
    setBusy(true); setError("");
    try { await onCreate(folder.trim(), name.trim()); } catch (e) { setError(String((e as Error).message)); setBusy(false); }
  };

  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className="dialog" role="dialog" aria-label="New project">
        <h2>New project</h2>
        <p className="muted" style={{ margin: 0 }}>Vyum reads every supported file in the folder and keeps the index in sync. Your files are never modified or uploaded.</p>
        <div style={{ display: "flex", gap: 8 }}>
          <input style={{ flex: 1 }} value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="/Users/you/Documents/Contracts" aria-label="Folder path" autoFocus />
          {inTauri() && <button className="btn secondary" onClick={browse}><I.Folder size={15} /> Browse</button>}
        </div>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Project name (optional)" aria-label="Project name" />
        {error && <div className="error-text">{error}</div>}
        <div className="actions">
          <button className="btn secondary" onClick={onCancel}>Cancel</button>
          <button className="btn primary" disabled={!folder.trim() || busy} onClick={submit}>{busy ? "Creating…" : "Create project"}</button>
        </div>
      </div>
    </div>
  );
}

function SettingsDialog({ onClose }: { onClose: () => void }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [check, setCheck] = useState<{ busy?: boolean; ok?: boolean; text?: string }>({});
  useEffect(() => { api.settings().then((x) => setUrl(x.searxng_url)).catch((e) => setError(String(e.message))); }, []);
  const test = async () => {
    setCheck({ busy: true });
    const r = await api.checkSearch(url.trim()).catch((e) => ({ ok: false, error: String(e.message) }));
    setCheck(r.ok ? { ok: true, text: `Connected · ${(r as { results?: number }).results ?? 0} results for a test search` }
                  : { ok: false, text: (r as { error?: string }).error || "Not reachable" });
  };
  const saveAll = async () => {
    setError("");
    try { await api.saveSettings({ searxng_url: url.trim() }); onClose(); } catch (e) { setError(String((e as Error).message)); }
  };
  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="dialog" role="dialog" aria-label="Settings">
        <h2>{t("Settings")}</h2>
        <div className="section-title" style={{ margin: 0 }}>{uiLang === "hi" ? "भाषा · Language" : "Language · भाषा"}</div>
        <div className="lang-switch">
          {([["en", "English"], ["hi", "हिन्दी"]] as const).map(([code, label]) => (
            <button key={code} className={`radio ${uiLang === code ? "on" : ""}`} onClick={() => { if (uiLang !== code) setLang(code); }}>{label}</button>
          ))}
        </div>
        <p className="muted small" style={{ margin: 0 }}>{uiLang === "hi" ? "आप किसी भी भाषा में पूछें — हिन्दी, Hinglish, मराठी, தமிழ், বাংলা… Vyum उसी भाषा में जवाब देगा।" : "Ask in any language — Hindi, Hinglish, Marathi, Tamil, Bengali… Vyum answers in the language you ask in."}</p>
        <div className="section-title" style={{ margin: 0 }}>{t("Web search · SearXNG")}</div>
        <p className="muted" style={{ margin: 0 }}>{t("Vyum searches the web through SearXNG, a search engine you run yourself. It is used only in Chat mode, only when Web is on, and only the search words are sent to it. Your files and conversations stay on this Mac.")}</p>
        <div style={{ display: "flex", gap: 8 }}>
          <input style={{ flex: 1 }} value={url} onChange={(e) => { setUrl(e.target.value); setCheck({}); }}
                 aria-label="SearXNG address" placeholder="http://127.0.0.1:8888" />
          <button className="btn secondary" onClick={test} disabled={!url.trim() || check.busy}>{check.busy ? "Testing…" : t("Test")}</button>
        </div>
        {check.text && <div className={check.ok ? "ok-text" : "error-text"}>{check.text}</div>}
        {error && <div className="error-text">{error}</div>}
        <div className="actions">
          <button className="btn secondary" onClick={onClose}>{t("Cancel")}</button>
          <button className="btn primary" onClick={saveAll} disabled={!url.trim()}>{t("Save")}</button>
        </div>
      </div>
    </div>
  );
}

function SourcePanel({ citation: c, onClose }: { citation: Citation; onClose: () => void }) {
  if (c.kind === "web") {
    return (
      <aside className="source">
        <header>
          <div>
            <div className="title">{c.doc_name}</div>
            <div className="muted" style={{ fontSize: 12.5 }}><I.Globe size={12} /> {c.section}</div>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close source"><I.Close size={16} /></button>
        </header>
        <blockquote><span className="tag">From the web page, read just now</span>{c.text}</blockquote>
        {c.url && <button className="btn secondary" onClick={() => openExternal(c.url!)}><I.External size={15} /> Open the page in your browser</button>}
        {c.url && <div className="muted" style={{ fontSize: 12, wordBreak: "break-all" }}>{c.url}</div>}
      </aside>
    );
  }
  const ext = c.doc_name.toLowerCase().split(".").pop();
  return (
    <aside className="source">
      <header>
        <div>
          <div className="title">{c.doc_name}</div>
          <div className="muted" style={{ fontSize: 12.5 }}>{where(c.doc_name, c.page).replace(/^p\./, "Page")}{c.page_end !== c.page ? `–${c.page_end.toLocaleString()}` : ""}{c.section ? ` · ${c.section}` : ""}</div>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Close source"><I.Close size={16} /></button>
      </header>
      <blockquote>{c.kind === "figure" && <span className="tag">Figure read by the vision model</span>}{c.text}</blockquote>
      {ext === "pdf" && (
        <>
          <iframe key={`${c.doc_id}-${c.page}`} title="Document page" src={api.pageUrl(c.doc_id, c.page)} />
          <a className="muted" style={{ fontSize: 12.5 }} href={api.fileUrl(c.doc_id, c.page)} target="_blank" rel="noreferrer">Open the full PDF at this page</a>
        </>
      )}
      {["png", "jpg", "jpeg", "webp"].includes(ext || "") && <img alt={c.doc_name} src={api.fileUrl(c.doc_id)} />}
    </aside>
  );
}
