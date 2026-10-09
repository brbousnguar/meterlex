import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { api, type Overview, type Period } from "./api";
import { PERIODS, periodLabel, stepRef, UNITS, windowLabel, type Unit } from "./lib";
import Now from "./screens/Now";
import Machines from "./screens/Machines";
import Harnesses from "./screens/Harnesses";
import Models from "./screens/Models";
import More from "./screens/More";
import Quota from "./screens/Quota";

export type Tab = "now" | "quota" | "machines" | "harnesses" | "models" | "more";

/* Every icon is cut square, like the rest of the app. `title` heads the page on a desktop. */
const TABS: { id: Tab; label: string; title: string; icon: JSX.Element }[] = [
  { id: "now",       label: "Now",      title: "The reading", icon: <Icon d="M3 17h4V8H3v9Zm7 0h4V4h-4v13Zm7 0h4v-6h-4v6Z" /> },
  { id: "quota",     label: "Quota",    title: "Claude Max quota", icon: <Icon d="M3 4h18v6H3V4Zm2 2v2h8V6H5Zm-2 8h18v6H3v-6Zm2 2v2h4v-2H5Z" /> },
  { id: "machines",  label: "Machines", title: "Machines",    icon: <Icon d="M4 4h16v10H4V4Zm2 2v6h12V6H6ZM8 18h8v2H8v-2Z" /> },
  { id: "harnesses", label: "Harness",  title: "Harnesses",   icon: <Icon d="M4 4h7v16H4V4Zm9 0h7v7h-7V4Zm0 9h7v7h-7v-7Z" /> },
  { id: "models",    label: "Models",   title: "Models",      icon: <Icon d="M12 2 3 7l9 5 9-5-9-5Zm0 20 9-5v-5l-9 5-9-5v5l9 5Z" /> },
  { id: "more",      label: "More",     title: "Rates and collectors", icon: <Icon d="M3 10h4v4H3v-4Zm7 0h4v4h-4v-4Zm7 0h4v4h-4v-4Z" /> },
];

/* The header mark is the app icon (public/icon.svg), drawn from the theme tokens:
   the clay tile of the Atlas mark, a dial cut from paper in six facets, four of
   them read, and an ink needle over its butter shadow. */
function Mark() {
  return (
    <svg className="brand-mark" viewBox="0 0 512 512" aria-hidden="true">
      <rect width="512" height="512" fill="var(--accent)" />
      <g fill="var(--surface)">
        <polygon points="96,378 76,266 147,276 159,344" />
        <polygon points="78,255 135,156 183,210 148,270" />
        <polygon points="143,149 250,110 252,182 188,206" />
        <polygon points="262,110 369,149 324,206 260,182" />
      </g>
      <g fill="var(--h-claude-tile)">
        <polygon points="377,156 434,255 364,270 329,210" />
        <polygon points="436,266 416,378 353,344 365,276" />
      </g>
      <polygon points="365,190 248,287 247,331 290,323" fill="var(--butter)" />
      <polygon points="352,177 235,274 234,318 277,310" fill="var(--ink)" />
    </svg>
  );
}

/** Moves one thumb to the current child of a control, so a switch slides instead of
 *  jumping. The thumb is an element that stays: rebuild it and it cannot slide. */
function useThumb<T extends HTMLElement>(current: string) {
  const box = useRef<T>(null);
  const thumb = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    const el = box.current, t = thumb.current;
    if (!el || !t) return;
    const place = () => {
      const on = el.querySelector<HTMLElement>('[aria-pressed="true"], [aria-current="page"]');
      if (!on) return;
      t.style.width = `${on.offsetWidth}px`;
      t.style.transform = `translateX(${on.offsetLeft}px)`;
      el.setAttribute("data-ready", "");
    };
    place();
    // Web fonts and a rotated phone both move the buttons under the thumb.
    const ro = new ResizeObserver(place);
    ro.observe(el);
    el.querySelectorAll("button").forEach((b) => ro.observe(b));
    return () => ro.disconnect();
  }, [current]);
  return { box, thumb };
}

