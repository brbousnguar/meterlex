import type { Overview } from "../api";
import Odometer from "../components/Odometer";
import { useState } from "react";
import { DayBars, RankRows, SplitBar, type Split } from "../components/charts";
import Treemap, { type Tile } from "../components/Treemap";
import { ago, bucketFull, byMeasure, change, fmtEur, fmtInt, fmtMeasure, fmtTok, folderLabel, harness, measure, moneyDrums, pct, PLAIN, type Unit } from "../lib";
import Seg from "../components/Seg";
import { recall, remember } from "../lib";
import type { Tab } from "../App";

const SPLITS: { id: Split; short: string }[] = [{ id: "harness", short: "Harness" }, { id: "model", short: "Model" }];

export default function Now({ data, unit, go }: { data: Overview; unit: Unit; go: (t: Tab) => void }) {
  const t = data.totals;
  const money = unit === "money";
  const total = measure(unit, t);
  const delta = money
    ? change(t.cost_eur, data.previous?.cost_eur)
    : change(t.tokens, data.previous?.tokens);
  const paidRatio = t.sub_eur > 0 ? t.cost_eur / t.sub_eur : null;
  const cacheShare = pct(t.cache_read, t.tokens);
  const [split, setSplit] = useState<Split>(() => (recall("mx.split") as Split) || "harness");
  const pickSplit = (s: Split) => { setSplit(s); remember("mx.split", s); };

  return (
    <>
      <section className="reading">
        {/* The header already names the period; this is the reading itself. */}
        {/* The size of the reading is said here, in both units, so the drums stand alone. */}
        <div className="reading-label">
          {money ? `${fmtEur(t.cost_eur)} at list price, ` : `${fmtTok(t.tokens)} tokens, `}
          metered on {t.machines || "no"} machine{t.machines === 1 ? "" : "s"}
        </div>
        {money
          ? <Odometer value={t.cost_eur} unit="euros" digits={moneyDrums(t.cost_eur)} cents />
          : <Odometer value={t.tokens} unit="tokens" digits={data.period === "daily" ? 8 : data.period === "weekly" ? 9 : 10} />}
        <div className="reading-meta">
          {delta !== null && (
            <span className="delta" data-dir={delta >= 0 ? "up" : "down"}>
              {delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(0)}%
              <span>
                vs {data.period === "daily" ? "yesterday" : data.period === "weekly" ? "last week" : data.period === "monthly" ? "last month" : "last year"}
              </span>
            </span>
          )}
          <span className="rank-sub">
            {fmtInt(t.turns)} replies across {fmtInt(t.sessions)} sessions
          </span>
          {/* A silent collector is visible here, without opening the Machines screen. */}
          {[...data.by_machine]
            .filter((m) => m.last_seen_at)
            .sort((a, b) => (b.last_seen_at! < a.last_seen_at! ? -1 : 1))
            .slice(0, 3)
            .map((m) => {
              const seen = ago(m.last_seen_at);
              return (
                <span className="live" key={m.machine} data-state={seen.state}
                      title={`Last reading received from ${m.machine}`}>
                  <span className="mono">{m.machine}</span> · {seen.text}
                </span>
              );
            })}
        </div>
      </section>

      {/* On the paper, each stat is a card of its own. */}
      <section className="section bare">
        <div className="rail lift">
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
            {money ? "Cost" : "Tokens"} by {data.period === "yearly" ? "month" : data.period === "daily" ? "hour" : "day"}
          </h2>
          {data.busiest && (
            <div className="section-note">
              busiest: {bucketFull(data.busiest.bucket)} · {fmtMeasure(unit, data.busiest)}
            </div>
          )}
        </div>
        <div className="split-pick">
          <Seg label="Split the bars by" value={split} options={SPLITS} onPick={pickSplit} />
        </div>
        <DayBars series={data.series} unit={unit} split={split} modelOrder={data.model_order} />
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">What the tokens were</h2>
          <div className="section-note">{fmtInt(t.tokens)} in total</div>
        </div>
        <SplitBar split={t} total={t.tokens} />
      </section>

      <div className="cols2">
      <div className="side">
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
          fill: PLAIN,
          parts: (m.sources ?? []).map((s) => ({ value: measure(unit, s), fill: harness(s.source).fill })),
          onClick: () => go("machines"),
        }))} />
      </section>
      </div>

      <section className="section" data-slot="map">
        <div className="section-head">
          <h2 className="section-title">Where the work happened</h2>
          <div className="section-note">area = {money ? "euros" : "tokens"} · colour = the harness that did most of it</div>
        </div>
        <Treemap tiles={byMeasure(unit, data.by_project).map((p): Tile => {
          const top = [...(p.sources ?? [])].sort((a, b) => b.tokens - a.tokens)[0];
          return {
            key: p.project,
            label: folderLabel(p.project),
            value: measure(unit, p),
            valueText: fmtMeasure(unit, p),
            fill: harness(top?.source ?? "").tile,
            ink: harness(top?.source ?? "").on,
            rows: [
              [money ? "tokens" : "list price", money ? fmtTok(p.tokens) : fmtEur(p.cost_eur)],
              ["replies", fmtInt(p.turns)],
              ...(top ? [["mostly", harness(top.source).label] as [string, string]] : []),
              ["folder", p.project],
            ],
          };
        })} />
      </section>

      <section className="section" data-slot="folders">
        <div className="section-head">
          <h2 className="section-title">Top folders</h2>
          <div className="section-note">bars split by harness</div>
        </div>
        <RankRows rows={byMeasure(unit, data.by_project).slice(0, 6).map((p) => ({
          key: p.project,
          name: <span className="mono" title={p.project}>{folderLabel(p.project)}</span>,
          value: fmtMeasure(unit, p),
          sub: money ? fmtTok(p.tokens) : fmtEur(p.cost_eur),
          share: pct(measure(unit, p), measure(unit, byMeasure(unit, data.by_project)[0] ?? p)),
          fill: harness([...(p.sources ?? [])].sort((a, b) => b.tokens - a.tokens)[0]?.source ?? "").fill,
          parts: (p.sources ?? []).map((s) => ({ value: measure(unit, s), fill: harness(s.source).fill })),
        }))} />
      </section>
      </div>
    </>
  );
}
