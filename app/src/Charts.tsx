import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart, RadarChart, ScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, RadarComponent, TitleComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { Chart } from "./api";
import * as I from "./icons";

echarts.use([BarChart, LineChart, PieChart, RadarChart, ScatterChart, GridComponent, LegendComponent, RadarComponent,
  TitleComponent, TooltipComponent, CanvasRenderer]);

export const PALETTE = ["#1a73e8", "#34a853", "#fbbc04", "#ea4335", "#a142f4", "#24c1e0", "#f97b22", "#e52592"];
export const CHART_KINDS: { type: Chart["type"]; label: string }[] = [
  { type: "column", label: "Column" }, { type: "bar", label: "Bar" }, { type: "line", label: "Line" }, { type: "area", label: "Area" },
  { type: "pie", label: "Pie" }, { type: "doughnut", label: "Doughnut" }, { type: "scatter", label: "Scatter" }, { type: "radar", label: "Radar" },
];

const num = (v: unknown) => (typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" && !isNaN(Number(v.replace(/[, ]/g, ""))) ? Number(v.replace(/[, ]/g, "")) : null);

/** An ECharts option from a block of cells: header row first, labels in the first column, one series per other column. */
export function chartOption(type: Chart["type"], title: string | undefined, values: unknown[][], compact = false, only?: string[]) {
  const rows = values.filter((r) => r && r.some((v) => v !== null && v !== ""));
  const header = (rows[0] || []).map((h) => String(h ?? ""));
  const hasHeader = header.slice(1).some((h) => h && num(h) === null);
  const body = hasHeader ? rows.slice(1) : rows;
  const labels = body.map((r) => String(r[0] ?? ""));
  const names = hasHeader ? header.slice(1) : (rows[0] || []).slice(1).map((_, i) => `Series ${i + 1}`);
  const all = names.map((name, j) => ({ name, data: body.map((r) => num(r[j + 1])) }));
  const picked = only?.length ? all.filter((x) => only.some((o) => o.toLowerCase() === x.name.toLowerCase())) : [];
  const series = picked.length ? picked : all;
  const base = {
    color: PALETTE, animationDuration: 500,
    title: title && !compact ? { text: title, left: 12, top: 8, textStyle: { fontSize: 14, fontWeight: 600, color: "#202124" } } : undefined,
    tooltip: { trigger: type === "pie" || type === "doughnut" || type === "radar" ? "item" : "axis" },
    legend: compact ? undefined : { bottom: 4, type: "scroll" },
    textStyle: { fontFamily: "-apple-system, Inter, Arial, sans-serif" },
  };
  if (type === "pie" || type === "doughnut") {
    return { ...base, series: [{ type: "pie", radius: type === "doughnut" ? ["45%", "70%"] : "68%", center: ["50%", compact ? "50%" : "52%"],
      itemStyle: { borderColor: "#fff", borderWidth: 2, borderRadius: 4 }, label: { show: !compact },
      data: labels.map((l, i) => ({ name: l, value: series[0]?.data[i] ?? 0 })) }] };
  }
  if (type === "radar") {
    const max = Math.max(1, ...series.flatMap((s) => s.data.map((v) => v ?? 0)));
    return { ...base, radar: { indicator: labels.map((l) => ({ name: compact ? "" : l, max })), radius: compact ? "70%" : "62%" },
      series: [{ type: "radar", areaStyle: { opacity: 0.15 }, data: series.map((s) => ({ name: s.name, value: s.data.map((v) => v ?? 0) })) }] };
  }
  const horizontal = type === "bar";
  const cat = { type: "category", data: labels, axisLabel: { show: !compact, color: "#5f6368", hideOverlap: true }, axisTick: { show: !compact },
                axisLine: { lineStyle: { color: "#dadce0" } } };
  const val = { type: "value", axisLabel: { show: !compact, color: "#5f6368" }, splitLine: { show: !compact, lineStyle: { color: "#eef0f2" } } };
  return {
    ...base,
    grid: { left: 12, right: 18, top: title && !compact ? 44 : 14, bottom: compact ? 8 : 40, containLabel: true },
    xAxis: horizontal ? val : cat, yAxis: horizontal ? cat : val,
    series: series.map((s) => ({
      name: s.name, data: s.data, type: type === "column" || type === "bar" ? "bar" : type === "scatter" ? "scatter" : "line",
      smooth: type === "line" || type === "area" ? 0.25 : undefined, symbolSize: type === "scatter" ? 9 : 5,
      areaStyle: type === "area" ? { opacity: 0.18 } : undefined, barMaxWidth: 42,
      itemStyle: type === "column" || type === "bar" ? { borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] } : undefined,
    })),
  };
}

