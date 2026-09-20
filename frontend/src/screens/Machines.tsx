import type { Overview } from "../api";
import Odometer from "../components/Odometer";
import { MachineDays, MiniRank, RankRows } from "../components/charts";
import { ago, byMeasure, fmtEur, fmtInt, fmtMeasure, fmtTok, folderLabel, harness, measure, moneyDrums, pct, type Unit } from "../lib";

/** One meter per machine: its reading for the period, its share of the wall,
 *  how many tokens it burned each day, what it worked in, and whether its
 *  collector is still reporting. */
export default function Machines({ data, unit }: { data: Overview; unit: Unit }) {
  const total = measure(unit, data.totals);
  const rows = byMeasure(unit, data.by_machine);
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
            const folders = byMeasure(unit, m.projects);
            const sources = byMeasure(unit, m.sources);
            const topFolder = folders[0] ? measure(unit, folders[0]) : 1;
            return (
              <article className="meter" key={m.machine}>
                <div className="meter-head">
                  <span className="meter-name">{m.machine}</span>
                  <span className="meter-reading">{fmtMeasure(unit, m)}</span>
                </div>
                {unit === "money"
                  ? <Odometer value={Math.round(m.cost_eur)} small digits={moneyDrums(m.cost_eur)} />
                  : <Odometer value={m.tokens} small digits={9} />}
                <div className="rank-bar">
                  <i style={{ width: `${Math.max(1.5, pct(measure(unit, m), total))}%`, background: "var(--ink-2)" }} />
                </div>

                <div>
                  <div className="card-label">
                    {unit === "money" ? "Cost" : "Tokens"} by {data.period === "yearly" ? "month" : "day"}
                  </div>
                  <MachineDays series={data.series} machine={m.machine} unit={unit}
                                fill={harness([...m.sources].sort((a, b) => b.tokens - a.tokens)[0]?.source ?? "").fill} />
                </div>

                {folders.length > 0 && (
                  <div>
                    <div className="card-label">Folders</div>
                    <MiniRank rows={folders.map((p) => ({
                      key: p.project,
                      name: <span title={p.project}>{folderLabel(p.project)}</span>,
                      value: fmtMeasure(unit, p),
                      share: pct(measure(unit, p), topFolder),
                      fill: "var(--ink-3)",
                    }))} />
                  </div>
                )}

                {sources.length > 0 && (
                  <div>
                    <div className="card-label">Harnesses</div>
                    <MiniRank rows={sources.map((s) => ({
                      key: s.source,
                      name: <span style={{ fontFamily: "var(--read)" }}>{harness(s.source).label}</span>,
                      value: fmtMeasure(unit, s),
                      share: pct(measure(unit, s), measure(unit, m)),
                      fill: harness(s.source).fill,
                    }))} />
                  </div>
                )}

                <div className="meter-foot">
                  <span className="live" data-state={seen.state}>{seen.text}</span>
                  <span>{pct(measure(unit, m), total).toFixed(0)}% of the period</span>
                  <span>{fmtInt(m.turns)} replies</span>
                  <span>{unit === "money" ? `${fmtTok(m.tokens)} tokens` : `${fmtEur(m.cost_eur)} list`}</span>
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
        <RankRows rows={byMeasure(unit, data.by_source).filter((s) => s.tokens > 0).map((s) => ({
          key: s.source,
          name: <span>{harness(s.source).label}</span>,
          value: fmtMeasure(unit, s),
          sub: `${s.models} model${s.models === 1 ? "" : "s"}`,
          share: pct(measure(unit, s), total),
          fill: harness(s.source).fill,
        }))} />
      </section>
    </>
  );
}
