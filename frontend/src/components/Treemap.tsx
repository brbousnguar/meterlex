import { useEffect, useLayoutEffect, useRef, useState } from "react";

export type Tile = {
  key: string;
  label: string;        // short name on the tile
  value: number;        // area, in the current unit
  valueText: string;
  fill: string;         // the harness that did most of the work there
  rows: [string, string][];  // the hover readout
};

type Rect = { x: number; y: number; w: number; h: number };

/** Squarified treemap (Bruls, Huizing, van Wijk): tiles as close to square as the areas allow. */
function squarify(areas: number[], box: Rect): Rect[] {
  const out: Rect[] = [];
  let { x, y, w, h } = box;
  let row: number[] = [];
  const worst = (r: number[], side: number) => {
    const sum = r.reduce((a, b) => a + b, 0);
    return Math.max((side * side * Math.max(...r)) / (sum * sum), (sum * sum) / (side * side * Math.min(...r)));
  };
  const lay = (r: number[]) => {
    const sum = r.reduce((a, b) => a + b, 0);
    if (w >= h) {
      const cw = sum / h;
      let cy = y;
      for (const a of r) { out.push({ x, y: cy, w: cw, h: a / cw }); cy += a / cw; }
      x += cw; w -= cw;
    } else {
      const rh = sum / w;
      let cx = x;
      for (const a of r) { out.push({ x: cx, y, w: a / rh, h: rh }); cx += a / rh; }
      y += rh; h -= rh;
    }
  };
  let i = 0;
  while (i < areas.length) {
    const side = Math.min(w, h);
    if (!row.length || worst([...row, areas[i]], side) <= worst(row, side)) { row.push(areas[i]); i++; }
    else { lay(row); row = []; }
  }
  if (row.length) lay(row);
  return out;
}

/**
 * Where the work happened, drawn as a map: the area of a tile is what that folder cost or
 * consumed, and its colour is the harness that did most of the work there — colour is harness
 * identity here as everywhere else. Every tile is also a row in the list underneath, so nothing
 * depends on reading the map.
 */
export default function Treemap({ tiles, height = 300 }: { tiles: Tile[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [tip, setTip] = useState<{ x: number; y: number; t: Tile } | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  useEffect(() => { if (!tip) return; const off = () => setTip(null); window.addEventListener("scroll", off, true); return () => window.removeEventListener("scroll", off, true); }, [tip]);

  const list = tiles.filter((t) => t.value > 0);
  const total = list.reduce((a, t) => a + t.value, 0);
  if (!list.length || !total) return <div className="empty">Nothing recorded in this period.</div>;

  const rects = width ? squarify(list.map((t) => (t.value / total) * width * height), { x: 0, y: 0, w: width, h: height }) : [];

  return (
    <div className="tmap" ref={ref} style={{ height }}>
      {rects.map((r, i) => {
        const t = list[i];
        const size = r.w >= 150 && r.h >= 92 ? "l" : r.w >= 96 && r.h >= 58 ? "m" : r.w >= 58 && r.h >= 36 ? "s" : "xs";
        return (
          <div className="tile" key={t.key} data-size={size}
               style={{ left: r.x, top: r.y, width: Math.max(0, r.w - 2), height: Math.max(0, r.h - 2), background: t.fill }}
               aria-label={`${t.label}: ${t.valueText}`}
               onPointerMove={(e) => e.pointerType === "mouse" && setTip({ x: e.clientX, y: e.clientY, t })}
               onPointerLeave={() => setTip(null)}>
            <span className="k">{t.label}</span>
            {size !== "xs" && <span className="v">{t.valueText}</span>}
          </div>
        );
      })}
      {tip && (
        <div className="tip tip-float" style={{ left: Math.min(tip.x + 14, (width || 0) + 40), top: tip.y + 14 }}>
          <div className="tip-head">{tip.t.label}</div>
          {tip.t.rows.map(([k, v]) => (
            <div className="tip-row" key={k}>{k}<span>{v}</span></div>
          ))}
        </div>
      )}
    </div>
  );
}
