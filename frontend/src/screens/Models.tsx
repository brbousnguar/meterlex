import type { Overview } from "../api";
import { RankRows, SplitBar } from "../components/charts";
import { fmtEur, fmtInt, fmtTok, harness, pct } from "../lib";

/** Models are ranked, not painted: the dot carries the harness that ran them. */
export default function Models({ data }: { data: Overview }) {
  const total = data.totals.tokens;
  const top = data.by_model[0];

  return (
    <>
      {top && (
        <section className="section" style={{ marginTop: 6 }}>
          <div className="section-head">
            <h2 className="section-title">Most used model</h2>
            <div className="section-note">{data.totals.models} in the period</div>
          </div>
          <div className="reading-label mono" style={{ fontSize: ".8rem", textTransform: "none", letterSpacing: 0, color: "var(--ink)" }}>
            {top.model_id}
          </div>
          <div className="rail" style={{ marginTop: 10 }}>
            <div className="stat">
              <div className="stat-label">Tokens</div>
              <div className="stat-value">{fmtTok(top.tokens)}</div>
              <div className="stat-sub">{pct(top.tokens, total).toFixed(0)}% of everything</div>
            </div>
            <div className="stat">
              <div className="stat-label">Replies</div>
              <div className="stat-value">{fmtInt(top.turns)}</div>
              <div className="stat-sub">through {harness(top.source).label}</div>
            </div>
            <div className="stat">
              <div className="stat-label">List price</div>
              <div className="stat-value">{fmtEur(top.cost_eur)}</div>
              <div className="stat-sub">at the central rate card</div>
            </div>
            <div className="stat">
              <div className="stat-label">Per reply</div>
              <div className="stat-value">{fmtTok(Math.round(top.tokens / Math.max(1, top.turns)))}</div>
              <div className="stat-sub">tokens, on average</div>
            </div>
          </div>
          <div style={{ marginTop: 14 }}>
            <SplitBar split={top} total={top.tokens} />
          </div>
        </section>
      )}

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">Every model this period</h2>
          <div className="section-note">by tokens</div>
        </div>
        <RankRows rows={data.by_model.map((m) => ({
          key: m.model_id,
          name: <span className="mono">{m.model_id}</span>,
          value: fmtTok(m.tokens),
          sub: `${fmtInt(m.turns)} replies · ${fmtEur(m.cost_eur)}`,
          share: pct(m.tokens, data.by_model[0]?.tokens || 1),
          fill: harness(m.source).fill,
        }))} />
      </section>
    </>
  );
}
