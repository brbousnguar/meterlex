import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { Quota } from "../api";
import { fmtTok } from "../lib";

const HOUR = 3600 * 1000;
const HOURS = 7 * 24;
const WEEK_MS = HOURS * HOUR;
const L = 34, R = 10, T = 10, B = 26;

/**
 * The week's quota against an even pace. The percentage is only known when a Claude Code session
 * reads it, so the thick line joins the readings and the thin one estimates the hours between them
 * from tokens (tokens so far ÷ the week's budget). Like the bar charts, the whole plot is the hit
 * target: pointing at an hour shows a readout beside the pointer (a panel above the plot on a
 * phone), and it leaves with the pointer. ← → move an hour, Shift ← → a day, Home End, Esc clears.
 */
export default function WeekLine({ q }: { q: Quota }) {
  const start = Date.parse(q.window.start);
  const now = Date.parse(q.now);
  const nowH = Math.min(HOURS - 1, Math.max(0, Math.floor((now - start) / HOUR)));
  const [picked, setPicked] = useState<number | null>(null);
  const [pointerY, setPointerY] = useState<number | null>(null);
  const [reading, setReading] = useState(false);
  const sel = picked ?? nowH;

  const rootRef = useRef<HTMLDivElement>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  // Drawn at the plot's own pixel width, so its type stays 11px on a phone instead of shrinking.
  const [width, setWidth] = useState(700);
  useEffect(() => {
    const el = plotRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.round(el.clientWidth) || 700));
    ro.observe(el);
    return () => ro.disconnect();
  }, [q.anchored]);
  const W = width, H = width < 520 ? 200 : 230;

  // ── the series ─────────────────────────────────────────────────────────────
  const top = Math.max(100, ...q.history.map((h) => h.pct), q.pace?.projected_pct ?? 0);
  const yMax = top > 100 ? Math.ceil(top / 25) * 25 : 100;
  const x = (ms: number) => L + ((ms - start) / WEEK_MS) * (W - L - R);
  const y = (v: number) => T + (1 - Math.min(v, yMax) / yMax) * (H - T - B);
  const pts = [{ ms: start, pct: 0 }, ...q.history.map((h) => ({ ms: Date.parse(h.at), pct: h.pct }))];
  if (q.used_pct !== null) pts.push({ ms: now, pct: q.used_pct });
  const used = pts.map((p, i) => `${i ? "L" : "M"}${x(p.ms)},${y(p.pct)}`).join("");

  const cum: number[] = [];
  q.hourly.reduce((acc, v, i) => (cum[i] = acc + v), 0);
  const budget = q.tokens.budget;
  const est = budget
    ? [`M${x(start)},${y(0)}`, ...cum.slice(0, nowH + 1).map((c, i) =>
        `L${x(Math.min(now, start + (i + 1) * HOUR))},${y((c / budget) * 100)}`)].join("")
    : null;

  // ── the picked hour ────────────────────────────────────────────────────────
  const hStart = start + sel * HOUR, hEnd = hStart + HOUR;
  const at = Math.min(hEnd, now);
  const future = hStart >= now;
  const pace = ((hEnd - start) / WEEK_MS) * 100;
  const known = [...q.history].reverse().find((h) => Date.parse(h.at) <= hEnd) ?? null;
  const soFar = future ? null : cum[sel] ?? 0;
  const estPct = budget && soFar !== null ? (soFar / budget) * 100 : null;
  const projAt = q.pace?.projected_pct != null && q.used_pct !== null
    ? q.used_pct + (q.pace.projected_pct - q.used_pct) * ((hEnd - now) / Math.max(1, start + WEEK_MS - now))
    : null;
  const ref = future ? projAt : known?.pct ?? (estPct ?? null);
  const delta = ref !== null ? ref - pace : null;
  const markY = future ? projAt : (known && hEnd - Date.parse(known.at) < 3 * HOUR ? known.pct : estPct ?? known?.pct ?? null);

  const tz = q.tz;
  const when = new Date(hStart).toLocaleString("en-GB", { timeZone: tz, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const until = new Date(hEnd).toLocaleTimeString("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit" });
  const readAt = known ? new Date(known.at).toLocaleString("en-GB", { timeZone: tz, weekday: "short", hour: "2-digit", minute: "2-digit" }) : "";

  useLayoutEffect(() => {
    const root = rootRef.current, box = boxRef.current, plot = plotRef.current;
    if (!root || !box || !plot) return;
    if (window.matchMedia("(max-width: 719px)").matches) { box.style.left = ""; box.style.top = ""; return; }
    const r = root.getBoundingClientRect(), p = plot.getBoundingClientRect();
    const gx = p.left - r.left + (x((hStart + hEnd) / 2) / W) * p.width;
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
    setPicked(Math.max(0, Math.min(HOURS - 1, Math.floor(frac * HOURS))));
  };
  const leave = (e?: PointerEvent<HTMLDivElement>) => {
    if (e && e.pointerType === "touch") return;   // a finger lifting is not the pointer leaving
    setReading(false);
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 24 : 1;
    if (e.key === "ArrowLeft") setPicked(Math.max(0, sel - step));
    else if (e.key === "ArrowRight") setPicked(Math.min(HOURS - 1, sel + step));
    else if (e.key === "Home") setPicked(0);
    else if (e.key === "End") setPicked(nowH);
    else if (e.key === "Escape") { setPicked(null); setReading(false); return; }
    else return;
    e.preventDefault();
    setPointerY(null);
    setReading(true);
  };

  if (!q.anchored) return <div className="empty">The line appears with the first quota reading.</div>;
  const ticks = yMax > 100 ? [0, 50, 100, yMax] : [0, 25, 50, 75, 100];
  const gx = x((hStart + hEnd) / 2);

  return (
    <div className="weekline" ref={rootRef}>
      <div className="readout" aria-live="polite" ref={boxRef} data-show={reading ? "true" : "false"}>
        <div className="ro-when">{when}–{until}{!future && hEnd > now ? " (now)" : ""}</div>
        {future ? (
          <>
            <div className="ro-value">{projAt !== null ? `≈ ${projAt.toFixed(0)}%` : "—"}</div>
            <div className="ro-line">at this week's rate · even pace <b>{pace.toFixed(0)}%</b></div>
          </>
        ) : (
          <>
            <div className="ro-value">{known ? `${known.pct.toFixed(0)}%` : estPct !== null ? `≈ ${estPct.toFixed(0)}%` : "—"}</div>
            <div className="ro-line">
              {known ? <>last read {readAt}</> : estPct !== null ? <>estimated from tokens</> : <>no reading yet</>}
              {" · "}even pace <b>{pace.toFixed(0)}%</b>
            </div>
          </>
        )}
        {delta !== null && (
          <div className={`ro-pace ${delta > 2 ? "over" : delta < -2 ? "under" : ""}`}>
            {Math.abs(delta) <= 2 ? "on pace" : `${Math.abs(delta).toFixed(0)} pts ${delta > 0 ? "ahead" : "behind"}`}
          </div>
        )}
        {future && (
          <ul className="ro-list">
            <li className="ro-sub"><span className="ro-name">tokens so far</span><span className="ro-val">{fmtTok(cum[nowH] ?? 0)}</span></li>
            <li className="ro-sub"><span className="ro-name">the week's budget</span><span className="ro-val">{budget ? `≈ ${fmtTok(budget)}` : "—"}</span></li>
          </ul>
        )}
        {!future && (
          <ul className="ro-list">
            <li className="ro-sub"><span className="ro-name">tokens this hour</span><span className="ro-val">{fmtTok(q.hourly[sel] ?? 0)}</span></li>
            <li className="ro-sub"><span className="ro-name">tokens so far</span><span className="ro-val">{fmtTok(soFar ?? 0)}</span></li>
            {estPct !== null && known && (
              <li className="ro-sub"><span className="ro-name">from tokens</span><span className="ro-val">≈ {estPct.toFixed(0)}%</span></li>
            )}
          </ul>
        )}
      </div>

      <div className="weekline-plot" ref={plotRef} role="group" tabIndex={0}
           aria-label="Quota over the week. Left and right arrows move an hour, with Shift a day."
           onPointerMove={pickAt} onPointerDown={pickAt} onPointerLeave={leave}
           onFocus={() => setReading(true)} onBlur={() => leave()} onKeyDown={onKey}>
        <svg className="quota-line" viewBox={`0 0 ${W} ${H}`} role="img"
             aria-label={`Quota used over the week: ${q.used_pct?.toFixed(0)}% now, ${q.window.elapsed_pct.toFixed(0)}% of the week gone`}>
          {ticks.map((v) => (
            <g key={v}>
              <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className={v === 100 ? "ql-limit" : "ql-grid"} />
              <text x={L - 6} y={y(v) + 4} textAnchor="end" className="ql-tick">{v}%</text>
            </g>
          ))}
          {q.days.map((dd, i) => (
            <text key={dd.start} x={x(start + (i + 0.5) * WEEK_MS / 7)} y={H - 8} textAnchor="middle" className="ql-tick">
              {new Date(dd.start).toLocaleDateString("en-GB", { timeZone: tz, weekday: "short" })}
            </text>
          ))}
          {reading && <rect x={x(hStart)} y={T} width={Math.max(1, x(hEnd) - x(hStart))} height={H - T - B} className="ql-hour" />}
          <line x1={x(start)} y1={y(0)} x2={x(start + WEEK_MS)} y2={y(100)} className="ql-pace" />
          {est && <path d={est} className="ql-est" />}
          {q.pace?.projected_pct != null && q.used_pct !== null && (
            <line x1={x(now)} y1={y(q.used_pct)} x2={x(start + WEEK_MS)} y2={y(q.pace.projected_pct)} className="ql-proj" />
          )}
          <path d={used} className="ql-used" />
          {q.history.map((h) => (
            <rect key={h.at} x={x(Date.parse(h.at)) - 3} y={y(h.pct) - 3} width="6" height="6" className="ql-read" />
          ))}
          <line x1={x(now)} x2={x(now)} y1={T} y2={H - B} className="ql-now" />
          {q.used_pct !== null && <rect x={x(now) - 4} y={y(q.used_pct) - 4} width="8" height="8" className="ql-dot" />}
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
        {est && <li><i className="k-est" />estimated from tokens</li>}
        <li><i className="k-pace" />an even week</li>
        {q.pace?.projected_pct != null && <li><i className="k-proj" />at this rate</li>}
      </ul>
    </div>
  );
}
