// Typed client for the engine. In the desktop app the port and token come from the Tauri shell;
// in a plain browser (development) they come from ?port=&token= or VITE_ENGINE_PORT / VITE_ENGINE_TOKEN.
import { invoke } from "@tauri-apps/api/core";

export type Doc = {
  id: string; name: string; ext: string; pages: number; chunks: number; figures: number; quarantined?: number;
  status?: "indexing" | "ready"; checkpoint?: number; unit?: "page" | "line"; eta_s?: number | null;
};
export type OutlineEntry = { title: string; first: number; last: number; passages: number };
export type OutlineSeries = { pattern: string; count: number; first: number; last: number; first_title: string; last_title: string };
export type Job = {
  id: string; name: string; kind: string; status: "queued" | "running" | "done" | "error"; step: string;
  error?: string; result?: any;
};
export type Sentence = { text: string; citations: number[]; verified: boolean | null };
export type Citation = {
  n: number; doc_id: string; doc_name: string; page: number; page_end: number;
  section: string; kind: string; text: string; score: number; url?: string;
};
export type Command = { name: string; usage: string; description: string; examples: string[] };
export type Cell = { text: string; citations: number[]; verified: boolean | null };

export type AnswerResult = {
  kind: "answer"; status: "answered" | "not_found"; answer: string; reason?: string; sentences: Sentence[];
  citations: Citation[]; searched: string[]; confidence: string | null; dropped: { text: string; why: string }[];
  timings: Record<string, number>; thinking?: string; resolved_question?: string | null; title?: string;
};
export type CompareResult = {
  kind: "compare"; status: "answered" | "not_found"; reason?: string; aspects: string;
  documents: { id: string; name: string; label: string }[];
  rows: { topic: string; cells: Record<string, Cell>; differs: boolean }[];
  summary: Sentence[]; citations: Citation[]; timings: Record<string, number>;
};
export type ChatResult = {
  kind: "chat"; answer: string; citations: Citation[]; web_queries: string[]; web_error?: string | null;
  used_files: number; timings: Record<string, number>; thinking?: string;
  stopped?: boolean; unverified?: boolean;   // Stop was pressed: the part written so far
  calculations?: CalcStep[];                  // computed by Vyum's calculator before the answer was written
};
export type DashSeries = { name: string; kind: string; data: (number | null)[] };
export type DashChart = { id: number; type: string; title: string; dimension?: string; filter_key?: string; categories?: string[];
  series?: DashSeries[]; points?: number[][]; measures?: { label: string; kind: string }[] };
export type DashData = { title: string; source: string; rows: number; total_rows: number; filters: Record<string, string[]>;
  kpis: { label: string; value: number | null; display: string; kind: string }[]; charts: DashChart[];
  slicers: { column: string; values: string[] }[] };
