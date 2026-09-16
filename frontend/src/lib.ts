import type { Period, TokenSplit } from "./api";

/* ── Harnesses ──────────────────────────────────────────────────────────────
   The only entities that carry a colour (see DESIGN.md). Codex is the neutral
   on purpose; every mark is direct-labelled, so no chart relies on colour. */
export const HARNESS: Record<string, { label: string; fill: string; text: string; note: string }> = {
  "claude-code": { label: "Claude Code", fill: "var(--h-claude)",      text: "var(--h-claude-text)",      note: "Claude Max" },
  codex:         { label: "Codex",       fill: "var(--h-codex)",       text: "var(--h-codex-text)",       note: "ChatGPT" },
  ollama:        { label: "Ollama",      fill: "var(--h-ollama)",      text: "var(--h-ollama-text)",      note: "local & cloud" },
  antigravity:   { label: "Antigravity", fill: "var(--h-antigravity)", text: "var(--h-antigravity-text)", note: "Google" },
  "gemini-cli":  { label: "Gemini CLI",  fill: "var(--h-gemini)",      text: "var(--h-gemini-text)",      note: "Google" },
  copilot:       { label: "Copilot",     fill: "var(--h-copilot)",     text: "var(--h-copilot-text)",     note: "GitHub" },
};
export const harness = (s: string) =>
  HARNESS[s] ?? { label: s, fill: "var(--ink-3)", text: "var(--ink-2)", note: "" };

/* ── Numbers ──────────────────────────────────────────────────────────────── */
export const fmtTok = (n: number) =>
  n >= 1e9 ? `${(n / 1e9).toFixed(2)}B`
  : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M`
  : n >= 1e3 ? `${(n / 1e3).toFixed(0)}K`
  : String(n);

export const fmtEur = (n: number) =>
  `€${n.toLocaleString("fr-FR", { minimumFractionDigits: n < 100 ? 2 : 0, maximumFractionDigits: n < 100 ? 2 : 0 })}`;

export const fmtInt = (n: number) => n.toLocaleString("fr-FR");

export const pct = (part: number, whole: number) => (whole > 0 ? (part / whole) * 100 : 0);

/** Growth against the same-length period before, or null when there is nothing
 *  to compare with (a first week, or a period that was empty). */
export const change = (now: number, before?: number | null) =>
  before && before > 0 ? ((now - before) / before) * 100 : null;

/* ── The token split, in the order it is always shown ─────────────────────── */
export const SPLIT: { key: keyof TokenSplit; label: string; fill: string; hint: string }[] = [
  { key: "cache_read",       label: "Cache read",  fill: "var(--ramp-1)", hint: "context replayed from cache — cheap, and most of the volume" },
  { key: "input_tokens",     label: "Input",       fill: "var(--ramp-2)", hint: "new prompt tokens sent" },
  { key: "output_tokens",    label: "Output",      fill: "var(--ramp-5)", hint: "what the model wrote back" },
  { key: "cache_write",      label: "Cache write", fill: "var(--ramp-3)", hint: "context stored for reuse" },
  { key: "reasoning_tokens", label: "Reasoning",   fill: "var(--ramp-4)", hint: "thinking tokens, when the tool reports them" },
];

/* ── Periods ──────────────────────────────────────────────────────────────── */
export const PERIODS: { id: Period; short: string; noun: string }[] = [
  { id: "weekly",  short: "Week",  noun: "this week" },
  { id: "monthly", short: "Month", noun: "this month" },
  { id: "yearly",  short: "Year",  noun: "this year" },
];

const MONTHS = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];

/** The ref string for a period, stepped by `delta` periods from `ref`. */
export function stepRef(period: Period, ref: string | null, delta: number, now = new Date()): string {
  const base = refToDate(period, ref, now);
  if (period === "weekly") {
    base.setDate(base.getDate() + delta * 7);
    return iso(base);
  }
  if (period === "monthly") {
    base.setMonth(base.getMonth() + delta);
    return `${base.getFullYear()}-${String(base.getMonth() + 1).padStart(2, "0")}`;
  }
  return String(base.getFullYear() + delta);
}

function refToDate(period: Period, ref: string | null, now: Date): Date {
  if (!ref) return new Date(now);
  if (period === "weekly") return new Date(`${ref}T12:00:00`);
  if (period === "monthly") { const [y, m] = ref.split("-").map(Number); return new Date(y, m - 1, 15); }
  return new Date(Number(ref), 6, 1);
}

const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** "This week" / "Week of 8 Sep" / "September 2026" / "2026", from the window
 *  the API actually used — never from the browser's own idea of the date. */
export function periodLabel(period: Period, fromLocal: string, isCurrent: boolean) {
  const from = new Date(fromLocal);
  if (period === "weekly") {
    if (isCurrent) return "This week";
    const to = new Date(from); to.setDate(to.getDate() + 6);
    return `${from.getDate()} ${MONTHS[from.getMonth()].slice(0, 3)} – ${to.getDate()} ${MONTHS[to.getMonth()].slice(0, 3)}`;
  }
  if (period === "monthly") return isCurrent ? "This month" : `${MONTHS[from.getMonth()]} ${from.getFullYear()}`;
  return isCurrent ? "This year" : String(from.getFullYear());
}

export function windowLabel(period: Period, fromLocal: string, toLocal: string) {
  const from = new Date(fromLocal), to = new Date(toLocal);
  to.setDate(to.getDate() - 1);
  const d = (x: Date) => `${x.getDate()} ${MONTHS[x.getMonth()].slice(0, 3)}`;
  if (period === "yearly") return `${from.getFullYear()}`;
  return `${d(from)} → ${d(to)}`;
}

/** A bucket key ("2026-09-15" or "2026-09") as a short axis label. */
export const bucketLabel = (b: string) => {
  const parts = b.split("-");
  if (parts.length === 2) return MONTHS[Number(parts[1]) - 1].slice(0, 3);
  const d = new Date(`${b}T12:00:00`);
  return `${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getDay()].slice(0, 2)} ${d.getDate()}`;
};

export const bucketFull = (b: string) => {
  const parts = b.split("-");
  if (parts.length === 2) return `${MONTHS[Number(parts[1]) - 1]} ${parts[0]}`;
  const d = new Date(`${b}T12:00:00`);
  return `${["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
};

/** "4 min ago" — a collector that reports every 5 minutes should never read older. */
export function ago(iso8601: string | null): { text: string; state: "live" | "quiet" | "stale" | "never" } {
  if (!iso8601) return { text: "never reported", state: "never" };
  const then = new Date(iso8601.endsWith("Z") ? iso8601 : `${iso8601}Z`).getTime();
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  const state = mins <= 15 ? "live" : mins <= 24 * 60 ? "quiet" : "stale";
  if (mins < 1) return { text: "just now", state };
  if (mins < 60) return { text: `${mins} min ago`, state };
  const hours = Math.round(mins / 60);
  if (hours < 24) return { text: `${hours} h ago`, state };
  return { text: `${Math.round(hours / 24)} d ago`, state };
}
