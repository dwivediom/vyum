import { t } from "./i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import { LocaleType, LogLevel, mergeLocales, Univer, type IWorkbookData } from "@univerjs/core";
import { FUniver } from "@univerjs/core/facade";
import { greenTheme } from "@univerjs/themes";
import { UniverSheetsCorePreset } from "@univerjs/preset-sheets-core";
import { UniverSheetsFilterPreset } from "@univerjs/preset-sheets-filter";
import { UniverSheetsSortPreset } from "@univerjs/preset-sheets-sort";
import { UniverSheetsConditionalFormattingPreset } from "@univerjs/preset-sheets-conditional-formatting";
import { UniverSheetsDataValidationPreset } from "@univerjs/preset-sheets-data-validation";
import { UniverSheetsFindReplacePreset } from "@univerjs/preset-sheets-find-replace";
import { UniverSheetsNotePreset } from "@univerjs/preset-sheets-note";
import { UniverSheetsTablePreset } from "@univerjs/preset-sheets-table";
import CoreEnUS from "@univerjs/preset-sheets-core/locales/en-US";
import FilterEnUS from "@univerjs/preset-sheets-filter/locales/en-US";
import SortEnUS from "@univerjs/preset-sheets-sort/locales/en-US";
import CfEnUS from "@univerjs/preset-sheets-conditional-formatting/locales/en-US";
import DvEnUS from "@univerjs/preset-sheets-data-validation/locales/en-US";
import FindEnUS from "@univerjs/preset-sheets-find-replace/locales/en-US";
import NoteEnUS from "@univerjs/preset-sheets-note/locales/en-US";
import TableEnUS from "@univerjs/preset-sheets-table/locales/en-US";
import "@univerjs/preset-sheets-core/lib/index.css";
import "@univerjs/preset-sheets-filter/lib/index.css";
import "@univerjs/preset-sheets-sort/lib/index.css";
import "@univerjs/preset-sheets-conditional-formatting/lib/index.css";
import "@univerjs/preset-sheets-data-validation/lib/index.css";
import "@univerjs/preset-sheets-find-replace/lib/index.css";
import "@univerjs/preset-sheets-note/lib/index.css";
import "@univerjs/preset-sheets-table/lib/index.css";
import { api, type Chart, type OfficeFile, type SheetTarget } from "./api";
import { Dashboard } from "./Dashboard";
import { ChartCard, InsertChart } from "./Charts";
import { DocChat, DocHeader, type SaveState } from "./Workspace";
import * as I from "./icons";

type Entry = Record<string, unknown> & { type: string };
type Runtime = { univer: Univer; univerAPI: FUniver };

/** Only free Apache-2.0 Univer packages: the @univerjs/presets meta-package pulls in proprietary @univerjs-pro ones. */
function createUniver(container: HTMLElement): Runtime {
  const univer = new Univer({
    logLevel: LogLevel.WARN, theme: greenTheme, locale: LocaleType.EN_US,
    locales: { [LocaleType.EN_US]: mergeLocales(CoreEnUS, FilterEnUS, SortEnUS, CfEnUS, DvEnUS, FindEnUS, NoteEnUS, TableEnUS) },
  });
  const presets = [
    UniverSheetsCorePreset({ container, header: true, toolbar: true, contextMenu: true, formulaBar: true,
      footer: { sheetBar: true, statisticBar: true, menus: true, zoomSlider: true } } as Parameters<typeof UniverSheetsCorePreset>[0]),
    UniverSheetsFilterPreset(), UniverSheetsSortPreset(), UniverSheetsConditionalFormattingPreset(),
    UniverSheetsDataValidationPreset(), UniverSheetsFindReplacePreset(), UniverSheetsNotePreset(), UniverSheetsTablePreset(),
  ];
  const seen = new Set<string>();
  for (const preset of presets) {
    for (const entry of (preset as { plugins: unknown[] }).plugins) {
      const [plugin, options] = Array.isArray(entry) ? entry : [entry, undefined];
      const name = (plugin as { pluginName?: string }).pluginName || String(plugin);
      if (seen.has(name)) continue;
      seen.add(name);
      univer.registerPlugin(plugin as never, options as never);
    }
  }
  return { univer, univerAPI: FUniver.newAPI(univer) };
}

