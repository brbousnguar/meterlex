import { useEffect, useRef, useState } from "react";

/** The reading, drawn like the drum of a gas meter: one digit per cell,
 *  hairline-separated, thin gaps every three digits. Leading zeros are kept —
 *  a meter has a fixed number of drums — but drawn in the muted ink so the
 *  significant digits still read first. */
const SCALE = ["", "K", "M", "B", "T"];

export default function Odometer({
  value, unit, digits = 0, small = false, cents = false, short,
}: {
  value: number; unit?: string; digits?: number; small?: boolean; cents?: boolean;
  /** The rounded reading (2.28B): what the eye takes in before it reads a drum. */
  short?: string | null;
}) {
  const shown = useCount(value);
  // Euros keep their cents on two muted drums: a reading of €0,86 must not say 1.
  const whole = Math.max(0, cents ? Math.floor(shown) : Math.round(shown));
  const text = String(whole);
  const padded = digits > text.length ? text.padStart(digits, "0") : text;
  const lead = padded.length - text.length;

  const cells: React.ReactNode[] = [];
  padded.split("").forEach((ch, i) => {
    const fromEnd = padded.length - i;
    if (i > 0 && fromEnd % 3 === 0) cells.push(<i className="odo-gap" key={`g${i}`} />);
    // The unit plate of a meter: the drum worth a thousand, a million, a billion is marked.
    // Money is read in full — nobody reads €1 460 as 1.4K — so only a count gets the plate.
    const mark = !small && !cents && fromEnd > 1 && fromEnd % 3 === 1 ? SCALE[(fromEnd - 1) / 3] : undefined;
    cells.push(
      <span className="odo-cell" data-lead={i < lead ? "true" : "false"} data-scale={mark || undefined} key={i}>{ch}</span>,
    );
  });

  if (cents) {
    cells.push(<i className="odo-dot" key="dot" />);
    const frac = String(Math.min(99, Math.round((shown - whole) * 100))).padStart(2, "0");
    [...frac].forEach((ch, i) => cells.push(
      <span className="odo-cell" data-cents="true" key={`c${i}`}>{ch}</span>,
    ));
  }

  const drums = padded.length + (cents ? 2.5 : 0);
  const spoken = value.toLocaleString("fr-FR", cents ? { minimumFractionDigits: 2, maximumFractionDigits: 2 } : {});
  const plate = !small && !cents && padded.length > 3;
  return (
    <div className={`odo${small ? " odo-small" : ""}`} role="img" data-plate={plate ? "true" : undefined}
         style={{ ["--drums" as any]: drums }} aria-label={`${spoken}${unit ? ` ${unit}` : ""}`}>
      {cells}
      {(unit || short) && (
        <span className="odo-unit" aria-hidden="true">
          {short && <b className="odo-short">{short}</b>}
          {unit}
        </span>
      )}
    </div>
  );
}

/** Counts up to the value once, the way a meter catches up — and skips the
 *  animation entirely when the reader asked for less motion. */
function useCount(target: number) {
  const [n, setN] = useState(target);
  const from = useRef(target);
  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || Math.abs(target - from.current) < 2) { from.current = target; setN(target); return; }
    const start = performance.now(), a = from.current, span = 620;
    let raf = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / span);
      const eased = 1 - Math.pow(1 - p, 3);
      setN(a + (target - a) * eased);
      if (p < 1) raf = requestAnimationFrame(tick); else from.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target]);
  return n;
}
