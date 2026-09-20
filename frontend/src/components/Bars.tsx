import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { fmtEur, fmtEurShort, fmtInt, fmtTok, type Unit } from "../lib";

/** One bucket of the chart: its figures, and what it was made of. */
export type BarPoint = {
  key: string;
  label: string;                 // under the bar
  title: string;                 // in the readout
  value: number;                 // in the current unit
  tokens: number;
  cost_eur: number;
  turns: number;
  /** Harness split — the only parts that carry a colour. */
  parts?: { key: string; label: string; fill: string; value: number }[];
  /** Models behind the bucket: named, never coloured. */
  models?: { key: string; label: string; value: number }[];
};

/** The top of an axis with four equal, round steps (0 · 10 · 20 · 30 · 40), at or above v. */
function niceMax(v: number) {
  if (!(v > 0)) return 1;
  const raw = v / 4;
  const p = 10 ** Math.floor(Math.log10(raw));
  const n = raw / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p * 4;
}

/**
 * The reading over time: ONE series of square bars, the busiest in ink (DESIGN.md → Charts).
 * The whole plot is the hit target — pointing anywhere picks the bar under the pointer and the
 * readout gives its figures, its harness split and its models. The readout floats beside the
 * selected column on a desktop and sits above the plot on a phone, so it never covers the bar
 * being read. ← → Home End move between buckets.
 */
export default function Bars({ points, unit, height = 230, axis = true }: {
  points: BarPoint[];
  unit: Unit;
  height?: number;
  axis?: boolean;
}) {
  const money = unit === "money";
  const top = niceMax(Math.max(...points.map((p) => p.value), 0));
  const busiest = points.reduce((best, p, i) => (p.value > points[best].value ? i : best), 0);
  const [picked, setPicked] = useState<number | null>(null);
  const [pointerY, setPointerY] = useState<number | null>(null);
  const sel = Math.min(picked ?? busiest, Math.max(0, points.length - 1));
  const b = points[sel];

  const rootRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  // The readout is placed from measured boxes, so it has to be placed again once the
  // chart has a width — and whenever that width changes.
  const [, remeasure] = useState(0);
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => remeasure((n) => n + 1));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useLayoutEffect(() => {
    const root = rootRef.current, box = boxRef.current, plot = plotRef.current;
    if (!root || !box || !plot) return;
    if (window.matchMedia("(max-width: 719px)").matches) { box.style.left = ""; box.style.top = ""; return; }
    const col = plot.querySelectorAll<HTMLElement>(".bars-col")[sel];
    if (!col) return;
    const r = root.getBoundingClientRect(), c = col.getBoundingClientRect(), p = plot.getBoundingClientRect();
    const w = box.offsetWidth, h = box.offsetHeight, gap = 14;
    let left = c.right - r.left + gap;
    if (left + w > r.width) left = c.left - r.left - gap - w;
    const anchor = pointerY ?? p.top - r.top + p.height / 2;
    box.style.left = `${Math.round(Math.max(0, left))}px`;
    box.style.top = `${Math.round(Math.max(0, Math.min(anchor - h / 2, r.height - h)))}px`;
  });

  const pickAt = (e: PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    setPointerY(e.clientY - (rootRef.current?.getBoundingClientRect().top ?? 0));
    setPicked(Math.max(0, Math.min(points.length - 1,
      Math.floor(((e.clientX - rect.left) / rect.width) * points.length))));
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const to = e.key === "ArrowLeft" ? sel - 1 : e.key === "ArrowRight" ? sel + 1
      : e.key === "Home" ? 0 : e.key === "End" ? points.length - 1 : null;
    if (to === null) return;
    e.preventDefault();
    setPointerY(null);
    setPicked(Math.max(0, Math.min(points.length - 1, to)));
  };

  if (!points.length) return <div className="empty">Nothing recorded in this period.</div>;
  const ticks = [0, 1, 2, 3, 4].map((i) => (top / 4) * i);
  // One label per bar while they fit; every k-th when they do not.
  const every = Math.max(1, Math.ceil(points.length / Math.max(1, Math.floor((plotRef.current?.clientWidth ?? 600) / 56))));
  const fmtV = (v: number) => (money ? fmtEur(v) : fmtTok(v));

  return (
    <div className="bars" ref={rootRef}>
      <div className="readout" aria-live="polite" ref={boxRef}>
        <div className="ro-when">{b.title}</div>
        {b.value > 0 || b.turns ? (
          <>
            <div className="ro-value">{fmtV(b.value)}</div>
            <div className="ro-line">
              <b>{money ? fmtTok(b.tokens) : fmtEur(b.cost_eur)}</b> · <b>{fmtInt(b.turns)}</b> {b.turns === 1 ? "reply" : "replies"}
            </div>
          </>
        ) : <div className="ro-empty">Nothing recorded</div>}
        {!!b.parts?.length && (
          <ul className="ro-list">
            {[...b.parts].sort((x, y) => y.value - x.value).map((p) => (
              <li key={p.key}>
                <i className="sw" style={{ background: p.fill }} />
                <span className="ro-name">{p.label}</span>
                <span className="ro-val">{fmtV(p.value)}</span>
              </li>
            ))}
          </ul>
        )}
        {!!b.models?.length && (
          <ul className="ro-list">
            {[...b.models].sort((x, y) => y.value - x.value).slice(0, 3).map((m) => (
              <li className="ro-sub" key={m.key}>
                <span className="ro-name">{m.label}</span>
                <span className="ro-val">{fmtV(m.value)}</span>
              </li>
            ))}
            {b.models.length > 3 && (
              <li className="ro-sub"><span className="ro-name">{b.models.length - 3} more</span><span /></li>
            )}
          </ul>
        )}
      </div>

      <div className="bars-body" style={{ marginLeft: axis ? undefined : 0 }}>
        <div className="bars-plot" ref={plotRef} style={{ height }} role="group" tabIndex={0}
             aria-label="Chart. Left and right arrows move between bars."
             onPointerMove={pickAt} onPointerDown={pickAt} onKeyDown={onKey}>
          {axis && (
            <div className="bars-grid" aria-hidden="true">
              {ticks.map((v, i) => (
                <div key={v} style={{ bottom: `${(i / 4) * 100}%` }} data-zero={i === 0 ? "" : undefined}>
                  <span>{money ? fmtEurShort(v) : fmtTok(v)}</span>
                </div>
              ))}
            </div>
          )}
          {points.map((p, i) => (
            <div className="bars-col" key={p.key} data-picked={i === sel ? "true" : "false"}>
              {p.value > 0
                ? <i data-peak={i === busiest ? "true" : "false"} style={{ height: `${(p.value / top) * 100}%` }} />
                : <i data-empty="true" />}
            </div>
          ))}
        </div>
        <div className="bars-axis" aria-hidden="true">
          {points.map((p, i) => <span key={p.key}>{i % every === 0 ? p.label : ""}</span>)}
        </div>
      </div>
    </div>
  );
}
