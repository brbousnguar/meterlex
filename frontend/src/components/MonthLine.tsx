import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { Credits } from "../api";
import { fmtInt } from "../lib";

const DAY = 24 * 3600 * 1000;
const L = 34, T = 14, B = 26;

/**
 * The credits month against an even pace, on the week line's pattern (#75). ElevenLabs counts
 * credits per UTC day, so the thick line is exact: the credits so far at the end of each day, then
 * the last reading. The dashed diagonal is an even month, and the dotted line is "at this rate"
 * from the reading to the reset, or to where it hits 100%. The whole plot is the hit target:
 * pointing at it reads any day beside the pointer (a panel above the plot on a phone), and the
 * readout leaves with it. ← → move a day, Shift ← → a week, Home End, Esc clears.
 */
export default function MonthLine({ c }: { c: Credits }) {
  const w = c.window!;
  const start = Date.parse(w.start), reset = Date.parse(w.reset);
  const nowMs = Math.min(reset, Date.parse(c.now));
  const readMs = Math.min(nowMs, Date.parse(c.reading!.at));
  const limit = c.limit || 1;
  const usedPct = c.used_pct ?? null;               // null once the reset passed the reading
  const days = (c.days ?? []).map((d) => {
    const s = Date.parse(`${d.day}T00:00:00Z`);
    return { ...d, from: Math.max(start, s), to: Math.min(reset, s + DAY) };
  });
  const todayI = Math.max(0, days.findIndex((d) => d.current));
  const [picked, setPicked] = useState<number | null>(null);
  const [pointerY, setPointerY] = useState<number | null>(null);
  const [reading, setReading] = useState(false);
  const sel = Math.min(days.length - 1, picked ?? todayI);

  const rootRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(700);
  useEffect(() => {
    const el = plotRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.round(el.clientWidth) || 700));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const narrow = width < 520;
  const W = width, H = narrow ? 214 : 244, R = narrow ? 20 : 10;

  // ── the series ─────────────────────────────────────────────────────────────
  const x = (ms: number) => L + ((ms - start) / (reset - start)) * (W - L - R);
  const y = (v: number) => T + (1 - Math.min(v, 100) / 100) * (H - T - B);
  const cum: number[] = [];
  days.reduce((acc, d, i) => (cum[i] = acc + d.used), 0);
  const pctOf = (credits: number) => (100 * credits) / limit;
  const pts = [{ ms: start, pct: 0 }];
  days.forEach((d, i) => { if (!d.future && !d.current && d.to <= readMs) pts.push({ ms: d.to, pct: pctOf(cum[i]) }); });
  if (usedPct !== null) pts.push({ ms: readMs, pct: usedPct });
  const used = pts.map((p, i) => `${i ? "L" : "M"}${x(p.ms)},${y(p.pct)}`).join("");

  // "at this rate", from the reading to the reset, or to where the month hits 100%
  const proj = c.pace?.projected_pct ?? null;
  const runsOut = c.pace?.runs_out_at ? Math.max(readMs, Date.parse(c.pace.runs_out_at)) : null;
  const projAt = (ms: number) => proj !== null && usedPct !== null
    ? Math.min(100, usedPct + (proj - usedPct) * ((ms - readMs) / Math.max(1, reset - readMs)))
    : null;
  const projEnd = runsOut ?? reset;
  const projEndPct = usedPct !== null && usedPct < 100 ? (runsOut ? 100 : proj) : null;

  // ── the picked day ─────────────────────────────────────────────────────────
  const d = days[sel];
  const pace = d ? (100 * (d.to - start)) / (reset - start) : 0;
  const future = !d || d.future;
  const val: number | null = !d ? null : d.current ? usedPct : future ? projAt(d.to) : pctOf(cum[sel]);
  const delta = val !== null ? val - pace : null;
  const dayName = (iso: string) => new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-GB", {
    timeZone: "UTC", weekday: "short", day: "numeric", month: "short" });

  useLayoutEffect(() => {
    const root = rootRef.current, box = boxRef.current, plot = plotRef.current;
    if (!root || !box || !plot || !d) return;
    if (window.matchMedia("(max-width: 719px)").matches) { box.style.left = ""; box.style.top = ""; return; }
    const r = root.getBoundingClientRect(), p = plot.getBoundingClientRect();
    const gx = p.left - r.left + (x((d.from + d.to) / 2) / W) * p.width;
    const bw = box.offsetWidth, bh = box.offsetHeight, gap = 18;
    let left = gx + gap;
    if (left + bw > r.width) left = gx - gap - bw;
    const anchor = pointerY ?? p.top - r.top + p.height / 2;
    box.style.left = `${Math.round(Math.max(0, left))}px`;
    box.style.top = `${Math.round(Math.max(0, Math.min(anchor - bh / 2, r.height - bh)))}px`;
  });

  const pickAt = (e: PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ms = start + (((e.clientX - rect.left) / rect.width * W - L) / (W - L - R)) * (reset - start);
    const i = days.findIndex((dd) => ms < dd.to);
    setReading(true);
    setPointerY(e.clientY - (rootRef.current?.getBoundingClientRect().top ?? 0));
    setPicked(i < 0 ? days.length - 1 : i);
  };
  const leave = (e?: PointerEvent<HTMLDivElement>) => {
    if (e && e.pointerType === "touch") return;   // a finger lifting is not the pointer leaving
    setReading(false);
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 7 : 1;
    if (e.key === "ArrowLeft") setPicked(Math.max(0, sel - step));
    else if (e.key === "ArrowRight") setPicked(Math.min(days.length - 1, sel + step));
    else if (e.key === "Home") setPicked(0);
    else if (e.key === "End") setPicked(todayI);
    else if (e.key === "Escape") { setPicked(null); setReading(false); return; }
    else return;
    e.preventDefault();
    setPointerY(null);
    setReading(true);
  };

  // a label every three days, a week on a phone, the month named where it starts
  const every = narrow ? 7 : 3;
  const ticks = days.filter((_, i) => i % every === 0);
  const tickLabel = (iso: string, i: number) => {
    const dt = new Date(`${iso}T12:00:00Z`);
    return i === 0 || dt.getUTCDate() <= every
      ? dt.toLocaleDateString("en-GB", { timeZone: "UTC", day: "numeric", month: "short" })
      : String(dt.getUTCDate());
  };
  const gx = d ? x((d.from + d.to) / 2) : 0;
  const hhmm = (ms: number) => new Date(ms).toLocaleDateString("en-GB", { timeZone: c.tz, day: "numeric", month: "short" });

  return (
    <div className="weekline monthline" ref={rootRef}>
      {d && (
      <div className="readout" aria-live="polite" ref={boxRef} data-show={reading ? "true" : "false"}>
        <div className="ro-when">{dayName(d.day)}{d.current ? " (today)" : ""}</div>
        <div className="ro-value">{val === null ? "—" : `${future ? "≈ " : ""}${val.toFixed(0)}%`}</div>
        <div className="ro-line">
          {future ? (val !== null ? <>at this month's rate</> : <>no projection yet</>)
            : d.current ? <>last read {new Date(readMs).toLocaleTimeString("en-GB", { timeZone: c.tz, hour: "2-digit", minute: "2-digit" })}</>
            : <>by the end of the day</>}
          {" · "}even pace <b>{pace.toFixed(0)}%</b>
        </div>
        {delta !== null && (
          <div className={`ro-pace ${delta > 2 ? "over" : delta < -2 ? "under" : ""}`}>
            {Math.abs(delta) <= 2 ? "on pace" : `${Math.abs(delta).toFixed(0)} pts ${delta > 0 ? "ahead" : "behind"}`}
          </div>
        )}
        <ul className="ro-list">
          {future ? (
            <>
              <li className="ro-sub"><span className="ro-name">credits so far</span><span className="ro-val">{c.used == null ? "—" : fmtInt(c.used)}</span></li>
              <li className="ro-sub"><span className="ro-name">the month's limit</span><span className="ro-val">{fmtInt(limit)}</span></li>
            </>
          ) : (
            <>
              <li className="ro-sub"><span className="ro-name">credits that day</span><span className="ro-val">{fmtInt(d.used)}</span></li>
              <li className="ro-sub"><span className="ro-name">credits so far</span><span className="ro-val">{fmtInt(d.current && c.used != null ? c.used : cum[sel])}</span></li>
            </>
          )}
        </ul>
      </div>
      )}

      <div className="weekline-plot" ref={plotRef} role="group" tabIndex={0}
           aria-label="The credits month. Left and right arrows move a day, with Shift a week."
           onPointerMove={pickAt} onPointerDown={pickAt} onPointerLeave={leave}
           onFocus={() => setReading(true)} onBlur={() => leave()} onKeyDown={onKey}>
        <svg className="quota-line" viewBox={`0 0 ${W} ${H}`} role="img"
             aria-label={`The credits month: ${usedPct === null ? "no figure yet" : `${usedPct.toFixed(0)}% used`}, ${w.elapsed_pct.toFixed(0)}% of it gone`}>
          {[0, 25, 50, 75, 100].map((v) => (
            <g key={v}>
              <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className={v === 100 ? "ql-limit" : "ql-grid"} />
              <text x={L - 6} y={y(v) + 4} textAnchor="end" className="ql-tick">{v}%</text>
            </g>
          ))}
          {ticks.map((t) => {
            const i = days.indexOf(t);
            return <text key={t.day} x={x((t.from + t.to) / 2)} y={H - 8} className="ql-tick" textAnchor="middle">{tickLabel(t.day, i)}</text>;
          })}
          {reading && d && <rect x={x(d.from)} y={T} width={Math.max(1, x(d.to) - x(d.from))} height={H - T - B} className="ql-hour" />}
          <line x1={x(start)} y1={y(0)} x2={x(reset)} y2={y(100)} className="ql-pace" />
          {projEndPct !== null && usedPct !== null && (
            <line x1={x(readMs)} y1={y(usedPct)} x2={x(projEnd)} y2={y(projEndPct)} className="ql-proj" />
          )}
          <path d={used} className="ql-used" />
          <line x1={x(nowMs)} x2={x(nowMs)} y1={T} y2={H - B} className="ql-now" />
          {usedPct !== null && <rect x={x(readMs) - 4} y={y(usedPct) - 4} width="8" height="8" className="ql-dot" />}
          {reading && d && (
            <g className="ql-pick">
              <line x1={gx} x2={gx} y1={T} y2={H - B} />
              <rect x={gx - 3.5} y={y(pace) - 3.5} width="7" height="7" className="ql-pick-pace" />
              {val !== null && <rect x={gx - 4.5} y={y(val) - 4.5} width="9" height="9" className="ql-pick-used" />}
            </g>
          )}
        </svg>
      </div>
      <ul className="weekline-key" aria-hidden="true">
        <li><i className="k-used" />% used, from ElevenLabs' daily counts</li>
        <li><i className="k-pace" />an even month</li>
        {projEndPct !== null && <li><i className="k-proj" />at this rate{runsOut ? `, 100% on ${hhmm(runsOut)}` : ""}</li>}
      </ul>
    </div>
  );
}
