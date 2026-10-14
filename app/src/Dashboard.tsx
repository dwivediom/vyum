import { lang, t } from "./i18n";
import { useEffect, useMemo, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, FunnelChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { api, type DashChart, type DashData, type OfficeFile } from "./api";
import * as I from "./icons";

echarts.use([BarChart, LineChart, PieChart, ScatterChart, FunnelChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

const COLORS = ["#4f6bed", "#16a37f", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4", "#f97316", "#ec4899", "#84cc16", "#64748b"];
const KPI_TINTS = ["#4f6bed", "#16a37f", "#f59e0b", "#8b5cf6", "#ef4444", "#06b6d4"];

/** 20,710 -> "20.7K" (or "20.7 हज़ार"-style lakh/crore when the app is in Hindi); 0.335 -> "33.5%" for percents. */
export function fmt(v: number | null | undefined, kind: string, short = true): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (kind === "percent") return `${(v * 100).toFixed(1)}%`;
  const a = Math.abs(v);
  if (short && lang === "hi") {  // Indian style: crore and lakh, and full digits (83,340) below a lakh
    if (a >= 1e7) return `${(v / 1e7).toFixed(2)} Cr`;
    if (a >= 1e5) return `${(v / 1e5).toFixed(2)} L`;
    return v.toLocaleString("en-IN", { maximumFractionDigits: a >= 100 ? 0 : 2 });
  }
  if (short && a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (short && a >= 1e4) return `${(v / 1e3).toFixed(1)}K`;
  return v.toLocaleString(lang === "hi" ? "en-IN" : undefined, { maximumFractionDigits: a >= 100 ? 0 : 2 });
}

function option(c: DashChart, selected: string[], dark: boolean): echarts.EChartsCoreOption {
  const ink = dark ? "#d6d3cc" : "#3d3b35";
  const grid = dark ? "rgba(255,255,255,.08)" : "rgba(0,0,0,.07)";
  const k0 = c.series?.[0]?.kind || "number";
  const dim = (i: number) => (selected.length && !selected.includes(c.categories![i]) ? 0.3 : 1);
  const axisLabel = { color: ink, fontSize: 11 };
  const base = { color: COLORS, textStyle: { color: ink }, tooltip: { trigger: "item" as const, valueFormatter: (v: number) => fmt(v, k0, false) } };
  if (c.type === "pie" || c.type === "donut") {
    return { ...base, legend: { type: "scroll", bottom: 0, textStyle: { color: ink } },
      series: [{ type: "pie", radius: c.type === "donut" ? ["46%", "72%"] : ["0%", "72%"], center: ["50%", "45%"],
        itemStyle: { borderColor: dark ? "#2f2f2c" : "#fff", borderWidth: 2, borderRadius: 4 },
        label: { color: ink, formatter: "{d}%" },
        data: c.categories!.map((name, i) => ({ name, value: c.series![0].data[i], itemStyle: { opacity: dim(i) } })) }] };
  }
  if (c.type === "funnel") {
    return { ...base, series: [{ type: "funnel", left: "10%", width: "80%", label: { color: ink },
      data: c.categories!.map((name, i) => ({ name, value: c.series![0].data[i] })) }] };
  }
  if (c.type === "scatter") {
    return { ...base, grid: { left: 56, right: 18, top: 16, bottom: 36 },
      xAxis: { type: "value", name: c.measures?.[0]?.label, axisLabel, splitLine: { lineStyle: { color: grid } } },
      yAxis: { type: "value", name: c.measures?.[1]?.label, axisLabel, splitLine: { lineStyle: { color: grid } } },
      series: [{ type: "scatter", symbolSize: 9, data: c.points }] };
  }
  const horizontal = c.type === "bar";
  const cat = { type: "category" as const, data: c.categories, axisLabel: { ...axisLabel, interval: 0, rotate: !horizontal && c.categories!.length > 6 ? 30 : 0 },
                axisLine: { lineStyle: { color: grid } }, inverse: horizontal };
  const valAxis = (kind: string) => ({ type: "value" as const, axisLabel: { ...axisLabel, formatter: (v: number) => fmt(v, kind) },
                                      splitLine: { lineStyle: { color: grid } } });
  const combo = c.type === "combo" && c.series!.length > 1;
  const series = c.series!.map((s, si) => {
    const asLine = c.type === "line" || c.type === "area" || (combo && si > 0);
    return {
      name: s.name, type: asLine ? "line" : "bar", smooth: asLine, yAxisIndex: combo && si > 0 ? 1 : 0,
      areaStyle: c.type === "area" ? { opacity: 0.25 } : undefined, barMaxWidth: 38, symbolSize: 7,
      itemStyle: asLine ? undefined : { borderRadius: horizontal ? [0, 6, 6, 0] : [6, 6, 0, 0] },
      label: { show: !asLine && c.categories!.length <= 8, position: horizontal ? "right" : "top", color: ink, fontSize: 11,
               formatter: (p: { value: number }) => fmt(p.value, s.kind) },
      data: s.data.map((v, i) => ({ value: v, itemStyle: { opacity: dim(i), color: !asLine && c.series!.length === 1 && !combo ? COLORS[i % COLORS.length] : undefined } })),
    };
  });
  return {
    ...base, tooltip: { trigger: "axis", valueFormatter: (v: number) => fmt(v, k0, false) },
    legend: c.series!.length > 1 ? { top: 0, textStyle: { color: ink } } : undefined,
    grid: { left: horizontal ? 96 : 56, right: combo ? 56 : 20, top: c.series!.length > 1 ? 30 : 14, bottom: 40, containLabel: false },
    xAxis: horizontal ? valAxis(k0) : cat,
    yAxis: horizontal ? cat : (combo ? [valAxis(k0), valAxis(c.series![1].kind)] : valAxis(k0)),
    series,
  };
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const slug = (s: string) => s.replace(/[^\w\- ]+/g, "").trim().replace(/\s+/g, " ") || "chart";
const loadImage = (src: string) => new Promise<HTMLImageElement>((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = fail; i.src = src; });
const toBase64 = (bytes: Uint8Array) => { let b = ""; for (let i = 0; i < bytes.length; i += 0x8000) b += String.fromCharCode(...bytes.subarray(i, i + 0x8000)); return btoa(b); };

/** The chart as exported: same picture, with its title on top so the image makes sense on its own. */
function exportOption(c: DashChart, selected: string[], dark: boolean): echarts.EChartsCoreOption {
  const o: any = option(c, selected, dark);
  o.title = { text: c.title, left: 10, top: 6, textStyle: { fontSize: 14, fontWeight: 600, color: dark ? "#ecebe6" : "#1f1e1b" } };
  if (o.grid) o.grid = { ...o.grid, top: (o.grid.top || 14) + 30 };
  if (o.legend) o.legend = { ...o.legend, top: o.legend.bottom === 0 ? undefined : 30 };
  if (Array.isArray(o.series)) o.series = o.series.map((s: any) => (s.type === "pie" ? { ...s, center: ["50%", "52%"] } : s));
  return o;
}

/** The chart drawing itself again (its build-up animation), captured frame by frame into an animated GIF. */
async function recordGif(inst: echarts.ECharts, opt: echarts.EChartsCoreOption, bg: string): Promise<string> {
  const { GIFEncoder, quantize, applyPalette } = await import("gifenc");
  const w = inst.getWidth(), h = inst.getHeight();
  const canvas = document.createElement("canvas");
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  const gif = GIFEncoder();
  inst.clear();
  inst.setOption({ ...(opt as object), animation: true, animationDuration: 1100, animationEasing: "cubicOut" }, true);
  const frames = 20;
  for (let i = 0; i < frames; i++) {
    await sleep(65);
    ctx.drawImage(await loadImage(inst.getDataURL({ pixelRatio: 1, backgroundColor: bg })), 0, 0, w, h);
    const { data } = ctx.getImageData(0, 0, w, h);
    const palette = quantize(data, 256);
    gif.writeFrame(applyPalette(data, palette), w, h, { palette, delay: i === frames - 1 ? 2000 : 65 });
  }
  gif.finish();
  return toBase64(gif.bytes());
}

function Visual({ c, selected, onPick, dark, onExported }: {
  c: DashChart; selected: string[]; onPick: (v: string) => void; dark: boolean; onExported: (r: { path: string; name: string }) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);
  const [busy, setBusy] = useState("");
  const bg = dark ? "#2f2f2c" : "#ffffff";
  const exportAs = async (kind: "png" | "gif" | "csv") => {
    setBusy(kind);
    try {
      let data = "";
      if (kind === "csv") {
        const rows = [[c.dimension || "", ...c.series!.map((s) => s.name)], ...c.categories!.map((cat, i) => [cat, ...c.series!.map((s) => s.data[i] ?? "")])];
        data = btoa(unescape(encodeURIComponent(rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n"))));
      } else if (inst.current) {
        if (kind === "png") {
          inst.current.setOption({ ...(exportOption(c, selected, dark) as object), animation: false }, true);
          data = inst.current.getDataURL({ pixelRatio: 2, backgroundColor: bg });
        } else {
          data = await recordGif(inst.current, exportOption(c, selected, dark), bg);
        }
        inst.current.setOption(option(c, selected, dark), true);
      }
      onExported(await api.saveExport(`${slug(c.title)}.${kind}`, data));
    } finally { setBusy(""); }
  };
  const pick = useRef(onPick);
  pick.current = onPick;
  useEffect(() => {
    if (!el.current || c.type === "table") return;
    inst.current = echarts.init(el.current, undefined, { renderer: "canvas" });
    inst.current.on("click", (p: any) => { if (p?.name) pick.current(String(p.name)); });  // cross-filter
    const ro = new ResizeObserver(() => inst.current?.resize());
    ro.observe(el.current);
    return () => { ro.disconnect(); inst.current?.dispose(); inst.current = null; };
  }, [c.type]);
  useEffect(() => { inst.current?.setOption(option(c, selected, dark), true); }, [c, selected, dark]);
  const tools = (
    <div className="visual-tools">
      {c.type === "table"
        ? <button className="ghost" onClick={() => exportAs("csv")} disabled={!!busy} title="Save as CSV">{busy === "csv" ? <span className="spinner sm" /> : <I.Download size={13} />} CSV</button>
        : <>
            <button className="ghost" onClick={() => exportAs("png")} disabled={!!busy} title="Save as PNG image">{busy === "png" ? <span className="spinner sm" /> : <I.Download size={13} />} PNG</button>
            <button className="ghost" onClick={() => exportAs("gif")} disabled={!!busy} title="Save as animated GIF">{busy === "gif" ? <span className="spinner sm" /> : <I.Download size={13} />} GIF</button>
          </>}
    </div>
  );
  if (c.type === "table") {
    return (
      <>{tools}<div className="dash-table">
        <table>
          <thead><tr><th>{c.dimension}</th>{c.series!.map((s) => <th key={s.name}>{s.name}</th>)}</tr></thead>
          <tbody>{c.categories!.map((cat, i) => (
            <tr key={cat} className={selected.includes(cat) ? "on" : ""} onClick={() => onPick(cat)}>
              <td>{cat}</td>{c.series!.map((s) => <td key={s.name}>{fmt(s.data[i], s.kind, false)}</td>)}
            </tr>))}
          </tbody>
        </table>
      </div></>
    );
  }
  return <>{tools}<div ref={el} className="dash-chart" /></>;
}

/** A Power-BI-style page: KPI cards, slicers and charts. Clicking a bar, slice or row filters every visual; all numbers
 *  are computed by the engine from the workbook for the current filters. */
export function Dashboard({ file, id, onClose }: { file: OfficeFile; id: string; onClose: () => void }) {
  const [data, setData] = useState<DashData | null>(null);
  const [filters, setFilters] = useState<Record<string, string[]>>({});
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<{ path: string; name: string } | null>(null);
  const dark = useMemo(() => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false, []);

  useEffect(() => { setFilters({}); }, [id]);
  useEffect(() => {
    let live = true;
    api.dashboardRender(file.id, id, filters).then((d) => { if (live) { setData(d); setError(""); } })
      .catch((e) => live && setError(String((e as Error).message)));
    return () => { live = false; };
  }, [file.id, id, filters, file.updated_at]);

  const toggle = (key: string, v: string) => setFilters((f) => {
    const cur = f[key] || [];
    const next = cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v];
    const out = { ...f, [key]: next };
    if (!next.length) delete out[key];
    return out;
  });
  const active = Object.entries(filters).filter(([, v]) => v.length);

  if (error) return <div className="dash"><div className="card error">{error}</div></div>;
  if (!data) return <div className="dash"><div className="status-line"><span className="spinner" />{t("Computing the dashboard")}</div></div>;
  return (
    <div className="dash">
      <div className="dash-head">
        <div>
          <h2><I.Chart size={20} /> {data.title}</h2>
          <div className="muted small">{data.rows.toLocaleString()} {t("of")} {data.total_rows.toLocaleString()} {t("rows")} · {t("from")} “{data.source}” · {t("computed by Vyum from your data")}</div>
        </div>
        <span className="spacer" />
        {active.length > 0 && <button className="btn secondary" onClick={() => setFilters({})}><I.Close size={14} /> {t("Clear filters")}</button>}
        <button className="btn secondary" onClick={onClose}><I.Grid size={14} /> {t("Back to sheet")}</button>
      </div>

      {data.slicers.length > 0 && (
        <div className="slicers">
          {data.slicers.map((s) => (
            <div key={s.column} className="slicer">
              <span className="slicer-name">{s.column}</span>
              <div className="slicer-values">
                {s.values.slice(0, 14).map((v) => (
                  <button key={v} className={`chip ${(filters[s.column] || []).includes(v) ? "on" : ""}`} onClick={() => toggle(s.column, v)}>{v}</button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      {active.length > 0 && (
        <div className="dash-active">{active.map(([k, vs]) => vs.map((v) => (
          <button key={k + v} className="chip on" onClick={() => toggle(k, v)}>{k.split("|")[0]}: {v} <I.Close size={11} /></button>)))}
        </div>
      )}

      <div className="kpis">
        {data.kpis.map((k, i) => (
          <div key={k.label} className="kpi" style={{ ["--tint" as any]: KPI_TINTS[i % KPI_TINTS.length] }}>
            <div className="kpi-label">{k.label}</div>
            <div className="kpi-value" title={k.value === null ? "" : fmt(k.value, k.kind, false)}>{fmt(k.value, k.kind)}</div>
          </div>
        ))}
      </div>

      {saved && (
        <div className="export-toast">
          <I.Check size={14} /> {t("Saved")} {saved.name} · Documents/Vyum/Exports
          <button className="ghost" onClick={() => api.revealExport(saved.path)}>{t("Show in Finder")}</button>
          <button className="ghost" onClick={() => setSaved(null)}><I.Close size={12} /></button>
        </div>
      )}
      <div className="visuals">
        {data.charts.map((c) => (
          <div key={c.id} className={`visual ${c.type === "table" ? "wide" : ""}`}>
            <div className="visual-title">{c.title}</div>
            <Visual c={c} dark={dark} onExported={setSaved} selected={c.filter_key ? filters[c.filter_key] || [] : []}
                    onPick={(v) => c.filter_key && toggle(c.filter_key, v)} />
          </div>
        ))}
      </div>
    </div>
  );
}
