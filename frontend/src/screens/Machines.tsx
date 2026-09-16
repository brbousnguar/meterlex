import type { Overview } from "../api";
import Odometer from "../components/Odometer";
import { RankRows, Sparkline } from "../components/charts";
import { ago, fmtEur, fmtInt, fmtTok, harness, pct } from "../lib";

/** One meter per machine: its reading for the period, its share of the wall,
 *  the shape of its week, and whether its collector is still reporting. */
export default function Machines({ data }: { data: Overview }) {
  const total = data.totals.tokens;
  const rows = [...data.by_machine].sort((a, b) => b.tokens - a.tokens);
  const silent = rows.filter((m) => m.tokens === 0);
  const live = rows.filter((m) => m.tokens > 0);

  return (
    <>
      <section className="section" style={{ marginTop: 6 }}>
        <div className="section-head">
          <h2 className="section-title">Meters</h2>
          <div className="section-note">{live.length} of {rows.length} reporting this period</div>
        </div>
        <div className="meters">
          {live.map((m) => {
            const seen = ago(m.last_seen_at);
            return (
              <article className="meter" key={m.machine}>
                <div className="meter-head">
                  <span className="meter-name">{m.machine}</span>
                  <span className="meter-reading">{fmtTok(m.tokens)}</span>
                </div>
                <Odometer value={m.tokens} small digits={9} />
                <div className="rank-bar"><i style={{ width: `${Math.max(1.5, pct(m.tokens, total))}%`, background: "var(--ink-2)" }} /></div>
                <Sparkline series={data.series} machine={m.machine} />
                <div className="meter-foot">
                  <span className="live" data-state={seen.state}>{seen.text}</span>
                  <span>{pct(m.tokens, total).toFixed(0)}% of the period</span>
                  <span>{fmtInt(m.turns)} replies</span>
                  <span>{fmtEur(m.cost_eur)} list</span>
                </div>
              </article>
            );
          })}
        </div>
      </section>

      {silent.length > 0 && (
        <section className="section">
          <div className="section-head">
            <h2 className="section-title">Silent this period</h2>
            <div className="section-note">registered, nothing received</div>
          </div>
          <RankRows rows={silent.map((m) => ({
            key: m.machine,
            name: <span className="mono">{m.machine}</span>,
            value: ago(m.last_seen_at).text,
            sub: m.last_seen_at ? "collector installed" : "collector never ran here",
            share: 0,
            fill: "var(--ink-3)",
          }))} />
        </section>
      )}

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">Who ran what</h2>
          <div className="section-note">harness share of the period</div>
        </div>
        <RankRows rows={data.by_source.filter((s) => s.tokens > 0).map((s) => ({
          key: s.source,
          name: <span>{harness(s.source).label}</span>,
          value: fmtTok(s.tokens),
          sub: `${s.models} model${s.models === 1 ? "" : "s"}`,
          share: pct(s.tokens, total),
          fill: harness(s.source).fill,
        }))} />
      </section>
    </>
  );
}