export function EChart({ option, className }: { option: object; className?: string }) {
  const el = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!el.current) return;
    inst.current = echarts.init(el.current, undefined, { renderer: "canvas" });
    const ro = new ResizeObserver(() => inst.current?.resize());
    ro.observe(el.current);
    return () => { ro.disconnect(); inst.current?.dispose(); inst.current = null; };
  }, []);
  useEffect(() => { inst.current?.setOption(option as echarts.EChartsCoreOption, true); }, [option]);
  return <div ref={el} className={className} />;
}

/** A chart floating over the grid: drag it by its title bar, resize it from the corner. */
export function ChartCard({ chart, values, onChange, onDelete, onEmbed }: {
  chart: Chart; values: unknown[][]; onChange: (c: Chart) => void; onDelete: () => void; onEmbed: () => void;
}) {
  const [c, setC] = useState(chart);
  const [menu, setMenu] = useState(false);
  useEffect(() => setC(chart), [chart]);
  const drag = (e: React.MouseEvent, mode: "move" | "size") => {
    e.preventDefault();
    const start = { x: e.clientX, y: e.clientY, c };
    const move = (ev: MouseEvent) => {
      const dx = ev.clientX - start.x, dy = ev.clientY - start.y;
      setC(mode === "move" ? { ...start.c, x: Math.max(0, start.c.x + dx), y: Math.max(0, start.c.y + dy) }
                          : { ...start.c, w: Math.max(260, start.c.w + dx), h: Math.max(180, start.c.h + dy) });
    };
    const up = (ev: MouseEvent) => {
      window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up);
      const dx = ev.clientX - start.x, dy = ev.clientY - start.y;
      if (dx || dy) onChange(mode === "move" ? { ...start.c, x: Math.max(0, start.c.x + dx), y: Math.max(0, start.c.y + dy) }
                                             : { ...start.c, w: Math.max(260, start.c.w + dx), h: Math.max(180, start.c.h + dy) });
    };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  };
  return (
    <div className="chart-card" style={{ left: c.x, top: c.y, width: c.w, height: c.h }}>
      <div className="chart-bar" onMouseDown={(e) => drag(e, "move")}>
        <span className="chart-range">{c.sheet ? `${c.sheet}!` : ""}{c.range}</span>
        <span className="spacer" />
        <select value={c.type} onMouseDown={(e) => e.stopPropagation()} onChange={(e) => onChange({ ...c, type: e.target.value as Chart["type"] })}>
          {CHART_KINDS.map((k) => <option key={k.type} value={k.type}>{k.label}</option>)}
        </select>
        <div style={{ position: "relative" }} onMouseDown={(e) => e.stopPropagation()}>
          <button className="icon-btn sm" onClick={() => setMenu(!menu)} aria-label="Chart options">⋮</button>
          {menu && (
            <div className="popover down menu-pop right" onMouseLeave={() => setMenu(false)}>
              <button className="menu-item" onClick={() => { const t = window.prompt("Chart title", c.title || ""); if (t !== null) onChange({ ...c, title: t }); setMenu(false); }}>Edit title</button>
              <button className="menu-item" onClick={() => { const r = window.prompt("Data range (e.g. A1:C9)", c.range); if (r) onChange({ ...c, range: r.toUpperCase() }); setMenu(false); }}>Change data range</button>
              <button className="menu-item" disabled={c.embedded} onClick={() => { onEmbed(); setMenu(false); }}>
                {c.embedded ? "Saved in the .xlsx file" : "Save into the .xlsx (Excel chart)"}</button>
              <button className="menu-item danger" onClick={() => { onDelete(); setMenu(false); }}><I.Trash size={14} /> Delete chart</button>
            </div>
          )}
        </div>
      </div>
      <EChart className="chart-body" option={chartOption(c.type, c.title, values, false, c.series)} />
      <span className="chart-resize" onMouseDown={(e) => drag(e, "size")} />
    </div>
  );
}

/** The Insert chart dialog: every chart type previewed live from the selected cells. */
export function InsertChart({ range, values, onCancel, onInsert }: {
  range: string; values: unknown[][]; onCancel: () => void; onInsert: (type: Chart["type"], title: string) => void;
}) {
  const [type, setType] = useState<Chart["type"]>("column");
  const [title, setTitle] = useState("");
  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className="dialog chart-dialog" role="dialog" aria-label="Insert chart">
        <h2>Insert chart <span className="muted small">from {range}</span></h2>
        <div className="chart-kinds">
          {CHART_KINDS.map((k) => (
            <button key={k.type} className={`chart-kind ${type === k.type ? "on" : ""}`} onClick={() => setType(k.type)}>
              <EChart className="chart-thumb" option={chartOption(k.type, undefined, values, true)} />
              <span>{k.label}</span>
            </button>
          ))}
        </div>
        <EChart className="chart-preview" option={chartOption(type, title || undefined, values)} />
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Chart title (optional)" aria-label="Chart title" />
        <div className="actions">
          <button className="btn secondary" onClick={onCancel}>Cancel</button>
          <button className="btn primary sheets" onClick={() => onInsert(type, title)}>Insert chart</button>
        </div>
      </div>
    </div>
  );
}
