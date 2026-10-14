/**
 * Vyum PDF host: runs GenOffice's PDF back end (apps/pdf/src/main/pdf-main.ts, Apache-2.0) unchanged inside a
 * windowless Electron app, and answers the PDF screen's calls over stdin/stdout instead of Electron IPC.
 *
 * Protocol (one JSON object per line):
 *   in  {"op":"open","view":1,"path":"/a.pdf","lang":"en","theme":"light"}   grant a file to a view
 *   in  {"op":"invoke","id":7,"view":1,"channel":"pdf:save","args":[...]}  -> out {"op":"reply","id":7,"ok":true,"result":...}
 *   in  {"op":"send","view":1,"channel":"pdf:dirty-changed","args":[true]}  one-way message from the screen
 *   in  {"op":"close","view":1}
 *   out {"op":"event","view":1,"channel":"pdf:save-as-request","args":[...]}  message to the screen
 * Protocol lines start with \u0001 so stray console output from libraries is never mistaken for a reply.
 * Binary values travel as {"__b64": "...", "__t": "ab" | "u8"}.
 *
 * Privacy: the AI panel's cloud providers, Genspark image generation and web image search are not wired here;
 * those channels answer "not available" (Vyum's own on-device chat sits beside the PDF instead).
 */
import { app, ipcMain } from 'electron'
import { createInterface } from 'node:readline'
import { EventEmitter } from 'node:events'
import * as pdf from 'GENOFFICE_PDF_MAIN'
import { defaultAiSettings, streamForProvider } from '@genoffice/ai-provider'

/** GenOffice's AI runs on Vyum's on-device model (Ollama's OpenAI-compatible API on this Mac), never a cloud one. */
const LOCAL_AI_URL = process.env.VYUM_AI_URL ?? 'http://127.0.0.1:11434/v1'
const LOCAL_AI_MODEL = process.env.VYUM_AI_MODEL ?? 'vyum-qwen3-8b'
function localAiSettings() {
  const s = defaultAiSettings() as ReturnType<typeof defaultAiSettings> & Record<string, unknown>
  s.provider = 'custom'
  s.providers.custom = { apiKey: 'local', model: LOCAL_AI_MODEL, baseUrl: LOCAL_AI_URL }
  s.gskToolsEnabled = false
  return s
}
const aiStreams = new Map<string, AbortController>()

type Handler = (event: unknown, ...args: unknown[]) => unknown
const handlers = new Map<string, Handler>()
const listeners = new Map<string, Handler[]>()

// Capture GenOffice's handlers as it registers them (they still go to Electron's ipcMain, which stays unused).
const realHandle = ipcMain.handle.bind(ipcMain)
;(ipcMain as unknown as { handle: unknown }).handle = (channel: string, fn: Handler) => {
  handlers.set(channel, fn)
  return realHandle(channel, fn as never)
}
const realOn = ipcMain.on.bind(ipcMain)
;(ipcMain as unknown as { on: unknown }).on = (channel: string, fn: Handler) => {
  listeners.set(channel, [...(listeners.get(channel) ?? []), fn])
  return realOn(channel, fn as never)
}

const out = (msg: object) => process.stdout.write('\u0001' + JSON.stringify(msg) + '\n')

function encode(v: unknown): unknown {
  if (v instanceof ArrayBuffer) return { __b64: Buffer.from(v).toString('base64'), __t: 'ab' }
  if (ArrayBuffer.isView(v)) return { __b64: Buffer.from(v.buffer, v.byteOffset, v.byteLength).toString('base64'), __t: 'u8' }
  if (Array.isArray(v)) return v.map(encode)
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, encode(x)]))
  return v
}

function decode(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(decode)
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    if (typeof o.__b64 === 'string') {
      const buf = Buffer.from(o.__b64, 'base64')
      return o.__t === 'ab' ? buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) : new Uint8Array(buf)
    }
    return Object.fromEntries(Object.entries(o).map(([k, x]) => [k, decode(x)]))
  }
  return v
}

/** Just enough of Electron's WebContents for pdf-main: an id, send(), lifecycle events. */
class FakeContents extends EventEmitter {
  constructor(readonly id: number) { super() }
  destroyed = false
  send(channel: string, ...args: unknown[]) { out({ op: 'event', view: this.id, channel, args: encode(args) }) }
  isDestroyed() { return this.destroyed }
  setWindowOpenHandler() { /* links are opened by the Vyum app */ }
}

const views = new Map<number, FakeContents>()
const prefs = new Map<number, { lang: string; theme: string }>()

