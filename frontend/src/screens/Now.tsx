import type { Overview } from "../api";
import Odometer from "../components/Odometer";
import { DayBars, RankRows, SplitBar } from "../components/charts";
import { bucketFull, byMeasure, change, fmtEur, fmtInt, fmtMeasure, fmtTok, folderLabel, harness, measure, moneyDrums, pct, type Unit } from "../lib";
import type { Tab } from "../App";

export default function Now({ data, unit, go }: { data: Overview; unit: Unit; go: (t: Tab) => void }) {
  const t = data.totals;
  const money = unit === "money";
  const total = measure(unit, t);
  const delta = money
    ? change(t.cost_eur, data.previous?.cost_eur)
    : change(t.tokens, data.previous?.tokens);
  const paidRatio = t.sub_eur > 0 ? t.cost_eur / t.sub_eur : null;
  const cacheShare = pct(t.cache_read, t.tokens);

  return (
    <>
      <section className="reading">
        {/* The header already names the period; this is the reading itself. */}
        <div className="reading-label">
          {money ? `${fmtEur(t.cost_eur)} at list price, ` : ""}
          metered on {t.machines || "no"} machine{t.machines === 1 ? "" : "s"}
        </div>
        {money
          ? <Odometer value={Math.round(t.cost_eur)} unit="euros" digits={moneyDrums(t.cost_eur)} />
          : <Odometer value={t.tokens} unit="tokens" digits={data.period === "weekly" ? 9 : 10} />}
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          {delta !== null && (
            <span className="delta" data-dir={delta >= 0 ? "up" : "down"}>
              {delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(0)}%
              <span style={{ color: "var(--ink-3)", fontWeight: 500 }}>
                vs {data.period === "weekly" ? "last week" : data.period === "monthly" ? "last month" : "last year"}
              </span>
            </span>
          )}
          <span className="rank-sub">
            {fmtInt(t.turns)} replies across {fmtInt(t.sessions)} sessions
          </span>
        </div>
      </section>

      <section className="section">
        <div className="rail">
          <div className="stat">
            <div className="stat-label">List price</div>
            <div className="stat-value">{fmtEur(t.cost_eur)}</div>
            <div className="stat-sub">what the tokens would cost per API rates</div>
          </div>
          <div className="stat">
            <div className="stat-label">Actually paid</div>
            <div className="stat-value">{fmtEur(t.sub_eur)}</div>
            <div className="stat-sub">
              subscriptions, {data.period === "monthly" ? "this month" : "pro-rated over the period"}
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Subscriptions earn</div>
            <div className={`stat-value ${paidRatio === null ? "" : paidRatio >= 1 ? "under" : "over"}`}>
              {paidRatio === null ? "—" : `${paidRatio.toFixed(1)}×`}
            </div>
            <div className="stat-sub">
              {paidRatio === null ? "no fee recorded"
                : paidRatio >= 1 ? "list price above the fees" : "the fees cost more than the usage"}
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">From cache</div>
            <div className="stat-value">{cacheShare.toFixed(0)}%</div>
            <div className="stat-sub">{fmtTok(t.cache_read)} replayed, not re-sent</div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">
            {money ? "Cost" : "Tokens"} by {data.period === "yearly" ? "month" : "day"}
          </h2>
          {data.busiest && (
            <div className="section-note">
              busiest: {bucketFull(data.busiest.bucket)} · {fmtMeasure(unit, data.busiest)}
            </div>
          )}
        </div>
        <DayBars series={data.series} period={data.period} unit={unit} />
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">What the tokens were</h2>
          <div className="section-note">{fmtInt(t.tokens)} in total</div>
        </div>
        <SplitBar split={t} total={t.tokens} />
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">Harnesses</h2>
          <button className="section-note" onClick={() => go("harnesses")}>all {data.by_source.length} →</button>
        </div>
        <RankRows rows={byMeasure(unit, data.by_source).filter((s) => s.tokens > 0).slice(0, 4).map((s) => ({
          key: s.source,
          name: <span>{harness(s.source).label}</span>,
          value: fmtMeasure(unit, s),
          sub: money ? `${fmtTok(s.tokens)} · ${fmtEur(s.sub_eur)} paid` : `${fmtEur(s.cost_eur)} list · ${fmtEur(s.sub_eur)} paid`,
          share: pct(measure(unit, s), total),
          fill: harness(s.source).fill,
          onClick: () => go("harnesses"),
        }))} />
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">Machines</h2>
          <button className="section-note" onClick={() => go("machines")}>all {data.by_machine.length} →</button>
        </div>
        <RankRows rows={byMeasure(unit, data.by_machine).filter((m) => m.tokens > 0).slice(0, 4).map((m) => ({
          key: m.machine,
          name: <span className="mono">{m.machine}</span>,
          value: fmtMeasure(unit, m),
          sub: `${fmtInt(m.turns)} replies`,
          share: pct(measure(unit, m), total),
          fill: "var(--ink-2)",
          onClick: () => go("machines"),
        }))} />
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">Where the work happened</h2>
          <div className="section-note">top folders</div>
        </div>
        <RankRows rows={byMeasure(unit, data.by_project).slice(0, 6).map((p) => ({
          key: p.project,
          name: <span className="mono" title={p.project}>{folderLabel(p.project)}</span>,
          value: fmtMeasure(unit, p),
          sub: money ? fmtTok(p.tokens) : fmtEur(p.cost_eur),
          share: pct(measure(unit, p), measure(unit, byMeasure(unit, data.by_project)[0] ?? p)),
          fill: "var(--ink-3)",
        }))} />
      </section>
    </>
  );
}