const col = (n: number) => { let s = ""; n += 1; while (n) { const r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = Math.floor((n - 1) / 26); } return s; };
const a1 = (r: { startRow: number; startColumn: number; endRow: number; endColumn: number }) =>
  `${col(r.startColumn)}${r.startRow + 1}:${col(r.endColumn)}${r.endRow + 1}`;

export default function SheetsWorkspace({ file: initial, think, setThink, onBack }: {
  file: OfficeFile; think: boolean; setThink: (t: boolean) => void; onBack: () => void;
}) {
  const [file, setFile] = useState(initial);
  const [save, setSave] = useState<SaveState>("idle");
  const [busy, setBusy] = useState(false);
  const [empty, setEmpty] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [charts, setCharts] = useState<Chart[]>([]);
  const [version, setVersion] = useState(0);            // bumps on every grid change: charts redraw from live cells
  const [inserting, setInserting] = useState<{ range: string; values: unknown[][] } | null>(null);
  const host = useRef<HTMLDivElement>(null);
  const runtime = useRef<{ rt: Runtime; sub: { dispose: () => void } } | null>(null);
  const journal = useRef<Entry[]>([]);
  const busyRef = useRef(false);  // while Vyum edits the file, the canvas only shows it: nothing is recorded or saved

  const names = useRef<Record<string, string>>({});   // sheet id -> name, kept current for renames
  const timer = useRef<number | null>(null);
  const saving = useRef<Promise<void> | null>(null);
  const loading = useRef(false);

  const flush = useCallback(async () => {
    if (saving.current) await saving.current;
    const entries = journal.current.splice(0);
    if (!entries.length) { setSave("saved"); return; }
    saving.current = (async () => {
      try {
        const r = await api.sheetSave(file.id, entries);
        if (r.file) setFile(r.file);
        setSave("saved");
      } catch { journal.current.unshift(...entries); setSave("error"); }
    })();
    await saving.current;
    saving.current = null;
  }, [file.id]);

  useEffect(() => {  // your unsaved edits are saved first, then Vyum's edit runs on top of them
    busyRef.current = busy;
    if (busy && timer.current) { window.clearTimeout(timer.current); timer.current = null; flush(); }
  }, [busy, flush]);

  const schedule = useCallback(() => {
    setSave("saving");
    setVersion((v) => v + 1);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(flush, 1200);
  }, [flush]);

  /** Turns one grid mutation into journal entries, in the order they happen. */
  const record = useCallback((rt: Runtime, cmd: { id: string; type?: number; params?: any }) => {
    if (busyRef.current) return;
    const p = cmd.params || {};
    const sheet = names.current[p.subUnitId] ?? null;
    const wb = rt.univerAPI.getActiveWorkbook();
    const push = (e: Entry) => journal.current.push(sheet ? { sheet, ...e } : e);
    switch (cmd.id) {
      case "sheet.mutation.set-range-values": {
        const styles = (wb?.save() as any)?.styles || {};
        const cells: any[] = [];
        for (const [r, row] of Object.entries(p.cellValue || {})) {
          for (const [c, cell] of Object.entries(row as Record<string, any>)) {
            const addr = `${col(Number(c))}${Number(r) + 1}`;
            const out: any = { cell: addr };
            if (cell && ("v" in cell || "f" in cell || "p" in cell)) {
              if (cell.f) out.formula = cell.f; else out.value = cell.v ?? (cell.p?.body?.dataStream?.replace(/\r\n$/, "") ?? null);
            } else if (cell === null) out.value = null;
            if (cell && "s" in cell && cell.s) {
              const st = typeof cell.s === "string" ? styles[cell.s] || {} : cell.s;
              out.style = { bold: st.bl === 1, italic: st.it === 1, fillColor: st.bg?.rgb ?? null, fontColor: st.cl?.rgb ?? null,
                            ...(st.fs ? { fontSize: st.fs } : {}) };
            }
            if (Object.keys(out).length > 1) cells.push(out);
          }
        }
        if (cells.length) push({ type: "cells", cells });
        break;
      }
      case "sheet.mutation.insert-row": push({ type: "insert_rows", row: p.range.startRow + 1, count: p.range.endRow - p.range.startRow + 1 }); break;
      case "sheet.mutation.remove-rows": push({ type: "delete_rows", row: p.range.startRow + 1, count: p.range.endRow - p.range.startRow + 1 }); break;
      case "sheet.mutation.insert-col": push({ type: "insert_cols", column: col(p.range.startColumn), count: p.range.endColumn - p.range.startColumn + 1 }); break;
      case "sheet.mutation.remove-col": push({ type: "delete_cols", column: col(p.range.startColumn), count: p.range.endColumn - p.range.startColumn + 1 }); break;
      case "sheet.mutation.set-worksheet-col-width":
        for (const r of p.ranges || []) {
          const w = typeof p.colWidth === "number" ? p.colWidth : p.colWidth?.[r.startColumn];
          if (w) push({ type: "col_width", column: col(r.startColumn), count: r.endColumn - r.startColumn + 1, widthPx: Math.round(w) });
        }
        break;
      case "sheet.mutation.set-worksheet-row-height":
        for (const r of p.ranges || []) {
          const h = typeof p.rowHeight === "number" ? p.rowHeight : p.rowHeight?.[r.startRow];
          if (h) push({ type: "row_height", row: r.startRow + 1, count: r.endRow - r.startRow + 1, heightPx: h });
        }
        break;
      case "sheet.mutation.add-worksheet-merge": for (const r of p.ranges || []) push({ type: "merge", range: a1(r) }); break;
      case "sheet.mutation.remove-worksheet-merge": for (const r of p.ranges || []) push({ type: "unmerge", range: a1(r) }); break;
      case "sheet.mutation.set-frozen": push({ type: "freeze", rows: Math.max(0, p.ySplit || 0), columns: Math.max(0, p.xSplit || 0) }); break;
      case "sheet.mutation.set-worksheet-name":
        journal.current.push({ type: "rename_sheet", sheet: names.current[p.subUnitId], name: p.name });
        names.current[p.subUnitId] = p.name;
        break;
      case "sheet.mutation.insert-sheet":
        names.current[p.sheet?.id] = p.sheet?.name;
        journal.current.push({ type: "add_sheet", name: p.sheet?.name });
        break;
      case "sheet.mutation.remove-sheet": journal.current.push({ type: "delete_sheet", sheet }); break;
      default: return;
    }
    schedule();
  }, [schedule]);

  /** Builds a fresh grid on a new element (Univer runs its own React tree inside; swapping in place races). */
  const focusRef = useRef<SheetTarget | null>(null);  // where Vyum is working right now (kept across reloads)
  const showFocus = useCallback((rt: Runtime, t: SheetTarget | null) => {
    if (!t) return;
    try {
      const wb = rt.univerAPI.getActiveWorkbook();
      const ws = wb?.getSheetByName(t.sheet);
      if (!ws) return;
      wb!.setActiveSheet(ws);
      ws.getRange(t.range).activate();  // selects it; the grid scrolls to the selection
    } catch { /* the sheet is not there yet */ }
  }, []);
  const focus = useCallback((t: SheetTarget | null) => {
    // null = the work is done: the label goes, but the canvas stays on the last place Vyum worked (see onChanged)
    if (t) focusRef.current = t;
    if (t && runtime.current) showFocus(runtime.current.rt, t);
    setWorking(t);
  }, [showFocus]);
  const [working, setWorking] = useState<SheetTarget | null>(null);

  const mount = useCallback((snap: any, highlight?: string[]) => new Promise<void>((resolve) => {
    window.setTimeout(() => {
      const el = host.current;
      if (!el) return resolve();
      const old = runtime.current;
      runtime.current = null;
      try { old?.sub.dispose(); old?.rt.univer.dispose(); } catch { /* already gone */ }
      el.replaceChildren();
      const mountEl = document.createElement("div");
      mountEl.className = "univer-mount";
      el.appendChild(mountEl);
      const rt = createUniver(mountEl);
      loading.current = true;
      rt.univerAPI.createWorkbook(snap as IWorkbookData);
      names.current = Object.fromEntries(Object.values(snap.sheets || {}).map((s: any) => [s.id, s.name]));
      const sub = rt.univerAPI.onCommandExecuted((cmd: any) => { if (!loading.current && cmd.type === 2) record(rt, cmd); });
      runtime.current = { rt, sub };
      if (focusRef.current) showFocus(rt, focusRef.current);
      else if (highlight?.length) {
        const range = highlight.length === 1 ? highlight[0] : `${highlight[0]}:${highlight[highlight.length - 1]}`;
        try { rt.univerAPI.getActiveWorkbook()!.getActiveSheet().getRange(range).activate(); } catch { /* not an A1 range */ }
      }
      window.setTimeout(() => { loading.current = false; setVersion((v) => v + 1); }, 500);
      resolve();
    }, 0);
  }), [record, showFocus]);

  const load = useCallback(async (highlight?: string[]) => {
    try {
      const t0 = performance.now();
      const snap: any = await api.sheetLoad(file.id);
      const used = Object.values(snap.sheets || {}).reduce((n: number, s: any) => n + (s.used?.rows || 0), 0);
      setEmpty(used === 0);
      setCharts(snap.charts || []);
      await mount(snap, highlight);
      if (used > 2000) setInfo(`${used.toLocaleString()} rows · loaded in ${((performance.now() - t0) / 1000).toFixed(1)} s`);
    } catch (e) { setError(String((e as Error).message)); }
  }, [file.id, mount]);

  useEffect(() => {
    load();
    return () => {
      if (timer.current) { window.clearTimeout(timer.current); flush(); }
      const old = runtime.current;
      runtime.current = null;
      window.setTimeout(() => { try { old?.sub.dispose(); old?.rt.univer.dispose(); } catch { /* already gone */ } }, 0);
    };
  }, [file.id]);

  /** The cells of a range, read live from the grid (charts redraw as the numbers change). */
  const valuesOf = useCallback((range: string, sheet?: string | null): unknown[][] => {
    try {
      const wb = runtime.current?.rt.univerAPI.getActiveWorkbook();
      const ws = (sheet && wb?.getSheetByName(sheet)) || wb?.getActiveSheet();
      return (ws?.getRange(range).getValues() as unknown[][]) || [];
    } catch { return []; }
  }, []);

  const insertChart = () => {
    const ws = runtime.current?.rt.univerAPI.getActiveWorkbook()?.getActiveSheet();
    let range = ws?.getSelection()?.getActiveRange()?.getA1Notation() || "";
    if (!range.includes(":")) range = ws?.getDataRange?.()?.getA1Notation?.() || "A1:B5";
    setInserting({ range, values: valuesOf(range) });
  };

  const persist = async (c: Chart, del = false) => setCharts((await api.chartSave(file.id, c, del)).charts);

  const onChanged = async (f: OfficeFile, changes: string[]) => {
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null; await flush(); }
    setFile(f);
    const cells = changes.map((c) => c.match(/^([A-Z]{1,3}\d+)/)?.[1]).filter(Boolean) as string[];
    await load(cells.sort((a, b) => a.localeCompare(b, undefined, { numeric: true })));
    setSave("saved");
    window.setTimeout(() => { focusRef.current = null; }, 800);  // later reloads open as usual
  };
  const undo = async () => { focusRef.current = null; setFile(await api.officeUndo(file.id)); await load(); };
  const [dash, setDash] = useState<string | null>(null);
  const [dashList, setDashList] = useState<{ id: string; title: string }[]>([]);
  const refreshDash = () => api.dashboards(file.id).then(setDashList).catch(() => undefined);
  useEffect(() => { refreshDash(); }, [file.id, dash]);  // eslint-disable-line react-hooks/exhaustive-deps
  const [inject, setInject] = useState<{ text: string; n: number } | null>(null);
  const [asking, setAsking] = useState(false);
  const [ask, setAsk] = useState("");
  const newDashboard = (text: string) => {
    if (!text.trim()) return;
    setAsking(false); setAsk("");
    setInject({ text: /dashboard|डैशबोर्ड/i.test(text) ? text : `Make a dashboard: ${text}`, n: Date.now() });
  };
  const renameDash = async (d: { id: string; title: string }) => {
    const title = window.prompt(t("Dashboard name"), d.title);
    if (title && title.trim()) { await api.dashboardRename(file.id, d.id, title.trim()); refreshDash(); }
  };
  const deleteDash = async (d: { id: string; title: string }) => {
    if (!window.confirm(`${t("Delete dashboard")} “${d.title}”?`)) return;
    await api.dashboardDelete(file.id, d.id);
    if (dash === d.id) setDash(null);
    refreshDash();
  };

  return (
    <div className="workspace sheets">
      <DocHeader file={file} save={save} onBack={onBack} onUndo={undo}>
        <div className="sheet-actions">
          <button className="pill green" onClick={insertChart} title="Chart the selected cells"><I.Chart size={15} /> {t("Insert chart")}</button>
          <button className="pill blue" onClick={() => runtime.current?.rt.univerAPI.getActiveWorkbook()?.getActiveSheet().setFrozenRows(1)}
                  title="Keep the header row visible"><I.Pin size={14} /> {t("Freeze header")}</button>
          {dashList.length > 0 && (
            <button className={`pill violet ${dash ? "on" : ""}`} onClick={() => setDash(dash ? null : dashList[0].id)}
                    title="Charts, KPIs and filters computed from this workbook"><I.Chart size={15} /> {t(dash ? "Sheet" : "Dashboard")}</button>
          )}
          {info && <span className="pill info">{info}</span>}
        </div>
      </DocHeader>
      <div className="workspace-body">
        <DocChat file={file} think={think} setThink={setThink} empty={empty} onChanged={onChanged} busy={busy} setBusy={setBusy}
                 onProgress={() => { load(); }} onFocus={focus} onDashboard={(id) => { setDash(id); refreshDash(); }} inject={inject} />
        <div className="sheet-pane">
        {dash && <Dashboard file={file} id={dash} onClose={() => setDash(null)} />}
        <div className={`sheet-canvas ${busy ? "busy" : ""}`} style={dash ? { display: "none" } : undefined}>
          {working && (
            <div className="working-on"><span className="spinner sm" /> {t("Vyum is working on")} <b>{working.sheet}</b> · {working.range}</div>
          )}
          {error && <div className="card error">{error}</div>}
          <div ref={host} className="univer-host" />
          <div className="chart-layer" data-version={version}>
            {charts.map((c) => (
              <ChartCard key={c.id} chart={c} values={valuesOf(c.range, c.sheet)}
                         onChange={persist} onDelete={() => persist(c, true)}
                         onEmbed={async () => { await api.chartEmbed(file.id, c.id!); await load(); }} />
            ))}
          </div>
        </div>
        <div className="dash-tabs">
          <button className={`dash-tab ${dash ? "" : "on"}`} onClick={() => setDash(null)}><I.Grid size={13} /> {t("Sheets")}</button>
          {dashList.map((d) => (
            <span key={d.id} className={`dash-tab ${dash === d.id ? "on" : ""}`} onClick={() => setDash(d.id)}
                  onDoubleClick={() => renameDash(d)} title={t("Double-click to rename")}>
              <I.Chart size={13} /> {d.title}
              <button className="x" onClick={(e) => { e.stopPropagation(); deleteDash(d); }} title={t("Delete dashboard")}><I.Close size={10} /></button>
            </span>
          ))}
          {asking ? (
            <form className="dash-ask" onSubmit={(e) => { e.preventDefault(); newDashboard(ask); }}>
              <input autoFocus value={ask} onChange={(e) => setAsk(e.target.value)} onBlur={() => !ask && setAsking(false)}
                     placeholder={t("What should it show? e.g. revenue by region and month")} />
              <button className="btn primary" disabled={!ask.trim() || busy}>{t("Create")}</button>
            </form>
          ) : (
            <button className="dash-tab add" disabled={busy} onClick={() => setAsking(true)} title={t("New dashboard")}>
              <I.Plus size={13} /> {t("Dashboard")}
            </button>
          )}
        </div>
        </div>
      </div>
      {inserting && <InsertChart range={inserting.range} values={inserting.values} onCancel={() => setInserting(null)}
                                 onInsert={async (type, title) => {
                                   const sheet = runtime.current?.rt.univerAPI.getActiveWorkbook()?.getActiveSheet().getSheetName();
                                   await persist({ type, title, range: inserting.range, sheet, x: 140 + charts.length * 28, y: 110 + charts.length * 28, w: 540, h: 330 });
                                   setInserting(null);
                                 }} />}
    </div>
  );
}
