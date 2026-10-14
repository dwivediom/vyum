// Small inline icons (no icon font, so the app stays fully offline).
type P = { size?: number; className?: string };
const svg = (path: JSX.Element, { size = 18, className }: P = {}) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
       strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">{path}</svg>
);

export const Plus = (p: P) => svg(<path d="M12 5v14M5 12h14" />, p);
export const ArrowUp = (p: P) => svg(<path d="M12 19V5M5 12l7-7 7 7" />, p);
export const Highlighter = (p: P) => svg(<><path d="m9 11-6 6v3h9l3-3" /><path d="m22 12-4.6 4.6a2 2 0 0 1-2.8 0l-5.2-5.2a2 2 0 0 1 0-2.8L14 4" /></>, p);
export const Stop = (p: P) => svg(<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" stroke="none" />, p);
export const Folder = (p: P) => svg(<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />, p);
export const FileIcon = (p: P) => svg(<><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></>, p);
export const Paperclip = (p: P) => svg(<path d="M21 12.5l-8.5 8.5a5 5 0 0 1-7-7l9-9a3.3 3.3 0 0 1 4.7 4.7l-9 9a1.7 1.7 0 0 1-2.4-2.4L16 8" />, p);
export const Calculator = (p: P) => svg(<><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M8 7h8M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15v3M8 18h4" /></>, p);
export const Bulb = (p: P) => svg(<><path d="M9 18h6M10 21h4" /><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z" /></>, p);
export const Chat = (p: P) => svg(<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" />, p);
export const Search = (p: P) => svg(<><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></>, p);
export const Trash = (p: P) => svg(<><path d="M4 7h16M10 11v6M14 11v6" /><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" /></>, p);
export const Close = (p: P) => svg(<path d="M6 6l12 12M18 6L6 18" />, p);
export const Chevron = (p: P) => svg(<path d="M9 6l6 6-6 6" />, p);
export const ChevronDown = (p: P) => svg(<path d="M6 9l6 6 6-6" />, p);
export const Check = (p: P) => svg(<path d="M5 12l5 5L20 7" />, p);
export const Copy = (p: P) => svg(<><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></>, p);
export const Refresh = (p: P) => svg(<><path d="M20 11a8 8 0 0 0-14.6-4.5L4 8" /><path d="M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5L20 16" /><path d="M20 20v-4h-4" /></>, p);
export const Shield = (p: P) => svg(<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />, p);
export const Sparkle = (p: P) => svg(<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />, p);
export const Sidebar = (p: P) => svg(<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></>, p);
export const Warn = (p: P) => svg(<><path d="M12 3l9 16H3z" /><path d="M12 10v4M12 17h.01" /></>, p);
export const Slash = (p: P) => svg(<path d="M15 4L9 20" />, p);
export const Globe = (p: P) => svg(<><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z" /></>, p);
export const Columns = (p: P) => svg(<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M12 4v16" /></>, p);
export const Gear = (p: P) => svg(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></>, p);
export const External = (p: P) => svg(<><path d="M14 4h6v6M20 4l-9 9" /><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></>, p);
export const Mail = (p: P) => svg(<><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" /></>, p);
export const Grid = (p: P) => svg(<><rect x="4" y="4" width="7" height="7" rx="1.5" /><rect x="13" y="4" width="7" height="7" rx="1.5" /><rect x="4" y="13" width="7" height="7" rx="1.5" /><rect x="13" y="13" width="7" height="7" rx="1.5" /></>, p);
export const Docs = (p: P) => svg(<><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M9 13h6M9 17h4" /></>, p);
export const Undo = (p: P) => svg(<><path d="M9 14L4 9l5-5" /><path d="M4 9h11a5 5 0 0 1 0 10h-3" /></>, p);

/** Vyum's own mark: a "V" whose right stroke ends in a spark, on the accent gradient. */
export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-label="Vyum" role="img">
      <defs>
        <linearGradient id="vyum-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6D7BF0" /><stop offset="1" stopColor="#3B47C4" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="30" height="30" rx="9" fill="url(#vyum-g)" />
      <path d="M9 10.5l7 12 5.2-9" fill="none" stroke="#fff" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="23.4" cy="9.6" r="2.3" fill="#FFD166" />
    </svg>
  );
}
export const Download = (p: P) => svg(<><path d="M12 4v11" /><path d="M7 10l5 5 5-5" /><path d="M5 19h14" /></>, p);
export const Chart = (p: P) => svg(<><path d="M4 20V4" /><path d="M4 20h16" /><rect x="7" y="11" width="3" height="6" rx=".5" /><rect x="12" y="7" width="3" height="10" rx=".5" /><rect x="17" y="13" width="3" height="4" rx=".5" /></>, p);
export const Pin = (p: P) => svg(<><path d="M9 4h6l-1 5 3 3v2H7v-2l3-3z" /><path d="M12 14v6" /></>, p);
export const Inbox = (p: P) => svg(<><path d="M3 13l3-8h12l3 8v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" /><path d="M3 13h5l1 2h6l1-2h5" /></>, p);
export const Send = (p: P) => svg(<path d="M4 12l16-8-6 16-3-7z" />, p);
export const Archive = (p: P) => svg(<><rect x="3" y="4" width="18" height="5" rx="1" /><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4" /></>, p);
export const Star = (p: P) => svg(<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />, p);
export const Pencil = (p: P) => svg(<><path d="M4 20h4L19 9l-4-4L4 16z" /><path d="M13.5 6.5l4 4" /></>, p);
export const MailOpen = (p: P) => svg(<><path d="M3 10l9-6 9 6v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" /><path d="M3 10l9 6 9-6" /></>, p);
export const Reply = (p: P) => svg(<><path d="M10 7L4 12l6 5" /><path d="M4 12h10a6 6 0 0 1 6 6" /></>, p);
export const Forward = (p: P) => svg(<><path d="M14 7l6 5-6 5" /><path d="M20 12H10a6 6 0 0 0-6 6" /></>, p);
