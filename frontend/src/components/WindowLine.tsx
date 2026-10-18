import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { FiveHourOpen } from "../api";
import { fmtTok } from "../lib";

const MIN = 60 * 1000;
const HOUR = 60 * MIN;
const SLOT = 10 * MIN;                    // the readout reads ten minutes at a time
const SLOTS = 30;                         // five hours of them
const SPAN = SLOTS * SLOT;
const L = 34, T = 14, B = 26;

/**
 * The open 5-hour window against an even pace, on the week line's pattern: the thick line joins
 * the readings, the dashed diagonal is an even five hours, and the dotted line is "at this rate"
 * from now to the reset, or to where it hits 100%. The axis is clock time, every hour from the
 * window's start. Like the week line, the whole plot is the hit target: pointing at it reads any
 * ten minutes beside the pointer (a panel above the plot on a phone), and the readout leaves with
 * it. ← → move ten minutes, Shift ← → an hour, Home End, Esc clears.
 */
export default function WindowLine({ open, now, tz }: { open: FiveHourOpen; now: string; tz: string }) {
  const start = Date.parse(open.start);
  const nowMs = Math.min(start + SPAN, Date.parse(now));
  const nowS = Math.min(SLOTS - 1, Math.max(0, Math.floor((nowMs - start) / SLOT)));
  const [picked, setPicked] = useState<number | null>(null);
  const [pointerY, setPointerY] = useState<number | null>(null);
  const [reading, setReading] = useState(false);
  const sel = picked ?? nowS;

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
  // on a phone every hour label is centred on its hour, so the last one needs room past the plot
  const W = width, H = narrow ? 176 : 204, R = narrow ? 24 : 10;

  // ── the series ─────────────────────────────────────────────────────────────
  const x = (ms: number) => L + ((ms - start) / SPAN) * (W - L - R);
  const y = (v: number) => T + (1 - Math.min(v, 100) / 100) * (H - T - B);
  const hist = open.history.map((h) => ({ ms: Date.parse(h.at), pct: h.pct }));
  const pts = [{ ms: start, pct: 0 }, ...hist, { ms: nowMs, pct: open.used_pct }];
  // a drop inside the window is a step, not a slope (a plan change rescales the figure)
  const used = pts.map((p, i) => i === 0 ? `M${x(p.ms)},${y(p.pct)}`
    : p.pct < pts[i - 1].pct ? `L${x(p.ms)},${y(pts[i - 1].pct)}L${x(p.ms)},${y(p.pct)}`
    : `L${x(p.ms)},${y(p.pct)}`).join("");

  // "at this rate", from now to the reset, or to where the window hits 100%
  const proj = open.pace.projected_pct;
  const runsOut = open.pace.runs_out_at ? Date.parse(open.pace.runs_out_at) : null;
  const projEnd = runsOut ?? start + SPAN;
  const projAt = (ms: number) => proj !== null
    ? Math.min(100, open.used_pct + (proj - open.used_pct) * ((ms - nowMs) / Math.max(1, start + SPAN - nowMs)))
    : null;

  const cum: number[] = [];
  open.slots.reduce((acc, v, i) => (cum[i] = acc + v), 0);

  // ── the picked ten minutes ─────────────────────────────────────────────────
  const sStart = start + sel * SLOT, sEnd = sStart + SLOT;
  const future = sStart >= nowMs;
  const pace = ((sEnd - start) / SPAN) * 100;
  const known = [...hist].reverse().find((h) => h.ms <= sEnd) ?? null;
  // a capped window stays at 100% to its reset: nothing to project
  const cappedAt = open.capped_at ? Date.parse(open.capped_at) : null;
  const pAt = future ? (cappedAt !== null ? 100 : projAt(sEnd)) : null;
  const ref = future ? pAt : known?.pct ?? null;
  const delta = ref !== null ? ref - pace : null;
  const markY = future ? pAt : known?.pct ?? null;

  const hhmm = (ms: number) => new Date(ms).toLocaleTimeString("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit" });

  useLayoutEffect(() => {
    const root = rootRef.current, box = boxRef.current, plot = plotRef.current;
    if (!root || !box || !plot) return;
    if (window.matchMedia("(max-width: 719px)").matches) { box.style.left = ""; box.style.top = ""; return; }
    const r = root.getBoundingClientRect(), p = plot.getBoundingClientRect();
    const gx = p.left - r.left + (x((sStart + sEnd) / 2) / W) * p.width;
    const w = box.offsetWidth, h = box.offsetHeight, gap = 18;
    let left = gx + gap;
    if (left + w > r.width) left = gx - gap - w;
    const anchor = pointerY ?? p.top - r.top + p.height / 2;
    box.style.left = `${Math.round(Math.max(0, left))}px`;
    box.style.top = `${Math.round(Math.max(0, Math.min(anchor - h / 2, r.height - h)))}px`;
  });

  const pickAt = (e: PointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const frac = ((e.clientX - rect.left) / rect.width * W - L) / (W - L - R);
    setReading(true);
    setPointerY(e.clientY - (rootRef.current?.getBoundingClientRect().top ?? 0));
    setPicked(Math.max(0, Math.min(SLOTS - 1, Math.floor(frac * SLOTS))));
  };
  const leave = (e?: PointerEvent<HTMLDivElement>) => {
    if (e && e.pointerType === "touch") return;   // a finger lifting is not the pointer leaving
    setReading(false);
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 6 : 1;
    if (e.key === "ArrowLeft") setPicked(Math.max(0, sel - step));
    else if (e.key === "ArrowRight") setPicked(Math.min(SLOTS - 1, sel + step));
    else if (e.key === "Home") setPicked(0);
    else if (e.key === "End") setPicked(nowS);
    else if (e.key === "Escape") { setPicked(null); setReading(false); return; }
    else return;
    e.preventDefault();
    setPointerY(null);
    setReading(true);
  };

  const gx = x((sStart + sEnd) / 2);
  const projEndPct = runsOut ? 100 : proj;

  return (
    <div className="weekline windowline" ref={rootRef}>
      <div className="readout" aria-live="polite" ref={boxRef} data-show={reading ? "true" : "false"}>
        <div className="ro-when">{hhmm(sStart)}–{hhmm(sEnd)}{!future && sEnd > nowMs ? " (now)" : ""}</div>
        <div className="ro-value">
          {future ? (cappedAt !== null ? "100%" : pAt !== null ? `≈ ${pAt.toFixed(0)}%` : "—") : known ? `${known.pct.toFixed(0)}%` : "—"}
        </div>
        <div className="ro-line">
          {future ? (cappedAt !== null ? <>capped at {hhmm(cappedAt)}</> : pAt !== null ? <>at this window's rate</> : <>no projection yet</>)
            : known ? <>last read {hhmm(known.ms)}</> : <>no reading yet</>}
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
              <li className="ro-sub"><span className="ro-name">tokens so far</span><span className="ro-val">{fmtTok(cum[nowS] ?? 0)}</span></li>
              <li className="ro-sub"><span className="ro-name">the window's budget</span><span className="ro-val">{open.tokens.budget ? `≈ ${fmtTok(open.tokens.budget)}` : "—"}</span></li>
            </>
          ) : (
            <>
              <li className="ro-sub"><span className="ro-name">tokens these ten minutes</span><span className="ro-val">{fmtTok(open.slots[sel] ?? 0)}</span></li>
              <li className="ro-sub"><span className="ro-name">tokens so far</span><span className="ro-val">{fmtTok(cum[sel] ?? 0)}</span></li>
            </>
          )}
        </ul>
      </div>

      <div className="weekline-plot" ref={plotRef} role="group" tabIndex={0}
           aria-label="The 5-hour window. Left and right arrows move ten minutes, with Shift an hour."
           onPointerMove={pickAt} onPointerDown={pickAt} onPointerLeave={leave}
           onFocus={() => setReading(true)} onBlur={() => leave()} onKeyDown={onKey}>
        <svg className="quota-line" viewBox={`0 0 ${W} ${H}`} role="img"
             aria-label={`The 5-hour window: ${open.used_pct.toFixed(0)}% used, ${open.elapsed_pct.toFixed(0)}% of it gone`}>
          {[0, 25, 50, 75, 100].map((v) => (
            <g key={v}>
              <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className={v === 100 ? "ql-limit" : "ql-grid"} />
              <text x={L - 6} y={y(v) + 4} textAnchor="end" className="ql-tick">{v}%</text>
            </g>
          ))}
          {Array.from({ length: 6 }, (_, i) => (
            <text key={i} x={x(start + i * HOUR)} y={H - 8} className="ql-tick"
                  textAnchor={narrow ? "middle" : i === 0 ? "start" : i === 5 ? "end" : "middle"}>
              {hhmm(start + i * HOUR)}
            </text>
          ))}
          {reading && <rect x={x(sStart)} y={T} width={Math.max(1, x(sEnd) - x(sStart))} height={H - T - B} className="ql-hour" />}
          <line x1={x(start)} y1={y(0)} x2={x(start + SPAN)} y2={y(100)} className="ql-pace" />
          {projEndPct !== null && (
            <line x1={x(nowMs)} y1={y(open.used_pct)} x2={x(projEnd)} y2={y(projEndPct)} className="ql-proj" />
          )}
          <path d={used} className="ql-used" />
          <line x1={x(nowMs)} x2={x(nowMs)} y1={T} y2={H - B} className="ql-now" />
          <rect x={x(nowMs) - 4} y={y(open.used_pct) - 4} width="8" height="8" className="ql-dot" />
          {reading && (
            <g className="ql-pick">
              <line x1={gx} x2={gx} y1={T} y2={H - B} />
              <rect x={gx - 3.5} y={y(pace) - 3.5} width="7" height="7" className="ql-pick-pace" />
              {markY !== null && <rect x={gx - 4.5} y={y(markY) - 4.5} width="9" height="9" className="ql-pick-used" />}
            </g>
          )}
        </svg>
      </div>
      <ul className="weekline-key" aria-hidden="true">
        <li><i className="k-used" />% used, from readings</li>
        <li><i className="k-pace" />an even five hours</li>
        {projEndPct !== null && <li><i className="k-proj" />at this rate{runsOut ? `, 100% at ${hhmm(runsOut)}` : ""}</li>}
      </ul>
    </div>
  );
}
