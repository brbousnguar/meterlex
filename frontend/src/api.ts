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

export interface Health {
  status: string;
  total_turns: number;
  last_ingested_at: string | null;
  by_source: Record<string, number>;
}

export const api = {
  health: () => get<Health>("/api/health"),
  summary: (q: string) => get<Summary>(`/api/summary${q}`),
  spend: (q: string) => get<SpendResp>(`/api/spend${q}`),
  timeseries: (q: string) => get<TsBucket[]>(`/api/spend/timeseries${q}`),
  prices: () => get<PriceRow[]>("/api/prices"),
  patchPrice: (id: string, body: Partial<PriceRow>) =>
    patch<{ model_id: string }>(`/api/prices/${encodeURIComponent(id)}`, body),
  settings: () => get<Settings>("/api/settings"),
  patchSettings: (body: Partial<Settings>) => patch<Settings>("/api/settings", body),
  scan: () => post<unknown>("/api/scan"),
  recompute: () => post<{ rows_updated: number }>("/api/recompute"),
  bills: (source: string) => get<ManualBill[]>(`/api/bills/${encodeURIComponent(source)}`),
  patchBill: (source: string, year_month: string, body: { amount_eur: number }) =>
    patch<ManualBill>(`/api/bills/${encodeURIComponent(source)}/${encodeURIComponent(year_month)}`, body),
};
