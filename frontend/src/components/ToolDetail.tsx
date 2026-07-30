import { useEffect, useMemo, useState, useCallback } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
} from "recharts";
import { api, fmtEur, fmtNum, fmtTok, type SpendResp, type TsBucket } from "../api";
import InfoTip from "./InfoTip";

const TOOL_META: Record<string, { label: string; color: string }> = {
  "claude-code": { label: "Claude Code", color: "#c8620a" },
  "codex":       { label: "Codex",        color: "#4a7cdc" },
  "antigravity": { label: "Antigravity",  color: "#7a9448" },
  "ollama":      { label: "Ollama",        color: "#c89020" },
  "copilot":     { label: "Copilot",       color: "#9a5638" },
};
const PIE_COLORS = ["#c8620a", "#4a7cdc", "#7a9448", "#c89020", "#9a5638", "#c02c18"];

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

export default function ToolDetail({ source, onBack }: { source: string; onBack: () => void }) {
  const meta = TOOL_META[source] ?? { label: source, color: "var(--accent)" };
  // Copilot CLI usage is now ingested from ~/.copilot/data.db, so Copilot is
  // treated like any other token-producing source. Flags kept false to
  // preserve the conditional layout used for the other tools.
  const noApiCost = false;
  const noModels  = false;

  const [period, setPeriod] = useState<"monthly" | "daily" | "yearly">("monthly");
  const [ref,    setRef]    = useState(MONTHS[0].val);
  const [spend,  setSpend]  = useState<SpendResp | null>(null);
  const [ts,     setTs]     = useState<TsBucket[]>([]);
  const [loading, setLoading] = useState(true);
  const [err,    setErr]    = useState<string | null>(null);

  const q = (period === "daily" ? "?period=daily"
    : period === "yearly" ? `?period=yearly&ref=${encodeURIComponent(ref)}`
    : `?period=monthly&ref=${encodeURIComponent(ref)}`) + `&source=${encodeURIComponent(source)}`;

  useEffect(() => {
    setLoading(true); setErr(null);
    Promise.all([api.spend(q), api.timeseries(q)])
      .then(([sp, t]) => { setSpend(sp); setTs(t); })
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

  const [modelSort, setModelSort] = useState<{ key: "cost_eur" | "turns" | "total_tokens"; dir: SortDir }>({ key: "cost_eur", dir: "desc" });
  const mkSort = useCallback(<K extends string>(cur: { key: K; dir: SortDir }, set: (v: { key: K; dir: SortDir }) => void, key: K) =>
    () => set(cur.key === key ? { key, dir: cur.dir === "desc" ? "asc" : "desc" } : { key, dir: "desc" }), []);
  const arrow = (cur: { key: string; dir: SortDir }, key: string) => cur.key === key ? (cur.dir === "desc" ? " ↓" : " ↑") : "";
  const sortBy = <T,>(arr: T[], key: keyof T, dir: SortDir) =>
    [...arr].sort((a, b) => dir === "desc" ? (b[key] as number) - (a[key] as number) : (a[key] as number) - (b[key] as number));

  const chartData = useMemo(() => ts.map((b) => ({ bucket: b.bucket, cost: b.cost_eur })), [ts]);

  const pieData = useMemo(() => {
    if (!spend) return [];
    return spend.by_model
      .filter((m) => m.cost_eur > 0)
      .sort((a, b) => b.cost_eur - a.cost_eur)
      .slice(0, 6);
  }, [spend]);

  if (loading) return <div className="content"><div className="empty">Loading…</div></div>;
  if (err)     return <div className="content"><div className="empty">Error: {err}</div></div>;
  if (!spend)  return null;

  return (
    <>
      <div className="topbar">
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <span className="back-link" onClick={onBack}>← Back</span>
          <div className="page-title" style={{ color: meta.color }}>{meta.label}</div>
        </div>
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
        </div>
      </div>

      <div className="content">
        {/* Summary cards */}
        <div className="cards">
          <div className="card">
            <div className="card-label">API-equivalent cost</div>
            <div className="card-value accent">{noApiCost ? "N/A" : fmtEur(spend.total_eur)}</div>
            <div className="card-sub">{fmtNum(spend.turns)} turns</div>
          </div>
          <div className="card">
            <div className="card-label">Subscription paid</div>
            <div className="card-value">{fmtEur(spend.sub_eur)}</div>
            <div className="card-sub">flat rate</div>
          </div>
          {!noApiCost && (
            <div className="card">
              <div className="card-label">Saved vs API</div>
              {(() => {
                const delta = spend.sub_eur > 0 ? spend.total_eur - spend.sub_eur : 0;
                return (
                  <>
                    <div className={`card-value ${delta > 0 ? "ok" : "danger"}`}>{fmtEur(Math.abs(delta))}</div>
                    <div className="card-sub">{delta > 0 ? "subscription wins" : "API cheaper"}</div>
                  </>
                );
              })()}
            </div>
          )}
          <div className="card">
            <div className="card-label">Models used</div>
            <div className="card-value">{spend.by_model.length}</div>
            <div className="card-sub">{spend.by_project.length} projects</div>
          </div>
        </div>

        {/* Area chart */}
        <div className="chart-box">
          <div className="section-title">
            {period === "daily" ? "Last 30 days" : period === "yearly" ? ref : "Calendar month"} — daily spend
          </div>
          {chartData.length === 0 ? (
            <div className="empty">No usage in this period.</div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={chartData} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
                <defs>
                  <linearGradient id={`grad-${source}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={meta.color} stopOpacity={0.25} />
                    <stop offset="95%" stopColor={meta.color} stopOpacity={0.03} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(110,65,20,.12)" vertical={false} />
                <XAxis dataKey="bucket" height={46} tick={{ fill: "#8c6643", fontSize: 11 }} fontFamily="IBM Plex Mono" tickFormatter={fmtBucket} minTickGap={20} />
                <YAxis tick={{ fill: "#8c6643", fontSize: 11 }} fontFamily="IBM Plex Mono" tickFormatter={(v) => `€${v}`} />
                <Tooltip
                  cursor={{ stroke: meta.color, strokeWidth: 1, strokeDasharray: "4 2" }}
                  content={({ active, payload, label }) => {
                    if (!active || !payload?.length) return null;
                    return (
                      <div style={{ background: "#ede3cc", border: "1px solid #d8c8a8", borderRadius: 6, fontFamily: "IBM Plex Mono", fontSize: 12, padding: "8px 10px" }}>
                        <div style={{ fontWeight: 600, marginBottom: 4 }}>{fmtBucketFull(String(label))}</div>
                        <div style={{ color: meta.color }}>{fmtEur(Number(payload[0]?.value ?? 0))}</div>
                      </div>
                    );
                  }}
                />
                <Area type="monotone" dataKey="cost" stroke={meta.color} strokeWidth={2} fill={`url(#grad-${source})`} dot={false} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: noModels ? "1fr" : "2fr 1fr", gap: 22, marginBottom: 22 }}>
          {/* Model breakdown */}
          {!noModels && (
            <div className="chart-box" style={{ margin: 0 }}>
              <div className="section-title">By model</div>
              {spend.by_model.length === 0 ? (
                <div className="empty" style={{ padding: "20px 0" }}>No model data.</div>
              ) : (
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Model</th>
                      {!noApiCost && (
                        <th className="right" onClick={mkSort(modelSort, setModelSort, "cost_eur")} style={{ cursor: "pointer" }}>
                          Cost{arrow(modelSort, "cost_eur")}
                          <InfoTip text="Estimated cost at public API rate (tokens × price per model)." />
                        </th>
                      )}
                      <th className="right" onClick={mkSort(modelSort, setModelSort, "turns")} style={{ cursor: "pointer" }}>Sessions{arrow(modelSort, "turns")}</th>
                      {!noApiCost && (
                        <th className="right" onClick={mkSort(modelSort, setModelSort, "total_tokens")} style={{ cursor: "pointer" }}>
                          Tokens{arrow(modelSort, "total_tokens")}
                          <InfoTip text="Total tokens (input + output + cache). Hover for split." />
                        </th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {sortBy(spend.by_model, modelSort.key, modelSort.dir).map((m) => (
                      <tr key={m.model_id}>
                        <td className="mono">{m.model_id}</td>
                        {!noApiCost && <td className="right mono">{fmtEur(m.cost_eur)}</td>}
                        <td className="right mono dim">{fmtNum(m.turns)}</td>
                        {!noApiCost && (
                          <td className="right mono" title={`in ${fmtNum(m.input_tokens)} · out ${fmtNum(m.output_tokens)}`}>
                            {fmtTok(m.total_tokens)}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {/* Pie chart — only for tools with real cost data */}
          {!noModels && !noApiCost && pieData.length > 0 && (
            <div className="chart-box" style={{ margin: 0, display: "flex", flexDirection: "column" }}>
              <div className="section-title">Cost share</div>
              <ResponsiveContainer width="100%" height={220}>
                <PieChart>
                  <Pie data={pieData} dataKey="cost_eur" nameKey="model_id" cx="50%" cy="50%" outerRadius={80} innerRadius={40}>
                    {pieData.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                  </Pie>
                  <Tooltip formatter={(v) => fmtEur(Number(v ?? 0))} contentStyle={{ fontFamily: "IBM Plex Mono", fontSize: 11, background: "#ede3cc", border: "1px solid #d8c8a8", borderRadius: 6 }} />
                  <Legend wrapperStyle={{ fontFamily: "IBM Plex Mono", fontSize: 10 }} formatter={(v) => v} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        {/* Project breakdown */}
        {spend.by_project.length > 0 && (
          <div className="chart-box">
            <div className="section-title">By project / workspace</div>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Project</th>
                  <th className="right">Cost</th>
                  <th className="right">Turns</th>
                </tr>
              </thead>
              <tbody>
                {spend.by_project.slice(0, 25).map((p) => (
                  <tr key={p.project}>
                    <td className="mono dim" style={{ maxWidth: 480, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {p.project || "(no path)"}
                    </td>
                    <td className="right mono">{fmtEur(p.cost_eur)}</td>
                    <td className="right mono dim">{fmtNum(p.turns)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {source === "antigravity" && (
          <div className="chart-box">
            <div className="section-title">Note</div>
            <p style={{ fontSize: ".84rem", color: "var(--text-dim)", lineHeight: 1.6 }}>
              Token counts are estimated from protobuf metadata in per-conversation DBs. Input tokens reflect the largest context window seen per session; output tokens are summed per AI response step. API cost uses flash-tier Gemini pricing as an approximation.
            </p>
          </div>
        )}
        {source === "copilot" && (
          <div className="chart-box">
            <div className="section-title">Note</div>
            <p style={{ fontSize: ".84rem", color: "var(--text-dim)", lineHeight: 1.6 }}>
              API cost here reflects <strong>Copilot CLI</strong> usage only — per-session token
              totals read from <code>~/.copilot/data.db</code>. In-editor Copilot usage is not
              exposed locally and is not counted. The CLI reports <code>model = &quot;auto&quot;</code>,
              so tokens are priced at a representative rate (<code>copilot-auto</code>, editable in
              Prices &amp; settings). Only recent sessions are retained by the CLI, so older usage
              is not recoverable. Subscription costs come from the monthly bills seeded in
              Prices &amp; settings.
            </p>
          </div>
        )}
      </div>
    </>
  );
}
