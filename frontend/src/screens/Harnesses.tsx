import type { Overview } from "../api";
import { RankRows, SplitBar } from "../components/charts";
import { byMeasure, fmtEur, fmtInt, fmtMeasure, fmtTok, harness, measure, pct, PLAIN, type Unit } from "../lib";

/** Per harness: what it burned, and whether its subscription is paying off.
 *  "Earns" is list price ÷ fee — above 1× the subscription is cheaper than the
 *  API would have been, below it you are paying for a seat you barely use. */
export default function Harnesses({ data, unit }: { data: Overview; unit: Unit }) {
  const rows = byMeasure(unit, data.by_source);
  const total = measure(unit, data.totals);
  const idle = rows.filter((s) => s.tokens === 0);
  const idleFees = idle.reduce((a, s) => a + s.sub_eur, 0);

  return (
    <>
      {rows.filter((s) => s.tokens > 0).map((s) => {
        const meta = harness(s.source);
        const ratio = s.sub_eur > 0 ? s.cost_eur / s.sub_eur : null;
        return (
          <section className="section" key={s.source} style={{ borderTop: `4px solid ${meta.fill}` }}>
            <div className="section-head">
              <h2 className="section-title" style={{ color: meta.text }}>{meta.label}</h2>
              <div className="section-note">{meta.note}</div>
            </div>

            <div className="rail">
              <div className="stat">
                <div className="stat-label">Tokens</div>
                <div className="stat-value">{fmtTok(s.tokens)}</div>
                <div className="stat-sub">{pct(measure(unit, s), total).toFixed(0)}% of the period</div>
              </div>
              <div className="stat">
                <div className="stat-label">Replies</div>
                <div className="stat-value">{fmtInt(s.turns)}</div>
                <div className="stat-sub">{s.models} model{s.models === 1 ? "" : "s"}</div>
              </div>
              <div className="stat">
                <div className="stat-label">List price</div>
                <div className="stat-value">{fmtEur(s.cost_eur)}</div>
                <div className="stat-sub">{fmtEur(s.sub_eur)} paid</div>
              </div>
              <div className="stat">
                <div className="stat-label">Earns</div>
                <div className={`stat-value ${ratio === null ? "" : ratio >= 1 ? "under" : "over"}`}>
                  {ratio === null ? "—" : `${ratio.toFixed(1)}×`}
                </div>
                <div className="stat-sub">{ratio === null ? "no fee" : ratio >= 1 ? "worth the fee" : "under-used"}</div>
              </div>
            </div>
            <div style={{ marginTop: 14 }}>
              <SplitBar split={s} total={s.tokens} />
            </div>
          </section>
        );
      })}

      {/* A harness that burned nothing is one row, not an empty card of its own. */}
      {idle.length > 0 && (
        <section className="section">
          <div className="section-head">
            <h2 className="section-title">Idle this period</h2>
            <div className="section-note">
              {idleFees > 0 ? `${fmtEur(idleFees)} paid for seats nothing used` : "nothing recorded, no fee running"}
            </div>
          </div>
          <RankRows rows={idle.map((s) => ({
            key: s.source,
            name: <span>{harness(s.source).label}</span>,
            value: s.sub_eur > 0 ? fmtEur(s.sub_eur) : "—",
            sub: s.sub_eur > 0 ? `paid anyway · ${harness(s.source).note}` : harness(s.source).note,
            share: 0,
            fill: harness(s.source).fill,
            tone: s.sub_eur > 0 ? "over" as const : undefined,
          }))} />
        </section>
      )}

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">How it was run</h2>
          <div className="section-note">interactive, scheduled or a subagent</div>
        </div>
        <RankRows rows={byMeasure(unit, data.by_origin).map((o) => ({
          key: o.origin,
          name: <span>{o.origin}</span>,
          value: fmtMeasure(unit, o),
          sub: `${fmtInt(o.turns)} replies`,
          share: pct(measure(unit, o), total),
          fill: PLAIN,
        }))} />
      </section>
    </>
  );
}
