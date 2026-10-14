import { useEffect, useState } from "react";
import { api, type SetupStatus } from "./api";
import { t } from "./i18n";

const gb = (bytes: number) => (bytes / 1e9).toFixed(bytes >= 1e10 ? 0 : bytes < 1e9 ? 2 : 1);
const size = (bytes: number) => (bytes < 1e9 ? `${Math.max(1, Math.round(bytes / 1e6))} MB` : `${gb(bytes)} GB`);

/** First launch of an installed Vyum: the models are downloaded once, then everything runs offline. */
export function Setup({ initial, onReady }: { initial: SetupStatus; onReady: () => void }) {
  const [s, setS] = useState(initial);
  const [vision, setVision] = useState(initial.vision);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (!s.running) return;
    const timer = setInterval(async () => {
      const next = await api.setupStatus().catch(() => null);
      if (!next) return;
      setS(next);
      if (next.ready) { clearInterval(timer); onReady(); }
    }, 1000);
    return () => clearInterval(timer);
  }, [s.running, onReady]);

  const start = async () => {
    setStarting(true);
    try { setS(await api.setupStart(vision)); } finally { setStarting(false); }
  };

  const todo = s.items.filter((i) => !i.done);
  const total = todo.reduce((a, i) => a + i.bytes, 0) + (vision && !s.items.some((i) => i.id === "vision") ? 6e9 : 0);
  const got = todo.reduce((a, i) => a + Math.min(i.got ?? 0, i.bytes), 0);
  const pct = total ? Math.min(100, Math.round((got / total) * 100)) : 0;
  const model = s.tier === "8b" ? "Qwen3-8B" : "Qwen3-4B";

  return (
    <div className="setup">
      <div className="setup-card">
        <span className="mark lg">V</span>
        <h1>{t("Set up Vyum")}</h1>
        <p>{t("Vyum downloads its AI models once. After that, everything runs on this computer and your documents never leave it.")}</p>
        <p className="muted small">
          {t("This computer has {ram} GB of memory, so Vyum uses {model}.").replace("{ram}", String(Math.round(s.ram_gb ?? 0))).replace("{model}", model)}
        </p>
        <ul className="setup-items">
          {s.items.map((i) => {
            const p = i.done ? 100 : i.got ? Math.min(100, Math.round((i.got / i.bytes) * 100)) : 0;
            return (
              <li key={i.id} className={i.done ? "done" : i.state ? "active" : ""}>
                <div className="setup-row">
                  <span>{t(i.label)}</span>
                  <span className="muted small">{i.done ? "✓" : i.state === "checking" ? t("Checking") : i.state === "unpacking" ? t("Unpacking")
                    : i.state === "preparing" ? t("Preparing") : size(i.bytes)}</span>
                </div>
                {!i.done && i.state && <div className="bar"><div style={{ width: `${p}%` }} /></div>}
              </li>
            );
          })}
        </ul>
        {!s.running && (
          <label className="setup-vision">
            <input type="checkbox" checked={vision} onChange={(e) => setVision(e.target.checked)} />
            {t("Also read pictures and charts inside documents (6 GB more)")}
          </label>
        )}
        {s.error && <p className="error-text">{s.error}</p>}
        {s.running ? (
          <>
            <div className="bar big"><div style={{ width: `${pct}%` }} /></div>
            <p className="muted small">{t("{got} of {total} GB · you can keep this window open; an interrupted download continues where it stopped.")
              .replace("{got}", gb(got)).replace("{total}", gb(total))}</p>
          </>
        ) : (
          <button className="primary" disabled={starting} onClick={start}>
            {s.error ? t("Try again") : t("Download ({size} GB) and set up").replace("{size}", gb(total))}
          </button>
        )}
      </div>
    </div>
  );
}
