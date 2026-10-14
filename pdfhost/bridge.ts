/**
 * Browser stand-ins for Electron's renderer side, so GenOffice's own preload script (apps/pdf/src/preload/index.ts,
 * Apache-2.0) runs unchanged in a normal web page: invoke() and send() go to the Vyum engine over HTTP, and messages
 * from the PDF host arrive through one EventSource. The page's URL carries the view id and the engine token.
 */
const qs = new URLSearchParams(location.search)
const view = qs.get('view') ?? '0'
const token = qs.get('token') ?? ''
const fid = qs.get('fid') ?? ''
// the file id lets the engine re-open this view after a restart instead of failing
const q = `view=${encodeURIComponent(view)}&fid=${encodeURIComponent(fid)}&token=${encodeURIComponent(token)}`

function toB64(bytes: Uint8Array): string {
  let s = ''
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(s)
}
function fromB64(b64: string): Uint8Array {
  const s = atob(b64)
  const out = new Uint8Array(s.length)
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i)
  return out
}
function encode(v: unknown): unknown {
  if (v instanceof ArrayBuffer) return { __b64: toB64(new Uint8Array(v)), __t: 'ab' }
  if (ArrayBuffer.isView(v)) return { __b64: toB64(new Uint8Array(v.buffer, v.byteOffset, v.byteLength)), __t: 'u8' }
  if (Array.isArray(v)) return v.map(encode)
  if (v && typeof v === 'object' && !(v instanceof Blob)) return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, encode(x)]))
  return v
}
function decode(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(decode)
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    if (typeof o.__b64 === 'string') {
      const u8 = fromB64(o.__b64)
      return o.__t === 'ab' ? u8.buffer : u8
    }
    return Object.fromEntries(Object.entries(o).map(([k, x]) => [k, decode(x)]))
  }
  return v
}

type Listener = (event: unknown, ...args: unknown[]) => void
const listeners = new Map<string, Set<Listener>>()
let source: EventSource | null = null
function events() {
  if (source) return
  source = new EventSource(`/pdfx/events?${q}`)
  source.onmessage = (ev) => {
    const m = JSON.parse(ev.data) as { channel: string; args: unknown[] }
    for (const fn of [...(listeners.get(m.channel) ?? [])]) fn({ sender: null }, ...(decode(m.args) as unknown[]))
  }
}

/** The editor has edits not yet written to the file (it says so on 'pdf:dirty-changed'). */
let unsaved = false

export const ipcRenderer = {
  async invoke(channel: string, ...args: unknown[]): Promise<unknown> {
    const r = await fetch(`/pdfx/rpc?${q}`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ channel, args: encode(args) }),
    })
    const j = await r.json()
    if (!j.ok) throw new Error(j.error || `${channel} failed`)
    return decode(j.result)
  },
  send(channel: string, ...args: unknown[]): void {
    if (channel === 'pdf:dirty-changed') unsaved = !!args[0]
    void fetch(`/pdfx/send?${q}`, {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ channel, args: encode(args) }),
    })
  },
  on(channel: string, fn: Listener) {
    events()
    if (!listeners.has(channel)) listeners.set(channel, new Set())
    listeners.get(channel)!.add(fn)
    return ipcRenderer
  },
  once(channel: string, fn: Listener) {
    const wrap: Listener = (e, ...a) => { ipcRenderer.removeListener(channel, wrap); fn(e, ...a) }
    return ipcRenderer.on(channel, wrap)
  },
  removeListener(channel: string, fn: Listener) { listeners.get(channel)?.delete(fn); return ipcRenderer },
  off(channel: string, fn: Listener) { return ipcRenderer.removeListener(channel, fn) },
  removeAllListeners(channel?: string) { if (channel) listeners.delete(channel); else listeners.clear(); return ipcRenderer },
}

export const contextBridge = {
  exposeInMainWorld(key: string, api: unknown) { (window as unknown as Record<string, unknown>)[key] = api },
}

export const webUtils = { getPathForFile: () => '' }
export default { ipcRenderer, contextBridge, webUtils }