/** A segmented control: a paper well and a card thumb that slides to the pressed option. */
function Seg<T extends string>({ label, value, options, onPick }: {
  label: string; value: T; options: { id: T; short: ReactNode }[]; onPick: (id: T) => void;
}) {
  const { box, thumb } = useThumb<HTMLDivElement>(value);
  return (
    <div className="seg" role="group" aria-label={label} ref={box}>
      <span className="seg-thumb" ref={thumb} aria-hidden="true" />
      {options.map((o) => (
        <button key={o.id} aria-pressed={value === o.id} onClick={() => onPick(o.id)}>{o.short}</button>
      ))}
    </div>
  );
}

function Icon({ d }: { d: string }) {
  return <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d={d} /></svg>;
}

/* localStorage is a convenience here: a private window or blocked site data
   must not stop the app rendering, so every access is guarded. */
const remember = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } };
const recall = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };

export default function App() {
  const [tab, setTab] = useState<Tab>(() => (recall("mx.tab") as Tab) || "now");
  const [period, setPeriod] = useState<Period>(() => (recall("mx.period") as Period) || "weekly");
  const [unit, setUnit] = useState<Unit>(() => (recall("mx.unit") as Unit) || "tokens");
  const [ref, setRef] = useState<string | null>(null);      // null = the current period
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tabs = useThumb<HTMLElement>(tab);

  const load = useCallback(() => {
    api.overview(period, ref)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(String(e.message ?? e)));
  }, [period, ref]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {                       // collectors report every 5 minutes
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  const go = (t: Tab) => { setTab(t); remember("mx.tab", t); window.scrollTo(0, 0); };
  const pick = (p: Period) => { setPeriod(p); setRef(null); remember("mx.period", p); };
  const pickUnit = (u: Unit) => { setUnit(u); remember("mx.unit", u); };
  const step = (delta: number) => {
    const next = stepRef(period, ref, delta);
    const current = stepRef(period, null, 0);
    setRef(next === current ? null : next);
  };

  const isCurrent = ref === null;
  const label = data ? periodLabel(period, data.from_local, isCurrent) : "…";

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-row">
          <div className="brand">
            <Mark />
            <div>
              <div className="brand-name">Meterlex</div>
              <div className="brand-kicker">every token, metered</div>
            </div>
          </div>
          {/* On a phone this is the bar fixed to the foot of the screen. */}
          <nav className="tabs" aria-label="Sections" ref={tabs.box}>
            <span className="tabs-thumb" ref={tabs.thumb} aria-hidden="true" />
            {TABS.map((t) => (
              <button className="tab" key={t.id} data-testid={`tab-${t.id}`}
                      aria-current={tab === t.id ? "page" : undefined} onClick={() => go(t.id)}>
                {t.icon}{t.label}
              </button>
            ))}
          </nav>
          <button className="icon-btn" onClick={load} aria-label="Read the meters again" title="Refresh">↻</button>
        </div>

        <div className="period" data-fixed={tab === "quota" ? "" : undefined}>
          <h1 className="page-title">{TABS.find((t) => t.id === tab)?.title}</h1>
          {tab !== "quota" && <>
          <Seg label="Period" value={period} options={PERIODS} onPick={pick} />
          <div className="steps">
            <button className="step" onClick={() => step(-1)} aria-label="Previous period">‹</button>
            <button className="step" onClick={() => step(1)} disabled={isCurrent} aria-label="Next period">›</button>
          </div>
          <Seg label="Read every figure as" value={unit} options={UNITS} onPick={pickUnit} />
          <div className="period-label">
            {label}
            <small>{data ? windowLabel(period, data.from_local, data.to_local) : "\u00a0"}</small>
          </div>
          </>}
        </div>
      </header>

      <main className="content">
        {error && tab !== "quota" && <p className="err">The hub did not answer ({error}). Retrying every minute.</p>}
        {!data && !error && tab !== "quota" && <p className="empty">Reading the meters…</p>}
        {data && tab === "now"       && <Now data={data} unit={unit} go={go} />}
        {tab === "quota"             && <Quota />}
        {data && tab === "machines"  && <Machines data={data} unit={unit} />}
        {data && tab === "harnesses" && <Harnesses data={data} unit={unit} />}
        {data && tab === "models"    && <Models data={data} unit={unit} />}
        {tab === "more" && <More onReload={load} />}
      </main>
    </div>
  );
}
