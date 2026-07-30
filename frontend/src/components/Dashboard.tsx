import { useEffect, useMemo, useState, useCallback } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { api, fmtEur, fmtNum, fmtTok, type Summary, type SpendResp, type TsBucket } from "../api";
import InfoTip from "./InfoTip";

const TOOL_META: Record<string, { label: string; color: string }> = {
  "claude-code": { label: "Claude Code", color: "#c8620a" },
  "codex":       { label: "Codex",        color: "#4a7cdc" },
  "antigravity": { label: "Antigravity",  color: "#7a9448" },
  "ollama":      { label: "Ollama",        color: "#c89020" },
  "copilot":     { label: "Copilot",       color: "#9a5638" },
};
const COLORS = ["#c8620a", "#4a7cdc", "#7a9448", "#c89020", "#9a5638", "#c02c18"];

const MONTHS = (() => {
  const out: { val: string; label: string }[] = [];
  const now = new Date();
  for (let i = 0; i < 12; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    out.push({ val: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`, label: d.toLocaleDateString("fr-FR", { month: "short", year: "numeric" }) });
  }
  return out;
})();
const YEARS = (() => { const c = new Date().getFullYear(); return Array.from({ length: 5 }, (_, i) => String(c - i)); })();

type SortDir = "desc" | "asc";

export default function Dashboard({ onSelectTool, onReload }: { onSelectTool: (s: string) => void; onReload: () => void }) {
  const [period, setPeriod] = useState<"monthly" | "daily" | "yearly">("monthly");
  const [ref,    setRef]    = useState(MONTHS[0].val);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [spend,   setSpend]   = useState<SpendResp | null>(null);
  const [ts,      setTs]      = useState<TsBucket[]>([]);
  const [loading, setLoading] = useState(true);
  const [err,     setErr]     = useState<string | null>(null);

  const q = period === "daily" ? "?period=daily"
    : period === "yearly" ? `?period=yearly&ref=${encodeURIComponent(ref)}`
    : `?period=monthly&ref=${encodeURIComponent(ref)}`;

  useEffect(() => {
    setLoading(true); setErr(null);
    Promise.all([api.summary(q), api.spend(q), api.timeseries(q)])
      .then(([s, sp, t]) => { setSummary(s); setSpend(sp); setTs(t); })
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [q]);

  const fmtBucket = (b: string) => {
    if (period === "yearly") {
      const d = new Date(b + "-01T00:00:00Z");
      return isNaN(+d) ? b : d.toLocaleDateString("fr-FR", { month: "short", timeZone: "UTC" });
    }
    const d = new Date(b + "T00:00:00Z");
    return isNaN(+d) ? b : `${String(d.getUTCDate()).padStart(2, "0")}/${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
  };
  const fmtBucketFull = (b: string) => {
    if (period === "yearly") {
      const d = new Date(b + "-01T00:00:00Z");
      return isNaN(+d) ? b : d.toLocaleDateString("fr-FR", { month: "long", year: "numeric", timeZone: "UTC" });
    }
    const d = new Date(b + "T00:00:00Z");
    return isNaN(+d) ? b : d.toLocaleDateString("fr-FR", { weekday: "short", day: "2-digit", month: "2-digit", timeZone: "UTC" });
  };

  // sortable model table
  const [modelSort, setModelSort] = useState<{ key: "cost_eur" | "turns" | "total_tokens"; dir: SortDir }>({ key: "cost_eur", dir: "desc" });
  const mkSort = useCallback(<K extends string>(cur: { key: K; dir: SortDir }, set: (v: { key: K; dir: SortDir }) => void, key: K) =>
    () => set(cur.key === key ? { key, dir: cur.dir === "desc" ? "asc" : "desc" } : { key, dir: "desc" }), []);
  const arrow = (cur: { key: string; dir: SortDir }, key: string) => cur.key === key ? (cur.dir === "desc" ? " ↓" : " ↑") : "";
  const sortBy = <T,>(arr: T[], key: keyof T, dir: SortDir) =>
    [...arr].sort((a, b) => dir === "desc" ? (b[key] as number) - (a[key] as number) : (a[key] as number) - (b[key] as number));

  const seriesKeys = useMemo(() => {
    const costs: Record<string, number> = {};
    ts.forEach((b) => Object.entries(b.by_source).forEach(([k, v]) => { costs[k] = (costs[k] || 0) + v; }));
    return Object.keys(costs).sort((a, b) => (costs[b] || 0) - (costs[a] || 0));
  }, [ts]);
  // Fill missing source keys with 0 so recharts stacked bars don't skip rendering
  const chartData = useMemo(() => ts.map((b) => {
    const pt: Record<string, number | string> = { bucket: b.bucket };
    seriesKeys.forEach((k) => { pt[k] = b.by_source[k] ?? 0; });
    return pt;
  }), [ts, seriesKeys]);

  if (loading) return <div className="content"><div className="empty">Loading…</div></div>;
  if (err)     return <div className="content"><div className="empty">Error: {err}</div></div>;
  if (!summary || !spend) return null;

  const delta = summary.total_savings_eur; // positive = sub is cheaper than API

  return (
    <>
      <div className="topbar">
        <div className="page-title">Spend dashboard</div>
        <div className="topbar-actions">
          <select value={period} onChange={(e) => {
            const p = e.target.value as typeof period;
            setPeriod(p);
            if (p === "yearly") setRef(YEARS[0]);
            else if (p === "monthly") setRef(MONTHS[0].val);
          }}>
            <option value="monthly">Calendar month</option>
            <option value="yearly">Full year</option>
            <option value="daily">Last 30 days</option>
          </select>
          {period === "monthly" && (
            <select value={ref} onChange={(e) => setRef(e.target.value)}>
              {MONTHS.map((m) => <option key={m.val} value={m.val}>{m.label}</option>)}
            </select>
          )}
          {period === "yearly" && (
            <select value={ref} onChange={(e) => setRef(e.target.value)}>
              {YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
          )}
          <button className="btn ghost sm" onClick={() => api.scan().then(onReload)}>↻ Rescan</button>
        </div>
      </div>

      <div className="content">
        {/* Summary cards */}
        <div className="cards">
          <div className="card">
            <div className="card-label">API-equivalent cost</div>
            <div className="card-value accent">{fmtEur(summary.total_cost_eur)}</div>
            <div className="card-sub">{fmtNum(spend.turns)} turns total</div>
          </div>
          <div className="card">
            <div className="card-label">Subscriptions paid</div>
            <div className="card-value">{fmtEur(summary.total_sub_eur)}</div>
            <div className="card-sub">flat rate across {summary.tools.length} tools</div>
          </div>
          <div className="card">
            <div className="card-label">Saved vs API rates</div>
            <div className={`card-value ${delta > 0 ? "ok" : "danger"}`}>{fmtEur(Math.abs(delta))}</div>
            <div className="card-sub">{delta > 0 ? "subscriptions win" : "API would be cheaper"}</div>
          </div>
        </div>

        {/* Per-tool cards */}
        <div className="tool-cards">
          {summary.tools.map((tool) => {
            const meta = TOOL_META[tool.source] || { label: tool.source, color: "var(--text-dim)" };
            const saving = tool.savings_eur; // positive = sub is cheaper
            const noTokens = tool.source === "copilot";
            return (
              <div key={tool.source} className="tool-card" onClick={() => onSelectTool(tool.source)}
                style={{ borderTop: `3px solid ${meta.color}` }}>
                <div className="tool-card-header">
                  <span className="tool-card-name" style={{ color: meta.color }}>{meta.label}</span>
                  <span className="badge" style={{ marginLeft: "auto" }}>{fmtNum(tool.turns)} turns</span>
                </div>
                <div className="tool-card-row">
                  <span className="tool-card-label">API cost</span>
                  <span className="tool-card-value">{noTokens ? "N/A" : fmtEur(tool.cost_eur)}</span>
                </div>
                <div className="tool-card-row">
                  <span className="tool-card-label">Subscription</span>
                  <span className="tool-card-value">{fmtEur(tool.sub_eur)}</span>
                </div>
                {!noTokens && (
                  <div className="tool-card-row">
                    <span className="tool-card-label">Tokens</span>
                    <span className="tool-card-value">{fmtTok(tool.total_tokens)}</span>
                  </div>
                )}
                {!noTokens && (
                  <div className="tool-card-saving" style={{ color: saving > 0 ? "var(--accent2)" : "var(--danger)" }}>
                    {saving > 0 ? `sub saves ${fmtEur(saving)}` : `API ${fmtEur(Math.abs(saving))} cheaper`}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Stacked bar chart */}
        <div className="chart-box">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
            <div className="section-title" style={{ margin: 0 }}>
              {period === "daily" ? "Last 30 days" : period === "yearly" ? ref : "Calendar month"} — spend by tool
            </div>
          </div>
          {chartData.length === 0 ? (
            <div className="empty">No usage in this period.</div>
          ) : (
            <>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={chartData} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(110,65,20,.12)" vertical={false} />
                <XAxis dataKey="bucket" height={46} tick={{ fill: "#8c6643", fontSize: 11 }} fontFamily="IBM Plex Mono" tickFormatter={fmtBucket} minTickGap={20} />
                <YAxis tick={{ fill: "#8c6643", fontSize: 11 }} fontFamily="IBM Plex Mono" tickFormatter={(v) => `€${v}`} />
                <Tooltip
                  cursor={{ fill: "rgba(110,65,20,.06)" }}
                  content={({ active, payload, label }) => {
                    if (!active || !payload?.length) return null;
                    const bucket = ts.find((b) => b.bucket === String(label));
                    const items = payload
                      .map((p) => ({ name: String(p.name), value: Number(p.value), color: p.fill as string }))
                      .filter((p) => p.value > 0)
                      .sort((a, b) => b.value - a.value);
                    const total = items.reduce((s, p) => s + p.value, 0);
                    return (
                      <div style={{ background: "#ede3cc", border: "1px solid #d8c8a8", borderRadius: 6, fontFamily: "IBM Plex Mono", fontSize: 12, padding: "8px 10px", minWidth: 200 }}>
                        <div style={{ fontWeight: 600, marginBottom: 6 }}>{fmtBucketFull(String(label))}</div>
                        {items.map((p) => {
                          const tok = bucket?.tokens_by_source[p.name] ?? 0;
                          return (
                            <div key={p.name} style={{ marginBottom: 4 }}>
                              <div style={{ display: "flex", justifyContent: "space-between", gap: 14, color: "#2d1a08" }}>
                                <span><span style={{ color: p.color, marginRight: 6 }}>●</span>{TOOL_META[p.name]?.label ?? p.name}</span>
                                <span>{fmtEur(p.value)}</span>
                              </div>
                              {tok > 0 && (
                                <div style={{ paddingLeft: 18, color: "var(--text-dim)", fontSize: 11 }}>
                                  {fmtTok(tok)} tokens
                                </div>
                              )}
                            </div>
                          );
                        })}
                        <div style={{ borderTop: "1px solid #d8c8a8", marginTop: 4, paddingTop: 4, display: "flex", justifyContent: "space-between", fontWeight: 600 }}>
                          <span>total</span><span>{fmtEur(total)}</span>
                        </div>
                      </div>
                    );
                  }}
                />
                {seriesKeys.map((k, i) => (
                  <Bar key={k} dataKey={k} stackId="a" isAnimationActive={false} fill={TOOL_META[k]?.color ?? COLORS[i % COLORS.length]} radius={i === seriesKeys.length - 1 ? [3, 3, 0, 0] : undefined} />
                ))}
              </BarChart>
            </ResponsiveContainer>
            <div style={{ display: "flex", gap: 14, justifyContent: "center", marginTop: 6, fontFamily: "IBM Plex Mono", fontSize: 11, color: "var(--text-dim)" }}>
              {seriesKeys.map((k) => (
                <span key={k}><span style={{ color: TOOL_META[k]?.color ?? "var(--text-dim)", marginRight: 4 }}>■</span>{TOOL_META[k]?.label ?? k}</span>
              ))}
            </div>
            </>
          )}
        </div>

        {/* Model breakdown */}
        <div className="chart-box">
          <div className="section-title">By model</div>
          <table className="tbl">
            <thead>
              <tr>
                <th>Model</th>
                <th className="right" onClick={mkSort(modelSort, setModelSort, "cost_eur")} style={{ cursor: "pointer" }}>
                  Cost{arrow(modelSort, "cost_eur")}
                  <InfoTip text="Estimated cost at each model's public API rate (tokens × price). What you'd pay without a subscription. Local/free models are €0." />
                </th>
                <th className="right" onClick={mkSort(modelSort, setModelSort, "turns")} style={{ cursor: "pointer" }}>Turns{arrow(modelSort, "turns")}</th>
                <th className="right" onClick={mkSort(modelSort, setModelSort, "total_tokens")} style={{ cursor: "pointer" }}>
                  Tokens{arrow(modelSort, "total_tokens")}
                  <InfoTip text="Total tokens processed (input + output + cache). Hover to see in/out split." />
                </th>
              </tr>
            </thead>
            <tbody>
              {sortBy(spend.by_model, modelSort.key, modelSort.dir).map((m) => (
                <tr key={m.model_id}>
                  <td className="mono">{m.model_id}</td>
                  <td className="right mono">{fmtEur(m.cost_eur)}</td>
                  <td className="right mono dim">{fmtNum(m.turns)}</td>
                  <td className="right mono" title={`in ${fmtNum(m.input_tokens)} · out ${fmtNum(m.output_tokens)}`}>
                    {fmtTok(m.total_tokens)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