/**
 * Vyum branding inside the hosted editor: GenOffice's and Genspark's names in labels, tooltips and titles read
 * "Vyum" (its AI runs on Vyum's on-device model). Brand names are not translated, so this works in every language.
 */
const BRAND: [RegExp, string][] = [
  [/Genspark\s*AI/g, 'Vyum AI'], [/GenSpark\s*AI/g, 'Vyum AI'], [/Genspark|GenSpark/g, 'Vyum'],
  [/GenOffice\s*AI/g, 'Vyum AI'], [/GenOffice/g, 'Vyum'], [/^AI Assistant$/g, 'Vyum'],
  [/जेनस्पार्क|जेनऑफ़िस|जेनऑफिस/g, 'Vyum'],
]
const ATTRS = ['title', 'placeholder', 'aria-label', 'alt', 'data-tooltip']
function rebrand(text: string): string {
  let out = text
  for (const [re, to] of BRAND) out = out.replace(re, to)
  return out
}
function fixNode(node: Node) {
  if (node.nodeType === Node.TEXT_NODE) {
    const v = node.nodeValue ?? ''
    if (/gen(spark|office)|जेन/i.test(v)) { const nv = rebrand(v); if (nv !== v) node.nodeValue = nv }
    return
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return
  const el = node as Element
  for (const a of ATTRS) {
    const v = el.getAttribute(a)
    if (v && /gen(spark|office)|जेन/i.test(v)) el.setAttribute(a, rebrand(v))
  }
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT)
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n.nodeType === Node.TEXT_NODE) {
      const v = n.nodeValue ?? ''
      if (/gen(spark|office)|जेन/i.test(v)) { const nv = rebrand(v); if (nv !== v) n.nodeValue = nv }
    } else {
      for (const a of ATTRS) {
        const v = (n as Element).getAttribute(a)
        if (v && /gen(spark|office)|जेन/i.test(v)) (n as Element).setAttribute(a, rebrand(v))
      }
    }
  }
}
function startRebrand() {
  fixNode(document.body)
  document.title = 'Vyum PDF'
  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === 'characterData' || r.type === 'attributes') fixNode(r.target)
      else r.addedNodes.forEach(fixNode)
    }
    if (document.title !== 'Vyum PDF' && /gen(spark|office)/i.test(document.title)) document.title = rebrand(document.title)
  }).observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS })
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', startRebrand)
else startRebrand()

/**
 * One assistant: the editor's own AI panel and buttons hand their requests to Vyum's chat beside the editor (the
 * verified, on-device assistant) instead of opening a second AI. The page tells its parent window what was asked.
 */
