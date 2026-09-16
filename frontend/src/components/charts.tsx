import {
  Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { Bucket, TokenSplit } from "../api";
import { bucketFull, bucketLabel, fmtEur, fmtEurShort, fmtInt, fmtTok, harness, pct, SPLIT, type Unit } from "../lib";

/* Recharts writes the tick colour as an SVG attribute, which the stylesheet
   cannot override — so the axes carry it as a prop. */
const TICK = { fill: "var(--ink-3)", fontFamily: "var(--mono)", fontSize: 10 };

/* Forms follow DESIGN.md → Charts. Tokens per bucket is ONE series: the harness
   split lives in the tooltip, because a six-colour stack fails CVD separation
   and answers a question that does not need colour. */

export function DayBars({ series, period, unit }: { series: Bucket[]; period: string; unit: Unit }) {
  const data = series.map((b) => ({ ...b, label: bucketLabel(b.bucket), v: unit === "money" ? b.cost_eur : b.tokens }));
  const peak = Math.max(...data.map((d) => d.v), 0);
  return (
    <div style={{ height: 210, margin: "4px -6px 0" }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 6, bottom: 0, left: 6 }} barCategoryGap={period === "yearly" ? 6 : 3}>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="label" tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={14}
                 tick={TICK} />
          <YAxis width={56} tickLine={false} axisLine={false} tick={TICK}
                 tickFormatter={(v: number) => (unit === "money" ? fmtEurShort(v) : fmtTok(v))} />
          <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<BucketTip unit={unit} />} />
          <Bar dataKey="v" isAnimationActive={false}>
            {data.map((d) => (
              /* The busiest bucket is the only one that changes weight: it is
                 the answer to "when did this happen", not a second series. */
              <Cell key={d.bucket} fill={d.v === peak && peak > 0 ? "var(--ink)" : "var(--ink-3)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function BucketTip({ active, payload, unit }: any) {
  if (!active || !payload?.length) return null;
  const b: Bucket = payload[0].payload;
  const money = unit === "money";
  const rows = Object.entries(money ? b.cost_by_source : b.by_source).sort((a, c) => c[1] - a[1]);
  return (
    <div className="tip">
      <div className="tip-head">{bucketFull(b.bucket)}</div>
      <div className="tip-row">{fmtInt(b.tokens)} tokens <span>{fmtEur(b.cost_eur)}</span></div>
      <div className="tip-row" style={{ color: "var(--ink-3)" }}>{fmtInt(b.turns)} replies</div>
      {rows.length > 0 && <div style={{ borderTop: "1px solid var(--rule)", margin: "6px 0 5px" }} />}
      {rows.map(([src, tok]) => (
        <div className="tip-row" key={src}>
          <span style={{ marginLeft: 0, display: "inline-flex", alignItems: "center", gap: 6 }}>
            <i className="dot" style={{ background: harness(src).fill }} /> {harness(src).label}
          </span>
          <span>{money ? fmtEur(tok) : fmtTok(tok)}</span>
        </div>
      ))}
    </div>
  );
}

/** One machine's reading per bucket. This replaced a sparkline: a shape with no
 *  numbers cannot answer "how many tokens on Tuesday", which is the question
 *  the machine screen exists for. */
export function MachineDays({ series, machine, period, unit }: {
  series: Bucket[]; machine: string; period: string; unit: Unit;
}) {
  const data = series.map((b) => ({
    bucket: b.bucket,
    label: bucketLabel(b.bucket),
    tokens: b.by_machine[machine] ?? 0,
    cost_eur: b.cost_by_machine[machine] ?? 0,
    v: unit === "money" ? (b.cost_by_machine[machine] ?? 0) : (b.by_machine[machine] ?? 0),
  }));
  const peak = Math.max(...data.map((d) => d.v), 0);
  if (peak === 0) return <p className="rank-sub">Nothing from this machine in the period.</p>;
  const busiest = data.find((d) => d.v === peak)!;
  return (
    <>
      <div style={{ height: 94, margin: "0 -4px" }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}
                    barCategoryGap={period === "yearly" ? 5 : 3}>
            <XAxis dataKey="label" tickLine={false} axisLine={false} tick={TICK}
                   interval="preserveStartEnd" minTickGap={10} />
            <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<DayTip />} />
            <Bar dataKey="v" isAnimationActive={false}>
              {data.map((d) => (
                <Cell key={d.bucket} fill={d.v === peak ? "var(--ink)" : "var(--ink-3)"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="rank-sub">
        busiest {bucketFull(busiest.bucket)} · {unit === "money" ? fmtEur(peak) : fmtTok(peak)}
      </p>
    </>
  );
}

function DayTip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="tip">
      <div className="tip-head">{bucketFull(d.bucket)}</div>
      <div className="tip-row">{fmtInt(d.tokens)} tokens <span>{fmtEur(d.cost_eur)}</span></div>
    </div>
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

/** A ranked list. The bar is the comparison; the colour is identity only. */
export function RankRows({ rows }: {
  rows: { key: string; name: React.ReactNode; value: string; sub?: string; share: number; fill: string; onClick?: () => void }[];
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
            <div className="rank-bar"><i style={{ width: `${Math.max(1.5, r.share)}%`, background: r.fill }} /></div>
          </Row>
        );
      })}
    </div>
  );
}
