const BASE = import.meta.env.VITE_API_URL ?? "";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}
async function patch<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}
async function post<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { method: "POST" });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

// ── formatters ────────────────────────────────────────────────────────────────
export const fmtEur = (n: number) =>
  `€${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const fmtNum = (n: number) => n.toLocaleString("fr-FR");
export const fmtTok = (n: number) =>
  n >= 1_000_000_000 ? `${(n / 1_000_000_000).toFixed(1)}B`
  : n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M`
  : n >= 1_000 ? `${(n / 1_000).toFixed(0)}K`
  : String(n);

// ── types ─────────────────────────────────────────────────────────────────────
export interface ToolSummary {
  source: string;
  turns: number;
  cost_eur: number;
  sub_eur: number;
  savings_eur: number;   // API_cost - sub_cost  (positive = sub is cheaper)
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface Summary {
  period: string;
  from: string;
  to: string;
  total_cost_eur: number;
  total_sub_eur: number;
  total_savings_eur: number;
  tools: ToolSummary[];
}

export interface ModelRow {
  model_id: string;
  cost_eur: number;
  turns: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface ProjectRow {
  project: string;
  cost_eur: number;
  turns: number;
}

export interface SpendResp {
  source: string | null;
  from: string;
  to: string;
  total_usd: number;
  total_eur: number;
  sub_eur: number;
  turns: number;
  by_model: ModelRow[];
  by_project: ProjectRow[];
}

export interface TsBucket {
  bucket: string;
  cost_eur: number;
  turns: number;
  by_source: Record<string, number>;
  tokens_by_source: Record<string, number>;
  by_model: Record<string, number>;
  tokens_by_model: Record<string, number>;
}

export interface PriceRow {
  model_id: string;
  display_name: string;
  provider: string;
  prompt: number;
  completion: number;
  cache_read: number;
  cache_write: number;
  reasoning: number;
  source: string;
  updated_at: string | null;
}

export interface Settings {
  fx_rate: number;
  sub_claude_code_eur: number;
  sub_codex_eur: number;
  sub_antigravity_eur: number;
  sub_ollama_eur: number;
  sub_copilot_eur: number;
  /** the plan the subscription is booked to move to (a plan id, or ""), and the day (YYYY-MM-DD, or "") */
  plan_next: string;
  plan_next_on: string;
}

export interface ManualBill {
  year_month: string;
  amount_eur: number;
}

export interface TokenSplit {
  input_tokens: number;
  output_tokens: number;
  cache_read: number;
  cache_write: number;
  reasoning_tokens: number;
}

export interface Totals extends TokenSplit {
  turns: number;
  tokens: number;
  cost_eur: number;
  cost_usd: number;
  sessions: number;
  models: number;
  machines: number;
  sub_eur: number;
  sub_eur_month: number;
}

export interface GroupRow extends TokenSplit {
  turns: number;
  tokens: number;
  cost_eur: number;
  models: number;
}

export type SourceRow  = GroupRow & { source: string; sub_eur: number; sub_eur_month: number };
export type MachineRow = GroupRow & {
  machine: string;
  last_seen_at: string | null;
  registered: boolean;
  projects: { project: string; tokens: number; turns: number; cost_eur: number }[];
  sources: SourceSlice[];
};
export type ModelRowX  = GroupRow & { model_id: string; source: string };
export type SourceSlice = { source: string; tokens: number; turns: number; cost_eur: number };
export type ProjectRowX = GroupRow & { project: string; sources: SourceSlice[] };
export type OriginRow  = GroupRow & { origin: string };

export interface Bucket {
  bucket: string;
  tokens: number;
  turns: number;
  cost_eur: number;
  by_source: Record<string, number>;
  by_machine: Record<string, number>;
  by_model: Record<string, number>;
  cost_by_source: Record<string, number>;
  cost_by_machine: Record<string, number>;
  cost_by_model: Record<string, number>;
}

export interface Overview {
  period: Period;
  ref: string | null;
  machine: string | null;
  source: string | null;
  tz: string;
  from: string; to: string;
  from_local: string; to_local: string;
  totals: Totals;
  previous: (Totals & { from: string; to: string }) | null;
  by_source: SourceRow[];
  by_machine: MachineRow[];
  by_model: ModelRowX[];
  by_project: ProjectRowX[];
  by_origin: OriginRow[];
  series: Bucket[];
  /** The top models of the last 30 days, biggest first: who owns the model split's shades. */
  model_order: string[];
  busiest: Bucket | null;
}

export type Period = "daily" | "weekly" | "monthly" | "yearly";

export interface MachineInfo {
  name: string;
  labels: string;
  registered: boolean;
  revoked: boolean;
  last_seen_at: string | null;
  collector_version: string | null;
  turns: number;
  total_tokens: number;
}

export interface Config {
  prices_url: string;
  openclaw_spend_url: string;
  tz: string;
  hub_machine: string;
}

export interface Health {
  status: string;
  total_turns: number;
  last_ingested_at: string | null;
  by_source: Record<string, number>;
}

export interface QuotaDay {
  start: string; start_local: string; tokens: number;
  future: boolean; current: boolean; end_pct: number | null; pace_pct: number;
}
/** How far back the Quota screen browses, as the backend's PAST_WINDOWS. */
export const QUOTA_WEEKS_BACK = 8;

export interface QuotaPast {
  /** weeks before the live window: the index that opens it */
  back: number;
  start: string; reset: string; start_local: string;
  tokens: number; final_pct: number | null; budget: number | null;
  /** the plan its last reading was on; only same-plan weeks lend a budget or an average */
  plan: string | null; plan_label: string | null; same_plan: boolean;
  /** the plan a week with a switch started on */
  switched_from_label: string | null;
}
/** The open 5-hour window: it began with the first message, five hours before its reset. */
export interface FiveHourOpen {
  used_pct: number; reset: string; at: string; machine: string; start: string;
  elapsed_pct: number; minutes_left: number;
  /** when a reading first found it at 100% */
  capped_at: string | null;
  /** against an even five hours; `runs_out_at` is when this rate reaches 100%, before the reset */
  pace: { delta_pts: number; status: "ahead" | "behind" | "on"; projected_pct: number | null; runs_out_at: string | null };
  history: { at: string; pct: number }[];
  /** Claude Code tokens per ten minutes of the window, from its start (30) */
  slots: number[];
  /** the budget is tokens by the last reading ÷ the points it read, as the week's */
  tokens: { used: number; budget: number | null; left: number | null };
}
export interface FiveHourWindow {
  start: string; reset: string; final_pct: number; tokens: number; capped_at: string | null;
  /** the open one */
  current: boolean; last_read: string;
}
export interface Quota {
  now: string;
  tz: string;
  /** false until a first reading: the window is then just the last seven days */
  anchored: boolean;
  /** the subscription tier of the newest reading that names one, e.g. default_claude_max_20x */
  plan: string | null;
  plan_label: string | null;
  /** the last switch between two plans inside this window */
  plan_change: { from: string; to: string; from_label: string; to_label: string; last_at: string; at: string } | null;
  /** the plan the subscription is booked to move to, until a reading names it; `at` is its day's local midnight */
  plan_next: { plan: string; plan_label: string; on: string; at: string } | null;
  /** the window in view, `back` weeks before the live one (0), read to `end`: now, or its reset once passed */
  window: { back: number; start: string; reset: string; end: string; start_local: string; reset_local: string; elapsed_pct: number; days_left: number | null };
  /** `stale`: Claude Code spent `tokens_since` after it and nothing read the quota since (#67) */
  reading: { used_pct: number; at: string; machine: string; tokens_since: number; stale: boolean } | null;
  used_pct: number | null;
  pace: { delta_pts: number; status: "ahead" | "behind" | "on"; projected_pct: number | null } | null;
  tokens: {
    used: number; per_day_so_far: number; avg_day_past: number | null;
    budget: number | null; budget_from: string | null;
    /** the line's estimate from tokens, one part per plan: from a known percentage (`pct` after
     * `tokens` of the window), on that plan's budget, until `to` (null: still running) */
    estimate: { from: string; to: string | null; pct: number; tokens: number; budget: number; plan_label: string | null }[];
    left: { tokens: number; per_day: number | null } | null;
  };
  /** the 5-hour window: the open one, if any, and the last windows newest first;
   * null for a past week, or before any reading */
  five_hour: { open: FiveHourOpen | null; windows: FiveHourWindow[] } | null;
  history: { at: string; pct: number; plan_label: string | null }[];
  days: QuotaDay[];
  /** Claude Code tokens per hour of the window, from its start (168) */
  hourly: number[];
  past: QuotaPast[];
  avg_final_pct: number | null;
  /** tokens by local weekday (0 = Monday) × hour, the last 28 days */
  hours: number[][];
  by_project: { key: string; tokens: number }[];
  by_machine: { key: string; tokens: number }[];
  by_model: { key: string; tokens: number }[];
}

/** A provider's monthly credits, as its own API reports them (#73). `used` is null when the
 *  count reset since the last reading. */
export type Credits = {
  now: string;
  tz: string;
  provider: string;
  label: string;
  reading: { at: string; machine: string; stale: boolean } | null;
  plan?: string | null;
  plan_label?: string | null;
  window?: { start: string; reset: string; elapsed_pct: number; days_left: number; reset_passed: boolean };
  used?: number | null;
  limit?: number;
  left?: number | null;
  used_pct?: number | null;
  /** an even month; the projection carries the month's own rate (credits a day) to the reset */
  pace?: {
    status: "ahead" | "on" | "behind";
    delta_pts: number;
    per_day: number | null;
    projected_used: number | null;
    projected_pct: number | null;
    runs_out_at: string | null;
  } | null;
  per_day_to_reset?: number | null;
  recent_per_day?: number;
  /** credits by UTC day, from the reset's day to the reset ahead */
  days?: { day: string; used: number; current: boolean; future: boolean }[];
};

export const api = {
  quota: (back = 0) => get<Quota>(`/api/quota${back ? `?back=${back}` : ""}`),
  credits: (provider = "elevenlabs") => get<Credits>(`/api/credits?provider=${encodeURIComponent(provider)}`),
  health: () => get<Health>("/api/health"),
  overview: (period: Period, ref?: string | null, machine?: string | null) =>
    get<Overview>(`/api/overview?period=${period}${ref ? `&ref=${encodeURIComponent(ref)}` : ""}` +
      `${machine ? `&machine=${encodeURIComponent(machine)}` : ""}`),
  machines: () => get<MachineInfo[]>("/api/machines"),
  config: () => get<Config>("/api/config"),
  summary: (q: string) => get<Summary>(`/api/summary${q}`),
  spend: (q: string) => get<SpendResp>(`/api/spend${q}`),
  timeseries: (q: string) => get<TsBucket[]>(`/api/spend/timeseries${q}`),
  prices: () => get<PriceRow[]>("/api/prices"),
  settings: () => get<Settings>("/api/settings"),
  patchSettings: (body: Partial<Settings>) => patch<Settings>("/api/settings", body),
  recompute: () => post<{ rows_updated: number }>("/api/recompute"),
  bills: (source: string) => get<ManualBill[]>(`/api/bills/${encodeURIComponent(source)}`),
  patchBill: (source: string, year_month: string, body: { amount_eur: number }) =>
    patch<ManualBill>(`/api/bills/${encodeURIComponent(source)}/${encodeURIComponent(year_month)}`, body),
};