function askVyum(kind: string, text = '') {
  const selection = (window.getSelection()?.toString() ?? '').trim().slice(0, 4000)
  window.parent?.postMessage({ type: 'vyum-ai', kind, text, selection }, '*')
}
function startOneAssistant() {
  const style = document.createElement('style')
  style.textContent = '.ai-dock, .ai-rail { display: none !important; }'
  document.head.appendChild(style)
  document.addEventListener('click', (e) => {
    const target = e.target as Element | null
    const b = target?.closest?.('.ai-entry, .ai-rail, .ai-quick-btn, .ai-ask-chip, .ai-ask-confirm')
    if (!b) return
    e.preventDefault()
    e.stopPropagation()
    if (b.classList.contains('ai-entry')) {
      askVyum(['open', 'summary', 'keypoints'][[...document.querySelectorAll('.ai-entry')].indexOf(b)] ?? 'open')
    } else if (b.classList.contains('ai-quick-btn')) {
      askVyum(['summary', 'keypoints'][[...document.querySelectorAll('.ai-quick-btn')].indexOf(b)] ?? 'open')
    } else {
      const input = document.querySelector<HTMLTextAreaElement | HTMLInputElement>('.ai-ask-pop-input')
      askVyum('ask', b.classList.contains('ai-ask-chip') ? (b.textContent ?? '').trim() : (input?.value ?? '').trim())
      document.querySelector<HTMLElement>('.ai-ask-pop-close')?.click()
    }
  }, true)
  document.addEventListener('keydown', (e) => {
    const t = e.target as Element | null
    if (e.key === 'Enter' && !e.shiftKey && t?.classList?.contains('ai-ask-pop-input')) {
      e.preventDefault()
      e.stopPropagation()
      askVyum('ask', ((t as HTMLInputElement).value ?? '').trim())
      document.querySelector<HTMLElement>('.ai-ask-pop-close')?.click()
    }
  }, true)
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', startOneAssistant)
else startOneAssistant()

/**
 * Citations: Vyum's chat sends {type:'vyum-cite', page, text} when a citation is clicked. The editor goes to that
 * page, finds the cited passage in the page text, scrolls it into view and highlights it (a temporary overlay).
 * {type:'vyum-mark', items:[{page,text}]} turns passages into real highlight annotations (saved with the PDF, undoable).
 */
type Rect = [number, number, number, number]
interface EditorApi {
  doc: () => { getPage: (n: number) => Promise<{ view: number[]; rotate: number; getViewport: (o: { scale: number; rotation?: number }) => { transform: number[]; width: number } }> } | null
  searchIndex: () => Promise<{ text: string; items: { start: number; end: number; x: number; y: number; w: number; h: number }[] }[]>
  gotoPage: (p: number) => boolean
  addMarkup: (type: string, origIdx: number, rects: Rect[], color?: [number, number, number]) => void
  editText: (input: Record<string, unknown>) => Promise<string | null>
  addNote: (origIdx: number, at: [number, number], contents: string, color?: [number, number, number]) => { key: string } | { error: string }
  insertText: (input: Record<string, unknown>) => { id: string } | { error: string }
  applyOps: (ops: Record<string, unknown>[]) => { ok?: boolean; error?: string } | unknown
}

/** The editor's own AI capability object, found through React's fiber tree (rebuilt every render: fetch it each time). */
function editorApi(): EditorApi | null {
  const start = document.querySelector('.ai-dock, .copilot, .app-main')
  if (!start) return null
  const key = Object.keys(start).find((k) => k.startsWith('__reactFiber$'))
  const seen = new Set<unknown>()
  const walk = (f: any, depth: number): EditorApi | null => {
    for (let n = f; n && depth < 400; n = n.return, depth++) {
      if (seen.has(n)) break
      seen.add(n)
      const api = n.memoizedProps?.api
      if (api?.gotoPage && api?.searchIndex) return api
    }
    return null
  }
  let found = key ? walk((start as any)[key], 0) : null
  if (!found && key) {  // the panel sits below App: search down from the app root for <AiPanel api=…>
    const stack = [(start as any)[key]]
    while (stack.length && !found) {
      const n = stack.pop()
      if (!n) continue
      if (n.memoizedProps?.api?.gotoPage && n.memoizedProps?.api?.searchIndex) found = n.memoizedProps.api
      if (n.child) stack.push(n.child)
      if (n.sibling) stack.push(n.sibling)
    }
  }
  return found
}

/** Letters and digits only, lower-cased, with each kept character's position in the original text. */
function squeeze(s: string): { norm: string; at: number[] } {
  let norm = ''
  const at: number[] = []
  for (let i = 0; i < s.length; i++) {
    const c = s[i]!.toLowerCase()
    if (/[\p{L}\p{N}]/u.test(c)) { norm += c; at.push(i) }
  }
  return { norm, at }
}

/** Character ranges of the passage on the page: whole lines where they are found, else each line's start and end. */
function passageRanges(pageText: string, passage: string): [number, number][] {
  const page = squeeze(pageText)
  const out: [number, number][] = []
  let from = 0
  for (const line of passage.split(/\n+/)) {
    const q = squeeze(line).norm
    if (q.length < 4) continue
    let s = page.norm.indexOf(q, from)
    let e = s >= 0 ? s + q.length : -1
    if (s < 0 && q.length > 50) {  // hyphenation or ligatures differ somewhere in the middle: anchor both ends
      s = page.norm.indexOf(q.slice(0, 40), from)
      const tail = s >= 0 ? page.norm.indexOf(q.slice(-40), s) : -1
      e = tail >= 0 && tail - s < q.length * 1.5 ? tail + 40 : -1
    }
    if (s < 0 || e < 0) continue
    out.push([page.at[s]!, page.at[e - 1]! + 1])
    from = e
  }
  return out
}

/** PDF-space rectangles covering the ranges, one per line of text. */
function rangeRects(entry: { items: { start: number; end: number; x: number; y: number; w: number; h: number }[] }, ranges: [number, number][]): Rect[] {
  const rects: Rect[] = []
  for (const [s, e] of ranges) {
    for (const it of entry.items) {
      if (it.end <= s || it.start >= e) continue
      const len = it.end - it.start || 1
      const lo = (Math.max(s, it.start) - it.start) / len
      const hi = (Math.min(e, it.end) - it.start) / len
      const r: Rect = [it.x + it.w * lo, it.y - it.h * 0.2, it.x + it.w * hi, it.y + it.h * 0.95]
      if (r[2] - r[0] < 0.01) continue
      const last = rects[rects.length - 1]
      if (last && Math.abs(last[1] - r[1]) < it.h * 0.5 && r[0] - last[2] < it.h * 2) {
        last[0] = Math.min(last[0], r[0]); last[2] = Math.max(last[2], r[2])
        last[1] = Math.min(last[1], r[1]); last[3] = Math.max(last[3], r[3])
      } else rects.push(r)
    }
  }
  return rects
}

async function findPassage(api: EditorApi, page: number, text: string): Promise<{ page: number; rects: Rect[] } | null> {
  const index = await api.searchIndex()
  const tryPage = (p: number) => {
    const entry = index[p - 1]
    if (!entry) return null
    const rects = rangeRects(entry, passageRanges(entry.text, text))
    return rects.length ? { page: p, rects } : null
  }
  return tryPage(page) ?? tryPage(page + 1) ?? tryPage(page - 1)  // page numbering can differ by one on odd files
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function showCitation(page: number, text: string) {
  const api = editorApi()
  if (!api) return
  const hit = await findPassage(api, page, text)
  const target = hit?.page ?? page
  api.gotoPage(target)
  if (!hit) return
  let el: HTMLElement | null = null
  for (let i = 0; i < 40 && !el; i++) {  // pages render when scrolled into view
    el = document.querySelector<HTMLElement>(`.pdf-page[data-page="${target - 1}"]`)
    if (!el || !el.clientWidth) { el = null; await sleep(50) }
  }
  if (!el) return
  const pdfPage = await api.doc()?.getPage(target)
  if (!pdfPage) return
  const base = pdfPage.getViewport({ scale: 1, rotation: pdfPage.rotate })
  const vp = pdfPage.getViewport({ scale: el.clientWidth / base.width, rotation: pdfPage.rotate })
  document.querySelectorAll('.vyum-cite-hl').forEach((n) => n.remove())
  const boxes = hit.rects.map((r) => {
    const [a, b, c, d, e, f] = vp.transform as [number, number, number, number, number, number]
    const x1 = a * r[0] + c * r[1] + e, y1 = b * r[0] + d * r[1] + f
    const x2 = a * r[2] + c * r[3] + e, y2 = b * r[2] + d * r[3] + f
    const box = document.createElement('div')
    box.className = 'vyum-cite-hl'
    Object.assign(box.style, { left: `${Math.min(x1, x2)}px`, top: `${Math.min(y1, y2)}px`,
      width: `${Math.abs(x2 - x1)}px`, height: `${Math.abs(y2 - y1)}px` })
    el!.appendChild(box)
    return box
  })
  const scroller = el.closest<HTMLElement>('.pdf-scroll')
  if (scroller && boxes[0]) {
    const top = boxes[0].getBoundingClientRect().top - scroller.getBoundingClientRect().top
    scroller.scrollTo({ top: scroller.scrollTop + top - scroller.clientHeight * 0.3, behavior: 'smooth' })
  }
}

async function markPassages(items: { page: number; text: string }[]) {
  const api = editorApi()
  if (!api) return 0
  let n = 0
  for (const it of items) {
    const hit = await findPassage(api, it.page, it.text)
    if (hit) { api.addMarkup('highlight', hit.page - 1, hit.rects, [1, 0.87, 0.2]); n++ }
  }
  document.querySelectorAll('.vyum-cite-hl').forEach((x) => x.remove())
  return n
}

function startCitations() {
  const style = document.createElement('style')
  style.textContent = `.vyum-cite-hl { position: absolute; z-index: 6; pointer-events: none; border-radius: 2px;
    background: rgba(255, 213, 0, 0.42); mix-blend-mode: multiply; box-shadow: 0 0 0 1px rgba(230, 170, 0, 0.55);
    animation: vyum-cite-pulse 1.4s ease-out 1; }
  @keyframes vyum-cite-pulse { 0% { background: rgba(255, 170, 0, 0.75); } 100% { background: rgba(255, 213, 0, 0.42); } }`
  document.head.appendChild(style)
  window.addEventListener('message', (e) => {
    if (e.source !== window.parent) return
    const d = e.data as { type?: string; page?: number; text?: string; items?: { page: number; text: string }[]; id?: string }
    if (d?.type === 'vyum-cite' && d.page && d.text) void showCitation(d.page, d.text)
    if (d?.type === 'vyum-mark' && d.items) {
      void markPassages(d.items).then((n) => window.parent.postMessage({ type: 'vyum-marked', id: d.id, count: n }, '*'))
    }
  })
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', startCitations)
else startCitations()


/**
 * Live editing: Vyum's chat sends one planned step at a time ({type:'vyum-live', id, step}); the editor makes it on
 * screen (going to its page first) and answers {type:'vyum-live-done', id, ok, note}. Every step is one undo step.
 */
type IndexEntry = { text: string; items: { start: number; end: number; x: number; y: number; w: number; h: number }[] }
type Target = { page: number; occurrence: number }
interface LiveStep {
  do: string; find?: string; with?: string; text?: string; style?: string; color?: [number, number, number] | null
  targets?: Target[]; page?: number; where?: string; pages?: number[]; degrees?: number
}

/** The nth place (1-based) a phrase appears on a page, as a character range of the page text (spacing ignored). */
function nthRange(entry: IndexEntry, find: string, n: number): [number, number] | null {
  const page = squeeze(entry.text)
  const q = squeeze(find).norm
  if (!q) return null
  let at = -1
  for (let i = 0, from = 0; i < n; i++) {
    at = page.norm.indexOf(q, from)
    if (at < 0) return null
    from = at + q.length
  }
  return [page.at[at]!, page.at[at + q.length - 1]! + 1]
}

function unionRect(entry: IndexEntry, [s, e]: [number, number]): { rect: Rect; fontSize: number } | null {
  let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity, fontSize = 0
  for (const it of entry.items) {
    if (it.end <= s || it.start >= e) continue
    const len = it.end - it.start || 1
    x1 = Math.min(x1, it.x + it.w * ((Math.max(s, it.start) - it.start) / len))
    x2 = Math.max(x2, it.x + it.w * ((Math.min(e, it.end) - it.start) / len))
    y1 = Math.min(y1, it.y); y2 = Math.max(y2, it.y + it.h)
    fontSize = Math.max(fontSize, it.h)
  }
  return fontSize > 0 && x2 - x1 > 0.01 ? { rect: [x1, y1, x2, y2], fontSize } : null
}

async function flashPlace(api: EditorApi, page: number, rects: Rect[]) {
  api.gotoPage(page)
  await sleep(250)
  const el = document.querySelector<HTMLElement>(`.pdf-page[data-page="${page - 1}"]`)
  const pdfPage = await api.doc()?.getPage(page)
  if (!el || !pdfPage || !rects.length) return
  const base = pdfPage.getViewport({ scale: 1, rotation: pdfPage.rotate })
  const vp = pdfPage.getViewport({ scale: el.clientWidth / base.width, rotation: pdfPage.rotate })
  const [a, b, c, d, e, f] = vp.transform as [number, number, number, number, number, number]
  const r = rects[0]!
  const top = Math.min(b * r[0] + d * r[1] + f, b * r[2] + d * r[3] + f)
  const scroller = el.closest<HTMLElement>('.pdf-scroll')
  if (scroller) {
    const y = el.getBoundingClientRect().top - scroller.getBoundingClientRect().top + top
    scroller.scrollTo({ top: scroller.scrollTop + y - scroller.clientHeight * 0.3, behavior: 'smooth' })
  }
  void a; void c; void e
}

/** Replaces a range of page text. The phrase alone first; when the PDF stores it inside a larger text run the
    editor cannot split, the whole line it sits on is rewritten with the phrase swapped (same font and place). */
async function replaceText(api: EditorApi, entry: IndexEntry, pageIndex: number, range: [number, number], newText: string) {
  const box = unionRect(entry, range)
  if (!box) return 'the text could not be found on the page'
  const first = await api.editText({ pageIndex, rect: box.rect, oldText: entry.text.slice(range[0], range[1]), newText,
    fontSize: box.fontSize })
  if (!first) return null
  const runs = entry.items.filter((it) => it.end > range[0] && it.start < range[1])
  if (!runs.length || Math.max(...runs.map((r) => r.y)) - Math.min(...runs.map((r) => r.y)) > box.fontSize * 0.5) return first
  const lineStart = runs[0]!.start, lineEnd = runs[runs.length - 1]!.end
  const line = entry.text.slice(lineStart, lineEnd)
  const rect: Rect = [Math.min(...runs.map((r) => r.x)), Math.min(...runs.map((r) => r.y)),
    Math.max(...runs.map((r) => r.x + r.w)), Math.max(...runs.map((r) => r.y + r.h))]
  const swapped = line.slice(0, range[0] - lineStart) + newText + line.slice(range[1] - lineStart)
  return await api.editText({ pageIndex, rect, oldText: line, newText: swapped, fontSize: box.fontSize })
}

async function runStep(step: LiveStep): Promise<{ ok: boolean; note?: string }> {
  const api = editorApi()
  if (!api) return { ok: false, note: 'The editor is not ready yet.' }
  const index = (await api.searchIndex()) as IndexEntry[]
  const failed: string[] = []
  let done = 0
  if (step.do === 'mark' || step.do === 'replace' || step.do === 'note') {
    for (const t of step.targets ?? []) {
      const entry = index[t.page - 1]
      const range = entry ? nthRange(entry, step.find ?? '', t.occurrence) : null
      if (!entry || !range) { failed.push(`page ${t.page}`); continue }
      if (step.do === 'mark') {
        const rects = rangeRects(entry, [range])
        if (!rects.length) { failed.push(`page ${t.page}`); continue }
        if (done === 0) await flashPlace(api, t.page, rects)
        api.addMarkup(step.style ?? 'highlight', t.page - 1, rects, step.color ?? undefined)
      } else {
        const box = unionRect(entry, range)
        if (!box) { failed.push(`page ${t.page}`); continue }
        if (done === 0) await flashPlace(api, t.page, [box.rect])
        if (step.do === 'note') {
          // the editor's addNote signs notes "AI Assistant"; Vyum's comments are signed "Vyum"
          const plan = api.applyOps([{ op: 'addDrawing', id: `vyum-${crypto.randomUUID()}`, drawing: {
            kind: 'note', pageIndex: t.page - 1, color: [0.96, 0.62, 0.12], at: [box.rect[2], box.rect[3]],
            contents: step.text ?? '', author: 'Vyum', createdMs: Date.now() } }]) as { failures?: { error: string }[] }
          if (plan?.failures?.length) { failed.push(plan.failures[0]!.error); continue }
        } else {
          const reason = await replaceText(api, entry, t.page - 1, range, step.with ?? '')
          if (reason) { failed.push(reason); continue }
        }
      }
      done++
      if (done < 4) await sleep(220)  // the first few land one by one, so the change can be followed on screen
    }
  } else if (step.do === 'add_text' && step.page) {
    const pdfPage = await api.doc()?.getPage(step.page)
    if (!pdfPage) return { ok: false, note: 'That page is not open.' }
    const [x0, y0, , y1] = pdfPage.view as [number, number, number, number]
    api.gotoPage(step.page)
    const r = api.insertText({ pageIndex: step.page - 1, text: step.text ?? '', fontSize: 14, color: [0, 0, 0],
      origin: [x0 + 54, step.where === 'bottom' ? y0 + 36 : y1 - 44] })
    if ('error' in r) failed.push(r.error); else done++
  } else if (step.do === 'rotate' && step.pages?.length) {
    const dir = step.degrees === 180 ? 180 : step.degrees === 270 ? -90 : 90
    api.gotoPage(step.pages[0]!)
    const r = api.applyOps([{ op: 'rotatePages', pages: step.pages.map((p) => p - 1), dir }]) as { error?: string } | undefined
    if (r && typeof r === 'object' && r.error) failed.push(String(r.error)); else done++
  } else if (step.do === 'delete_pages' && step.pages?.length) {
    const r = api.applyOps(step.pages.map((p) => ({ op: 'deletePage', pageIndex: p - 1 }))) as { error?: string } | undefined
    if (r && typeof r === 'object' && r.error) failed.push(String(r.error)); else done++
  } else if (step.do === 'save') {
    // the editor's own Save (⌘S); it reports "not dirty" once the file is written, or shows a toast when it fails
    document.querySelectorAll('.pdf-toast').forEach((t) => t.setAttribute('data-vyum-seen', '1'))
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 's', code: 'KeyS', metaKey: true, ctrlKey: !navigator.platform.includes('Mac'), bubbles: true }))
    for (let i = 0; i < 100 && unsaved; i++) {
      await sleep(150)
      const toast = document.querySelector('.pdf-toast:not([data-vyum-seen])')
      if (toast && unsaved) return { ok: false, note: (toast.textContent ?? '').trim().slice(0, 200) }
    }
    if (unsaved) return { ok: false, note: 'The PDF was not saved.' }
    done++
  }
  if (!done) return { ok: false, note: failed[0] ?? 'Nothing was changed.' }
  return { ok: true, note: failed.length ? `${failed.length} place(s) could not be changed` : undefined }
}

