import { t } from "./i18n";
import * as I from "./icons";

export type Section = "chat" | "projects" | "office" | "mail" | "overview";

const ITEMS: { key: Section; label: string; icon: (p: { size?: number }) => JSX.Element }[] = [
  { key: "chat", label: "Chats", icon: I.Chat },
  { key: "projects", label: "Projects", icon: I.Folder },
  { key: "office", label: "Office", icon: I.Docs },
  { key: "mail", label: "Mail", icon: I.Mail },
  { key: "overview", label: "Overview", icon: I.Grid },
];

/** The narrow icon bar at the far left: Vyum's logo, the sections, and settings at the bottom. */
export default function Rail({ section, onSection, onHome, onSettings, web }: {
  section: Section; onSection: (s: Section) => void; onHome: () => void; onSettings: () => void; web: boolean;
}) {
  return (
    <nav className="rail" aria-label="Sections">
      <button className="rail-logo" onClick={onHome} title="Vyum · new chat"><I.Logo size={30} /></button>
      {ITEMS.map((it) => (
        <button key={it.key} className={`rail-item ${section === it.key ? "on" : ""}`} onClick={() => onSection(it.key)}
                aria-label={t(it.label)} aria-current={section === it.key ? "page" : undefined}>
          <it.icon size={19} />
          <span className="rail-label">{t(it.label)}</span>
        </button>
      ))}
      <span className="spacer" />
      <span className={`rail-dot ${web ? "web" : ""}`} title={t(web ? "Web search on · files stay on this Mac" : "Offline · models ready")} />
      <button className="rail-item" onClick={onSettings} aria-label={t("Settings")} title={t("Settings")}><I.Gear size={18} /></button>
    </nav>
  );
}
