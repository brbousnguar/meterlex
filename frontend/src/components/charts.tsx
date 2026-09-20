import type { Bucket, TokenSplit } from "../api";
import Bars, { type BarPoint } from "./Bars";
import { bucketFull, bucketLabel, fmtEur, fmtInt, fmtTok, harness, pct, SPLIT, type Unit } from "../lib";

/* Forms follow DESIGN.md → Charts. The reading over time is ONE series: the harness split and
   the models live in the readout, because a six-colour stack fails CVD separation and answers a
   question that does not need colour. */

/** The harness that did most of a bucket's work — the bar wears its colour. */
const leader = (b: Bucket) => {
  const [top] = Object.entries(b.by_source).sort((x, y) => y[1] - x[1]);
  return top ? harness(top[0]).fill : "var(--ink-3)";
};

export function DayBars({ series, unit }: { series: Bucket[]; unit: Unit }) {
  const money = unit === "money";
  const points: BarPoint[] = series.map((b) => ({
    fill: leader(b),
    key: b.bucket,
    label: bucketLabel(b.bucket),
    title: bucketFull(b.bucket),
    value: money ? b.cost_eur : b.tokens,
    tokens: b.tokens,
    cost_eur: b.cost_eur,
    turns: b.turns,
    parts: Object.entries(money ? b.cost_by_source : b.by_source).map(([src, v]) => ({
      key: src, label: harness(src).label, fill: harness(src).fill, value: v,
    })),
    models: Object.entries((money ? b.cost_by_model : b.by_model) ?? {}).map(([m, v]) => ({ key: m, label: m, value: v })),
  }));
  return <Bars points={points} unit={unit} />;
}

/** One machine's reading per bucket. This replaced a sparkline: a shape with no numbers cannot
 *  answer "how many tokens on Tuesday", which is the question the machine screen exists for. */
export function MachineDays({ series, machine, unit, fill }: {
  series: Bucket[]; machine: string; unit: Unit;
  /** The harness this machine mostly runs — its bars wear that colour. */
  fill?: string;
}) {
  const money = unit === "money";
  const points: BarPoint[] = series.map((b) => ({
    fill,
    key: b.bucket,
    label: bucketLabel(b.bucket),
    title: bucketFull(b.bucket),
    value: money ? (b.cost_by_machine[machine] ?? 0) : (b.by_machine[machine] ?? 0),
    tokens: b.by_machine[machine] ?? 0,
    cost_eur: b.cost_by_machine[machine] ?? 0,
  }));
  const peak = Math.max(...points.map((p) => p.value), 0);
  if (peak === 0) return <p className="rank-sub">Nothing from this machine in the period.</p>;
  const busiest = points.find((p) => p.value === peak)!;
  return (
    <>
      <Bars points={points} unit={unit} height={94} axis={false} compact />
      <p className="rank-sub">
        busiest {busiest.title} · {money ? fmtEur(peak) : fmtTok(peak)}
      </p>
    </>
  );
}

/** A ranked strip inside a card: name, figure, and a hairline bar. */
export function MiniRank({ rows }: { rows: { key: string; name: React.ReactNode; value: string; share: number; fill: string }[] }) {
  if (!rows.length) return null;
  return (
    <ul className="mini-rank">
      {rows.map((r) => (
        <li key={r.key}>
          <span className="mini-name">{r.name}</span>
          <span className="mini-value">{r.value}</span>
          <i className="mini-bar"><i style={{ width: `${Math.max(2, r.share)}%`, background: r.fill }} /></i>
        </li>
      ))}
    </ul>
  );
}

/** Parts of one whole: a single 100% bar in one sequential ramp, 2px gaps. */
export function SplitBar({ split, total }: { split: TokenSplit; total: number }) {
  const parts = SPLIT.map((s) => ({ ...s, value: split[s.key] ?? 0 })).filter((s) => s.value > 0);
  if (!parts.length || total <= 0) return <div className="empty">No tokens in this period.</div>;
  return (
    <>
      <div className="split" role="img"
           aria-label={parts.map((p) => `${p.label} ${pct(p.value, total).toFixed(0)}%`).join(", ")}>
        {parts.map((p) => (
          <div className="split-seg" key={p.key}
               style={{ background: p.fill, width: `${pct(p.value, total)}%` }} title={`${p.label}: ${fmtInt(p.value)}`} />
        ))}
      </div>
      <ul className="split-legend">
        {parts.map((p) => (
          <li key={p.key} title={p.hint}>
            <i className="swatch" style={{ background: p.fill }} />
            {p.label} <b>{pct(p.value, total).toFixed(0)}%</b> <span>{fmtTok(p.value)}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

const sumOf = (parts: { value: number }[]) => parts.reduce((a, p) => a + p.value, 0) || 1;

/** A ranked list. The bar is the comparison; the colour is identity only. */
export function RankRows({ rows }: {
  rows: {
    key: string; name: React.ReactNode; value: string; sub?: string; share: number; fill: string;
    /** Split the bar by harness, the way the folder map is coloured. */
    parts?: { value: number; fill: string }[];
    onClick?: () => void;
  }[];
}) {
  if (!rows.length) return <div className="empty">Nothing recorded in this period.</div>;
  return (
    <div className="rank">
      {rows.map((r) => {
        const Row = r.onClick ? "button" : "div";
        return (
          <Row className="rank-row" key={r.key} onClick={r.onClick} type={r.onClick ? "button" : undefined}>
            <div className="rank-name"><i className="dot" style={{ background: r.fill }} />{r.name}</div>
            <div style={{ textAlign: "right" }}>
              <div className="rank-value">{r.value}</div>
              {r.sub && <div className="rank-sub">{r.sub}</div>}
            </div>
            <div className="rank-bar">
              {r.parts?.length
                ? r.parts.filter((p) => p.value > 0).map((p, i) => (
                    <i key={i} style={{ width: `${Math.max(0.5, (p.value / sumOf(r.parts!)) * Math.max(1.5, r.share))}%`, background: p.fill }} />
                  ))
                : <i style={{ width: `${Math.max(1.5, r.share)}%`, background: r.fill }} />}
            </div>
          </Row>
        );
      })}
    </div>
  );
}