const NOT_HERE = 'Not available in Vyum: everything stays on this Mac. Use Vyum’s chat on the left.'
const local: Record<string, (view: number, ...args: unknown[]) => unknown> = {
  'app:get-language': (v) => prefs.get(v)?.lang ?? 'en',
  'app:get-theme': (v) => prefs.get(v)?.theme ?? 'light',
  // the panel starts closed in every document: Vyum's own chat is beside the PDF
  'app:get-ai-panel-prefs': () => ({ side: 'right', fontSize: 'default', customFontSize: 14, spellcheck: true, openInNewDocs: false }),
  'app:set-ai-panel-prefs': () => undefined,
  'ai:get-settings': () => localAiSettings(),
  'ai:set-settings': () => undefined,
  'ai:gsk-status': () => ({ installed: false, loggedIn: false }),
  'ai:open-model-settings': () => undefined,
  // whatever settings the screen sends, the request goes to the local model
  'ai:stream': async (view, request) => {
    const r = (request ?? {}) as { requestId: string; system?: string; messages?: unknown[]; tools?: unknown[]; maxTokens?: number }
    const wc = views.get(view)
    const send = (chunk: object) => { if (wc && !wc.isDestroyed()) wc.send('ai:stream-chunk', { requestId: r.requestId, ...chunk }) }
    const controller = new AbortController()
    aiStreams.set(r.requestId, controller)
    try {
      let stopReason: string | undefined
      await streamForProvider('custom', localAiSettings().providers.custom, r.system ?? '', (r.messages ?? []) as never,
        (r.tools ?? []) as never, r.maxTokens ?? 4096, {
          signal: controller.signal,
          onDelta: (text: string) => send({ type: 'delta', text }),
          onReasoningDelta: (text: string) => send({ type: 'reasoning', text }),
          onToolCall: (toolCall: unknown) => send({ type: 'tool-call', toolCall }),
          onActivity: () => send({ type: 'ping' }),
          onStopReason: (reason: string) => { stopReason = reason },
        } as never)
      send({ type: 'done', stopReason })
    } catch (err) {
      send(controller.signal.aborted ? { type: 'done' } : { type: 'error', error: err instanceof Error ? err.message : String(err) })
    } finally {
      aiStreams.delete(r.requestId)
    }
  },
  'ai:stream-cancel': (_view, requestId) => { aiStreams.get(String(requestId))?.abort() },
  'ai:web-search': () => ({ results: [], error: NOT_HERE }),
  'pdf:generate-image': () => ({ error: NOT_HERE }),  // Genspark (cloud) image generation: off
  'ai:image-search': () => ({ images: [], method: 'error', error: NOT_HERE }),
  'ai:fetch-image': () => null,
  'home:open-path': () => undefined,
}

async function handle(msg: { op: string; id?: number; view: number; channel?: string; args?: unknown[]; path?: string; lang?: string; theme?: string }) {
  if (msg.op === 'open') {
    let wc = views.get(msg.view)
    if (!wc) { wc = new FakeContents(msg.view); views.set(msg.view, wc) }
    prefs.set(msg.view, { lang: msg.lang ?? 'en', theme: msg.theme ?? 'light' })
    ;(pdf as unknown as { grantAndTrack: (w: unknown, p?: string) => void }).grantAndTrack(wc, msg.path)
    return
  }
  const wc = views.get(msg.view)
  if (msg.op === 'close') {
    if (wc) { wc.destroyed = true; wc.emit('destroyed'); views.delete(msg.view) }
    return
  }
  if (!wc || !msg.channel) {
    if (msg.op === 'invoke') out({ op: 'reply', id: msg.id, ok: false, error: 'unknown PDF view' })
    return
  }
  const event = { sender: wc, senderFrame: null }
  const args = decode(msg.args ?? []) as unknown[]
  if (msg.op === 'send') {
    for (const fn of listeners.get(msg.channel) ?? []) { try { fn(event, ...args) } catch { /* one listener must not stop others */ } }
    return
  }
  try {
    // Vyum decides language, theme and AI settings; GenOffice's own answers come from its app settings
    const mine = msg.channel.startsWith('app:') || msg.channel.startsWith('ai:') || msg.channel === 'pdf:generate-image'
    const fn = mine && local[msg.channel] ? undefined : handlers.get(msg.channel)
    const result = fn ? await fn(event, ...args)
      : local[msg.channel] ? await local[msg.channel](msg.view, ...args)
      : (() => { throw new Error(NOT_HERE) })()
    out({ op: 'reply', id: msg.id, ok: true, result: encode(result) })
  } catch (err) {
    out({ op: 'reply', id: msg.id, ok: false, error: err instanceof Error ? err.message : String(err) })
  }
}

// Electron's own settings and caches go into Vyum's data folder, not a generic "Electron" folder
if (process.env.VYUM_ELECTRON_DATA) app.setPath('userData', process.env.VYUM_ELECTRON_DATA)
app.dock?.hide()
app.on('window-all-closed', () => { /* stay alive: Vyum owns this process */ })
void app.whenReady().then(() => {
  pdf.configurePdfRuntime({
    preloadPath: '',
    createDocument: async () => ({ ok: false, error: NOT_HERE }),
  })
  ;(pdf as unknown as { registerPdfIpc: () => void }).registerPdfIpc()
  const rl = createInterface({ input: process.stdin })
  rl.on('line', (line) => {
    if (!line.trim()) return
    try { void handle(JSON.parse(line)) } catch (err) { out({ op: 'log', error: String(err) }) }
  })
  rl.on('close', () => app.quit())  // Vyum went away: so does the host
  out({ op: 'ready', channels: [...handlers.keys()].length })
})