window.addEventListener('message', (e) => {
  if (e.source !== window.parent) return
  const d = e.data as { type?: string; id?: string; step?: LiveStep }
  if (d?.type !== 'vyum-live' || !d.step) return
  void runStep(d.step)
    .catch((err) => ({ ok: false, note: err instanceof Error ? err.message : String(err) }))
    .then((r) => window.parent.postMessage({ type: 'vyum-live-done', id: d.id, ...r }, '*'))
})

/**
 * Readable zoom: beside Vyum's chat the editor is often narrow, and "fit width" (minus the thumbnails and the
 * comments margin) can shrink a page to 10%. Narrow panes hide the thumbnails; a fitted zoom below READABLE is
 * raised to it. Once the person zooms by hand, their zoom is left alone.
 */
const READABLE = 55
function startReadableZoom() {
  const style = document.createElement('style')
  style.textContent = '@media (max-width: 860px) { .pdf-thumbs, .pdf-side-resizer { display: none !important; } }'
  document.head.appendChild(style)
  let byHand = false
  document.addEventListener('pointerdown', (e) => {
    if (e.isTrusted && (e.target as Element)?.closest?.('.zoom-btn, .zoom-slider')) byHand = true
  }, true)
  window.addEventListener('keydown', (e) => {
    if (e.isTrusted && (e.metaKey || e.ctrlKey) && ['=', '+', '-', '0'].includes(e.key)) byHand = true
  }, true)
  const pct = () => parseInt(document.querySelector('.zoom-value')?.textContent ?? '', 10)
  let busy = false
  const fix = async () => {
    if (byHand || busy) return
    busy = true
    try {
      // back to fit width first (a wider pane may now fit well), then up to a readable size if needed
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '0', metaKey: true, ctrlKey: !navigator.platform.includes('Mac'), bubbles: true }))
      await sleep(120)
      const zoomIn = document.querySelectorAll<HTMLButtonElement>('.zoom-btn')[1]
      for (let i = 0; i < 12 && zoomIn && pct() < READABLE; i++) { zoomIn.click(); await sleep(40) }
    } finally { busy = false }
  }
  let timer = 0
  const later = () => { clearTimeout(timer); timer = window.setTimeout(() => void fix(), 250) }
  new ResizeObserver(later).observe(document.documentElement)
  const wait = setInterval(() => { if (!Number.isNaN(pct())) { clearInterval(wait); later() } }, 200)
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', startReadableZoom)
else startReadableZoom()
