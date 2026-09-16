import type { Overview } from "../api";
import { RankRows, SplitBar } from "../components/charts";
import { byMeasure, fmtEur, fmtInt, fmtMeasure, fmtTok, harness, measure, pct, type Unit } from "../lib";

/** Per harness: what it burned, and whether its subscription is paying off.
 *  "Earns" is list price ÷ fee — above 1× the subscription is cheaper than the
 *  API would have been, below it you are paying for a seat you barely use. */
export default function Harnesses({ data, unit }: { data: Overview; unit: Unit }) {
  const rows = byMeasure(unit, data.by_source);
  const total = measure(unit, data.totals);

  return (
    <>
      {rows.map((s) => {
        const meta = harness(s.source);
        const ratio = s.sub_eur > 0 ? s.cost_eur / s.sub_eur : null;
        return (
          <section className="section" key={s.source} style={{ marginTop: 18 }}>
            <div className="section-head" style={{ borderBottomColor: s.tokens > 0 ? meta.fill : "var(--rule)" }}>
              <h2 className="section-title" style={{ color: s.tokens > 0 ? meta.text : "var(--ink-3)" }}>
                {meta.label}
              </h2>
              <div className="section-note">{meta.note}</div>
            </div>

            {s.tokens === 0 ? (
              <div className="meter-foot" style={{ padding: "10px 0" }}>
                <span>nothing this period</span>
                {s.sub_eur > 0 && <span className="stat-value over" style={{ fontSize: "1rem" }}>{fmtEur(s.sub_eur)} paid anyway</span>}
              </div>
            ) : (
              <>
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
              </>
            )}
          </section>
        );
      })}

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
          fill: "var(--ink-3)",
        }))} />
      </section>
    </>
  );
}