export type SheetTarget = { sheet: string; range: string };
export type TodoState = "pending" | "running" | "done" | "failed";
export type TodoItem = { text: string; state: TodoState; note?: string };
/** One edit the PDF editor makes on screen (planned by the engine, see engine/pdflive.py). */
export type LiveStep = { do: string; label: string; [k: string]: unknown };
export type CalcStep = { label: string; expr: string; result: string | null; error: string | null; name?: string };
export type Source = "chat" | "docs" | "compare";
export type Settings = { searxng_url: string };
export type OfficeKind = "docx" | "xlsx" | "pptx" | "md" | "html" | "pdf";
export type OfficeFile = {
  id: string; name: string; path: string; kind: OfficeKind; origin: "created" | "opened"; prompt?: string | null;
  created_at: number; updated_at: number; modified_at?: number; size?: number; exists: boolean;
  versions: { path: string; note: string; ts: number }[]; doc_id?: string | null; match?: string;
};
export type Chart = {
  id?: string; type: "column" | "bar" | "line" | "area" | "pie" | "doughnut" | "scatter" | "radar"; range: string; sheet?: string | null;
  title?: string; x: number; y: number; w: number; h: number; embedded?: boolean; series?: string[];
};
export type MailAccount = { id: string; email: string; name: string; provider: string; status?: { state?: string; step?: string; error?: string; last_sync?: number } };
export type MailFolder = { name: string; role: string | null; total: number; unread: number };
export type MailAddr = { name: string; email: string };
export type MailMsg = {
  id: number; account: string; folder: string; uid: number; msgid: string; thread: string; from_name: string; from_addr: string;
  subject: string; date_ts: number; snippet: string; seen: boolean; flagged: boolean; answered: boolean; has_att: boolean; count?: number;
  to: MailAddr[]; cc: MailAddr[]; text?: string; has_html?: boolean; attachments?: { index: number; name: string; type: string; size: number; inline: boolean }[];
};
export type MailDetect = {
  email?: string; provider?: string; method?: "oauth" | "password"; label?: string; configured?: boolean; error?: string;
  imap?: [string, number]; smtp?: [string, number, string]; found?: string; guessed?: boolean; hint?: string; help?: string;
};
export type MailProvider = { label: string; help: string; note: string; imap: [string, number]; smtp: [string, number, string] };
export type Deck = {
  size: { cx: number; cy: number }; stamp: number;
  pages: { index: number; id: string; notes: string; elements: { id: string; type: string; box: { x: number; y: number; cx: number; cy: number }; text: string; fontSize?: number }[] }[];
};
export type OfficeStatus = { installed: boolean; app: string; office_dir: string };
export type OfficeResult = {
  kind: "office"; action: "created" | "edited"; file: OfficeFile; summary?: string; changes?: string[];
  timings: Record<string, number>; thinking?: string | null;
};
export type MailResult = {
  kind: "mail"; mode: "new" | "reply" | "summarize"; subject?: string; body?: string; summary?: string;
  error?: string; timings?: Record<string, number>; thinking?: string | null;
};
export type Overview = {
  documents: number; indexing: number; pages: number; passages: number; figures: number; by_type: Record<string, number>;
  projects: number; chats: number; questions: number; activity_14d: number[]; storage_bytes: number;
  recent_documents: { id: string; name: string; ext: string; added_at: number }[];
  recent_chats: ChatSummary[]; office: OfficeStatus; web: { searxng_url: string; running: boolean };
  models: Record<string, string>;
};
export type FindHit = { doc_id: string; doc_name: string; page: number; section: string; snippet: string; text: string; kind: string };
export type Result =
  | AnswerResult
  | CompareResult
  | ChatResult
  | { kind: "find"; query: string; hits: FindHit[] }
  | { kind: "files"; files: Doc[] }
  | { kind: "help"; commands: Command[] }
  | { kind: "instructions"; command: string | null; title?: string; note?: string; usage?: string; description?: string;
      examples: string[]; files: string[]; commands: Command[] }
  | { kind: "job"; job: Job; text: string }
  | { kind: "outline"; doc_name: string; unit: string; pages: number; sections: number; parts: OutlineEntry[];
      more_parts: number; series: OutlineSeries[] }
  | OfficeResult
  | MailResult
  | { kind: "error"; error: string };

export type ChatMessage = { role: "user"; text: string; ts: number } | { role: "assistant"; result: Result; ts: number };
export type ChatSummary = { id: string; title: string; project_id: string | null; updated_at: number; files: number; messages: number };
export type Chat = Omit<ChatSummary, "files" | "messages"> & { doc_ids: string[]; messages: ChatMessage[]; files: Doc[] };
export type ProjectFile = { path: string; error?: string | null; doc?: Doc };
export type Project = {
  id: string; name: string; folder: string; created_at: number; last_scan: number | null;
  files: ProjectFile[]; scan: Job | null;
};
export type Health = { ok: boolean; models: Record<string, boolean | string>; documents: number };

export type StreamEvent =
  | { type: "status"; text: string }
  | { type: "sources"; citations: Citation[] }
  | { type: "thinking"; text: string }
  | { type: "thinking_done"; seconds: number }
  | { type: "draft"; text: string }
  | { type: "draft_format"; format: "html" | "markdown"; base?: string }
  | { type: "result"; result: Result }
  | { type: "stopped" }
  | { type: "calc"; steps: CalcStep[] }
  | { type: "plan"; attempt: number; items: string[] }
  | { type: "file_restored" }
  | { type: "plan_step"; index: number; state: TodoState; note?: string; target?: SheetTarget | null }
  | { type: "done" };

let base = "";
let token = "";

export const inTauri = () => "__TAURI_INTERNALS__" in window;

