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
  /** Replies behind the bucket, when the series carries them. */
  turns?: number;
  /** The harness split — stacked, and the only parts that carry a colour. */
  parts?: { key: string; label: string; fill: string; value: number }[];
  /** Models behind the bucket: named, never coloured. */
  models?: { key: string; label: string; value: number }[];
  /** A bar with no split takes this colour. */
  fill?: string;
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
 * The reading over time. Each bar is stacked by harness, in one order for the whole period, so a
 * month of bars shows the mix instead of one colour. The whole plot is the hit target: pointing at
 * a bar picks it, pointing at a part focuses that harness across every bar, and the legend does the
 * same (a tap pins it). The readout shows while you are pointing at the chart or have it focused,
 * beside the selected column on a desktop, as a panel above the plot on a phone — never over the
 * bar it describes. ← → Home End move between buckets, ↑ ↓ between parts, Esc clears.
 */
export default function Bars({ points, unit, height = 230, axis = true, compact = false, legend = true }: {
  points: BarPoint[];
  unit: Unit;
  height?: number;
  axis?: boolean;
  /** A small multiple: the readout sits above the plot instead of floating over 94px of bars. */
  compact?: boolean;
  legend?: boolean;
}) {
  const money = unit === "money";
  const top = niceMax(Math.max(...points.map((p) => p.value), 0));
  const busiest = points.reduce((best, p, i) => (p.value > points[best].value ? i : best), 0);
  const [picked, setPicked] = useState<number | null>(null);
  const [pointerY, setPointerY] = useState<number | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);   // a part under the pointer
  const [pinned, setPinned] = useState<string | null>(null);     // a harness pinned from the legend
  const [reading, setReading] = useState(false);                 // pointing at the plot, or keyboard
  const focus = hovered ?? pinned;
  const sel = Math.min(picked ?? busiest, Math.max(0, points.length - 1));
  const b = points[sel];

  const rootRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  // The readout is placed from measured boxes, so it is placed again once the chart has a width.
  const [, remeasure] = useState(0);
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => remeasure((n) => n + 1));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  /** The harnesses of the whole period, in one order, so a colour keeps its place across bars. */
  const order: { key: string; label: string; fill: string; total: number }[] = [];
  for (const p of points) {
    for (const part of p.parts ?? []) {
      const seen = order.find((o) => o.key === part.key);
      if (seen) seen.total += part.value;
      else order.push({ key: part.key, label: part.label, fill: part.fill, total: part.value });
    }
  }
  order.sort((a, c) => c.total - a.total);
  const stacked = order.length > 0;

  useLayoutEffect(() => {
    const root = rootRef.current, box = boxRef.current, plot = plotRef.current;
    if (!root || !box || !plot) return;
    if (compact || window.matchMedia("(max-width: 719px)").matches) { box.style.left = ""; box.style.top = ""; return; }
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
    setReading(true);
    setPointerY(e.clientY - (rootRef.current?.getBoundingClientRect().top ?? 0));
    const i = Math.max(0, Math.min(points.length - 1,
      Math.floor(((e.clientX - rect.left) / rect.width) * points.length)));
    setPicked(i);
    // Which part is under the pointer is worked out from its height in the stack, not from the
    // element under it: a harness with 1% of the bar is a 2px band no pointer can enter reliably.
    if (!stacked) { setHovered(null); return; }
    const atValue = ((rect.bottom - e.clientY) / rect.height) * top;
    let acc = 0, hit: string | null = null;
    for (const o of order) {
      const v = points[i].parts?.find((x) => x.key === o.key)?.value ?? 0;
      if (v <= 0) continue;
      acc += v;
      if (atValue <= acc) { hit = o.key; break; }
    }
    setHovered(atValue > acc ? null : hit);   // above the bar: the bucket's own readout
  };
  const leave = (e?: PointerEvent<HTMLDivElement>) => {
    if (e && e.pointerType === "touch") return;   // a finger lifting is not the pointer leaving
    setReading(false);
    setHovered(null);
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const keys = (b?.parts ?? []).map((p) => p.key);
    const at = focus ? keys.indexOf(focus) : -1;
    if (e.key === "ArrowLeft") setPicked(Math.max(0, sel - 1));
    else if (e.key === "ArrowRight") setPicked(Math.min(points.length - 1, sel + 1));
    else if (e.key === "Home") setPicked(0);
    else if (e.key === "End") setPicked(points.length - 1);
    else if (e.key === "ArrowUp" && keys.length) setHovered(keys[Math.min(keys.length - 1, at + 1)]);
    else if (e.key === "ArrowDown" && keys.length) setHovered(at <= 0 ? null : keys[at - 1]);
    else if (e.key === "Escape") { setHovered(null); setPinned(null); }
    else return;
    e.preventDefault();
    setPointerY(null);
    setReading(true);
  };

  if (!points.length) return <div className="empty">Nothing recorded in this period.</div>;
  const ticks = [0, 1, 2, 3, 4].map((i) => (top / 4) * i);
  // One label per bar while they fit; every k-th when they do not.
  const every = Math.max(1, Math.ceil(points.length / Math.max(1, Math.floor((plotRef.current?.clientWidth ?? 600) / 56))));
  const fmtV = (v: number) => (money ? fmtEur(v) : fmtTok(v));
  const focused = focus ? b?.parts?.find((p) => p.key === focus) : undefined;
  const pctOf = focused && b.value > 0 ? (focused.value / b.value) * 100 : null;
  const share = pctOf === null ? null : pctOf < 0.5 ? "<1%" : `${Math.round(pctOf)}%`;

  return (
    <div className="bars" ref={rootRef}>
      <div className={`readout${compact ? " readout-inline" : ""}`} aria-live="polite" ref={boxRef}
           data-show={reading ? "true" : "false"}>
        <div className="ro-when">{b.title}</div>
        {focused ? (
          <>
            <div className="ro-focus"><i className="sw" style={{ background: focused.fill }} />{focused.label}</div>
            <div className="ro-value">{fmtV(focused.value)}</div>
            <div className="ro-line">
              {share !== null && <>{share} of this bar · </>}<b>{fmtV(b.value)}</b> in total
            </div>
          </>
        ) : b.value > 0 ? (
          <>
            <div className="ro-value">{fmtV(b.value)}</div>
            <div className="ro-line">
              <b>{money ? fmtTok(b.tokens) : fmtEur(b.cost_eur)}</b>
              {b.turns != null && <> · <b>{fmtInt(b.turns)}</b> {b.turns === 1 ? "reply" : "replies"}</>}
            </div>
          </>
        ) : <div className="ro-empty">Nothing recorded</div>}
        {!!b.parts?.length && !compact && (
          <ul className="ro-list">
            {[...b.parts].sort((x, y) => y.value - x.value).map((p) => (
              <li key={p.key} data-on={focus === p.key ? "true" : "false"}>
                <i className="sw" style={{ background: p.fill }} />
                <span className="ro-name">{p.label}</span>
                <span className="ro-val">{fmtV(p.value)}</span>
              </li>
            ))}
          </ul>
        )}
        {!!b.models?.length && !compact && !focused && (
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
             data-focusing={focus ? "true" : "false"}
             aria-label="Chart. Left and right arrows move between bars, up and down between harnesses."
             onPointerMove={pickAt} onPointerDown={pickAt} onPointerLeave={leave}
             onFocus={() => setReading(true)} onBlur={() => leave()} onKeyDown={onKey}>
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
              {stacked
                ? order.map((o) => {
                    const part = p.parts?.find((x) => x.key === o.key);
                    if (!part || part.value <= 0) return null;
                    return (
                      <i className="part" key={o.key} data-on={focus === o.key ? "true" : "false"}
                         style={{ height: `${(part.value / top) * 100}%`, background: o.fill }} />
                    );
                  })
                : p.value > 0
                  ? <i data-peak={i === busiest ? "true" : "false"}
                       style={{ height: `${(p.value / top) * 100}%`, background: i === busiest ? undefined : p.fill }} />
                  : null}
              {p.value <= 0 && <i data-empty="true" />}
            </div>
          ))}
        </div>
        <div className="bars-axis" aria-hidden="true">
          {points.map((p, i) => <span key={p.key}>{i % every === 0 ? p.label : ""}</span>)}
        </div>
      </div>

      {stacked && legend && (
        <ul className="bars-legend">
          {order.map((o) => (
            <li key={o.key}>
              <button type="button" aria-pressed={pinned === o.key} data-on={focus === o.key ? "true" : "false"}
                      onPointerEnter={(e) => { if (e.pointerType === "mouse") { setHovered(o.key); setReading(true); } }}
                      onPointerLeave={(e) => { if (e.pointerType === "mouse") setHovered(null); }}
                      onClick={() => setPinned(pinned === o.key ? null : o.key)}>
                <i className="sw" style={{ background: o.fill }} />
                <span className="lg-name">{o.label}</span>
                <span className="lg-val">{fmtV(o.total)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
