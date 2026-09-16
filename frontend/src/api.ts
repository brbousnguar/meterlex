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
  projects: { project: string; tokens: number; turns: number }[];
  sources: { source: string; tokens: number; turns: number }[];
};
export type ModelRowX  = GroupRow & { model_id: string; source: string };
export type ProjectRowX = GroupRow & { project: string };
export type OriginRow  = GroupRow & { origin: string };

export interface Bucket {
  bucket: string;
  tokens: number;
  turns: number;
  cost_eur: number;
  by_source: Record<string, number>;
  by_machine: Record<string, number>;
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
  busiest: Bucket | null;
}

export type Period = "weekly" | "monthly" | "yearly";

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
  tz: string;
  hub_machine: string;
}

export interface Health {
  status: string;
  total_turns: number;
  last_ingested_at: string | null;
  by_source: Record<string, number>;
}

export const api = {
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