export async function connect(): Promise<void> {
  if (inTauri()) {
    for (;;) {
      const info = await invoke<{ ready: boolean; port: number | null; token: string; error: string | null }>("engine_info");
      if (info.error) throw new Error(info.error);
      if (info.ready && info.port) {
        base = `http://127.0.0.1:${info.port}`;
        token = info.token;
        return;
      }
      await new Promise((r) => setTimeout(r, 300));
    }
  }
  const q = new URLSearchParams(location.search);
  base = `http://127.0.0.1:${q.get("port") ?? import.meta.env.VITE_ENGINE_PORT ?? "8765"}`;
  token = q.get("token") ?? import.meta.env.VITE_ENGINE_TOKEN ?? "";
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(base + path, { ...init, headers: { ...(init.headers || {}), "x-vyum-token": token } });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

const json = (method: string, body: unknown): RequestInit => ({
  method, body: JSON.stringify(body), headers: { "content-type": "application/json" },
});

/** POSTs JSON and calls onEvent for every server-sent event until the stream ends. */
async function stream<E>(path: string, body: unknown, onEvent: (e: E) => void, signal?: AbortSignal) {
  const res = await fetch(base + path, {
    method: "POST", signal,
    headers: { "content-type": "application/json", "x-vyum-token": token },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${res.status} ${res.statusText}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const line = buf.slice(0, cut).trim();
      buf = buf.slice(cut + 2);
      if (line.startsWith("data:")) onEvent(JSON.parse(line.slice(5)));
    }
  }
}

export const api = {
  health: () => call<Health>("/health"),
  commands: () => call<Command[]>("/commands"),
  documents: () => call<Doc[]>("/documents"),
  job: (id: string) => call<Job>(`/jobs/${id}`),

  projects: () => call<Project[]>("/projects"),
  project: (id: string) => call<Project>(`/projects/${id}`),
  createProject: (folder: string, name?: string) => call<Project>("/projects", json("POST", { folder, name })),
  scanProject: (id: string) => call<Job>(`/projects/${id}/scan`, { method: "POST" }),
  deleteProject: (id: string) => call(`/projects/${id}`, { method: "DELETE" }),

  chats: (projectId?: string) => call<ChatSummary[]>(`/chats${projectId ? `?project_id=${projectId}` : ""}`),
  chat: (id: string) => call<Chat>(`/chats/${id}`),
  createChat: (projectId?: string | null) => call<Chat>("/chats", json("POST", { project_id: projectId ?? null })),
  renameChat: (id: string, title: string) => call(`/chats/${id}`, json("PATCH", { title })),
  deleteChat: (id: string) => call(`/chats/${id}`, { method: "DELETE" }),
  attach: (chatId: string, files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return call<Job[]>(`/chats/${chatId}/files`, { method: "POST", body: form });
  },
  detach: (chatId: string, docId: string) => call(`/chats/${chatId}/files/${docId}`, { method: "DELETE" }),

  /** Sends a message and calls onEvent for every streamed event until the answer is complete. */
  send(chatId: string, text: string, opts: { mode: string; think: boolean; source: Source; web: boolean; run_id?: string }, onEvent: (e: StreamEvent) => void,
       signal?: AbortSignal) {
    return stream(`/chats/${chatId}/messages`, { text, ...opts }, onEvent, signal);
  },

  /** The Stop button: the engine ends that answer at its next safe point (a file being saved always finishes). */
  stop: (runId: string) => call(`/stop/${encodeURIComponent(runId)}`, { method: "POST" }).catch(() => undefined),

  overview: () => call<Overview>("/overview"),
  officeFiles: (kind?: string, q?: string) => {
    const qs = new URLSearchParams();
    if (kind) qs.set("kind", kind);
    if (q) qs.set("q", q);
    return call<{ files: OfficeFile[]; status: OfficeStatus }>(`/office/files?${qs}`);
  },
  officeCreate: (body: { kind: OfficeKind; prompt: string; doc_ids: string[]; think: boolean }, onEvent: (e: StreamEvent) => void,
                 signal?: AbortSignal) => stream("/office/create", body, onEvent, signal),
  officeEdit: (id: string, body: { instruction: string; think: boolean; with_ids?: string[] }, onEvent: (e: StreamEvent) => void,
               signal?: AbortSignal) => stream(`/office/${id}/edit`, body, onEvent, signal),
  officeNew: (kind: OfficeKind, title?: string) => call<OfficeFile>("/office/new", json("POST", { kind, title })),
  officeChat: (id: string, body: { message: string; history: { role: string; content: string }[]; think: boolean; run_id?: string; live?: boolean },
               onEvent: (e: StreamEvent) => void, signal?: AbortSignal) => stream(`/office/${id}/chat`, body, onEvent, signal),
  docLoad: (id: string) => call<{ html: string; blocks: number; stamp: number }>(`/office/${id}/doc`),
  docSave: (id: string, html: string) => call<{ changed: boolean; file?: OfficeFile }>(`/office/${id}/doc`, json("PUT", { html })),
  sheetLoad: (id: string) => call<Record<string, unknown>>(`/office/${id}/sheet`),
  sheetSave: (id: string, entries: unknown[]) => call<{ changed: boolean; file?: OfficeFile }>(`/office/${id}/sheet`, json("PUT", { entries })),
  /** The full PDF editor (GenOffice's PDF module hosted by the engine): a view of this file, loaded in a frame. */
  pdfxOpen: (id: string, lang: string, theme: string) =>
    call<{ view: number; path: string }>(`/office/${id}/pdfx/open?lang=${lang}&theme=${theme}`, { method: "POST" }),
  pdfxClose: (view: number) => call(`/pdfx/close?view=${view}`, { method: "POST" }).catch(() => undefined),
  /** An engine page URL with the token, for frames (which cannot send headers). */
  engineUrl: (path: string) => `${base}${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`,
  dashboards: (id: string) => call<{ id: string; title: string }[]>(`/office/${id}/dashboards`),
  dashboardRender: (id: string, did: string, filters: Record<string, string[]>) =>
    call<DashData>(`/office/${id}/dashboards/${did}`, json("POST", { filters })),
  dashboardDelete: (id: string, did: string) => call(`/office/${id}/dashboards/${did}`, { method: "DELETE" }),
  dashboardRename: (id: string, did: string, title: string) => call(`/office/${id}/dashboards/${did}/rename`, json("POST", { title })),
  /** Saves an exported chart (PNG/GIF) or table (CSV) to Documents/Vyum/Exports. */
  saveExport: (filename: string, data: string) => call<{ path: string; name: string }>("/exports", json("POST", { filename, data })),
  revealExport: (path: string) => call("/exports/reveal", json("POST", { path })),
  chartSave: (id: string, chart: Chart, del = false) => call<{ charts: Chart[] }>(`/office/${id}/charts`, json("POST", { chart, delete: del })),
  chartEmbed: (id: string, chartId: string) => call<Chart>(`/office/${id}/charts/${chartId}/embed`, { method: "POST" }),
  pdfOp: (id: string, action: "rotate" | "delete" | "keep" | "extract", pages: number[], degrees = 90) =>
    call<OfficeFile>(`/office/${id}/pdf`, json("POST", { action, pages, degrees })),
  officeFileUrl: (id: string, stamp: number) => `${base}/office/${id}/file?token=${encodeURIComponent(token)}&v=${stamp}`,
  slidesLoad: (id: string) => call<Deck>(`/office/${id}/slides`),
  slideOp: (id: string, body: { op: "text" | "notes" | "delete" | "duplicate" | "move"; slide: number; el?: string; text?: string; to?: number }) =>
    call<OfficeFile>(`/office/${id}/slides`, json("POST", body)),
  mailDetect: (email: string) => call<MailDetect>("/mail/detect", json("POST", { email })),
  oauthClients: () => call<Record<string, { configured: boolean; client_id: string; setup: string }>>("/mail/oauth/clients"),
  oauthClientsSave: (body: Record<string, { client_id: string; client_secret?: string }>) => call("/mail/oauth/clients", json("PUT", body)),
  oauthStart: (provider: string, email: string, name: string) => call<{ state: string; url: string }>("/mail/oauth/start", json("POST", { provider, email, name })),
  oauthPoll: (state: string) => call<{ status: string; error?: string; email?: string }>(`/mail/oauth/poll?state=${encodeURIComponent(state)}`),
  mailProviders: () => call<Record<string, MailProvider>>("/mail/providers"),
  mailAccounts: () => call<MailAccount[]>("/mail/accounts"),
  mailAddAccount: (body: Record<string, unknown>) => call<MailAccount>("/mail/accounts", json("POST", body)),
  mailRemoveAccount: (id: string) => call(`/mail/accounts/${id}`, { method: "DELETE" }),
  mailSync: (account?: string) => call(`/mail/sync${account ? `?account=${account}` : ""}`, { method: "POST" }),
  mailFolders: (aid: string) => call<MailFolder[]>(`/mail/${aid}/folders`),
  mailMessages: (aid: string, opts: { folder?: string; q?: string; page?: number; starred?: boolean }) => {
    const qs = new URLSearchParams();
    if (opts.folder) qs.set("folder", opts.folder);
    if (opts.q) qs.set("q", opts.q);
    if (opts.page) qs.set("page", String(opts.page));
    if (opts.starred) qs.set("starred", "true");
    return call<{ total: number; messages: MailMsg[]; status: MailAccount["status"] }>(`/mail/${aid}/messages?${qs}`);
  },
  mailThread: (aid: string, key: string) => call<MailMsg[]>(`/mail/${aid}/thread?key=${encodeURIComponent(key)}`),
  mailHtml: async (mid: number, images: boolean) => {
    const res = await fetch(`${base}/mail/message/${mid}/html?images=${images}&token=${encodeURIComponent(token)}`);
    return { html: await res.text(), blocked: Number(res.headers.get("X-Blocked-Images") || 0) };
  },
  mailAttachmentUrl: (mid: number, index: number) => `${base}/mail/message/${mid}/attachment/${index}?token=${encodeURIComponent(token)}`,
  mailAct: (aid: string, ids: number[], action: string, target?: string) => call<{ done: number }>(`/mail/${aid}/act`, json("POST", { ids, action, target })),
  mailSend: (aid: string, form: FormData) => call<{ sent: boolean }>(`/mail/${aid}/send`, { method: "POST", body: form }),
  mailAssist: (body: Record<string, unknown>, onEvent: (e: StreamEvent) => void, signal?: AbortSignal) => stream("/mail/assist", body, onEvent, signal),
  officeUndo: (id: string) => call<OfficeFile>(`/office/${id}/undo`, { method: "POST" }),
  officeOpen: (id: string) => call(`/office/${id}/open`, { method: "POST" }),
  officeReveal: (id: string) => call(`/office/${id}/reveal`, { method: "POST" }),
  officePreview: (id: string) => call<{ pages: number }>(`/office/${id}/preview`),
  officePreviewUrl: (id: string, page: number, stamp: number) =>
    `${base}/office/${id}/preview/${page}?token=${encodeURIComponent(token)}&v=${stamp}`,
  officeOpenLocal: (path: string) => call<OfficeFile>("/office/open-local", json("POST", { path })),
  officeUpload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<OfficeFile>("/office/upload", { method: "POST", body: form });
  },
  officeDelete: (id: string, deleteFile: boolean) => call(`/office/${id}?delete_file=${deleteFile}`, { method: "DELETE" }),
  mailDraft: (body: { kind: "new" | "reply" | "summarize"; instructions: string; thread: string; to: string; subject: string;
                      tone: string; length: string; sender: string; doc_ids: string[]; think: boolean },
              onEvent: (e: StreamEvent) => void, signal?: AbortSignal) => stream("/mail/draft", body, onEvent, signal),
  ingest: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<Job>("/ingest", { method: "POST", body: form });
  },
  settings: () => call<Settings>("/settings"),
  saveSettings: (patch: Settings) => call<Settings>("/settings", json("PUT", patch)),
  checkSearch: (searxng_url: string) =>
    call<{ ok: boolean; results?: number; error?: string }>("/settings/check", json("POST", { searxng_url })),

  pageUrl: (id: string, page: number) => `${base}/documents/${id}/page/${page}?token=${encodeURIComponent(token)}`,
  fileUrl: (id: string, page?: number) =>
    `${base}/documents/${id}/file?token=${encodeURIComponent(token)}${page ? `#page=${page}` : ""}`,
};

/** Opens a web page in the user's browser (the desktop app never navigates away from itself). */
export async function openExternal(url: string) {
  if (!/^(https?:\/\/|mailto:)/i.test(url)) return;
  if (inTauri()) {
    const { openUrl } = await import("@tauri-apps/plugin-opener");
    await openUrl(url);
  } else {
    window.open(url, "_blank", "noopener");
  }
}
