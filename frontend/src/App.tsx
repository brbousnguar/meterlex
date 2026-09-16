import { useCallback, useEffect, useState } from "react";
import { api, type Overview, type Period } from "./api";
import { PERIODS, periodLabel, stepRef, windowLabel } from "./lib";
import Now from "./screens/Now";
import Machines from "./screens/Machines";
import Harnesses from "./screens/Harnesses";
import Models from "./screens/Models";
import More from "./screens/More";

export type Tab = "now" | "machines" | "harnesses" | "models" | "more";

const TABS: { id: Tab; label: string; icon: JSX.Element }[] = [
  { id: "now",       label: "Now",      icon: <Icon d="M3 17h4V8H3v9Zm7 0h4V4h-4v13Zm7 0h4v-6h-4v6Z" /> },
  { id: "machines",  label: "Machines", icon: <Icon d="M4 4h16v10H4V4Zm2 2v6h12V6H6ZM8 18h8v2H8v-2Z" /> },
  { id: "harnesses", label: "Harness",  icon: <Icon d="M12 3a9 9 0 1 0 9 9h-9V3Z" /> },
  { id: "models",    label: "Models",   icon: <Icon d="M12 2 3 7l9 5 9-5-9-5Zm0 20 9-5v-5l-9 5-9-5v5l9 5Z" /> },
  { id: "more",      label: "More",     icon: <Icon d="M5 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4Zm7 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4Zm7 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z" /> },
];

/* The header mark: the app icon without its paper ground, drawn from the theme
   tokens so the needle stays visible on either. */
function Mark() {
  return (
    <svg className="brand-mark" viewBox="0 0 512 512" aria-hidden="true">
      <path d="M 153 379 A 146 146 0 1 1 359 379" fill="none" stroke="var(--surface-2)" strokeWidth={40} />
      <path d="M 153 379 A 146 146 0 1 1 334 153" fill="none" stroke="var(--h-claude)" strokeWidth={40} />
      <path d="M 256 276 L 323 170" stroke="var(--ink)" strokeWidth={24} />
      <circle cx="256" cy="276" r="26" fill="var(--ink)" />
    </svg>
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
  const [ref, setRef] = useState<string | null>(null);      // null = the current period
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<"light" | "dark" | "system">(
    () => (recall("mx.theme") as "light" | "dark" | "system") || "system");

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    remember("mx.theme", theme);
  }, [theme]);

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
              <div className="brand-kicker">meter room</div>
            </div>
          </div>
          <button className="icon-btn" onClick={load} aria-label="Read the meters again" title="Refresh">↻</button>
          <button className="icon-btn" aria-label={`Theme: ${theme}`} title={`Theme: ${theme}`}
                  onClick={() => setTheme(theme === "system" ? "light" : theme === "light" ? "dark" : "system")}>
            {theme === "dark" ? "◑" : theme === "light" ? "○" : "◐"}
          </button>
        </div>

        <div className="period">
          <div className="seg" role="group" aria-label="Period">
            {PERIODS.map((p) => (
              <button key={p.id} aria-pressed={period === p.id} onClick={() => pick(p.id)}>{p.short}</button>
            ))}
          </div>
          <button className="step" onClick={() => step(-1)} aria-label="Previous period">‹</button>
          <button className="step" onClick={() => step(1)} disabled={isCurrent} aria-label="Next period">›</button>
          <div className="period-label">
            {label}
            <small>{data ? windowLabel(period, data.from_local, data.to_local) : " "}</small>
          </div>
        </div>
      </header>

      <nav className="tabs" aria-label="Sections">
        {TABS.map((t) => (
          <button className="tab" key={t.id} data-testid={`tab-${t.id}`}
                  aria-current={tab === t.id ? "page" : undefined} onClick={() => go(t.id)}>
            {t.icon}{t.label}
          </button>
        ))}
      </nav>

      <main className="content">
        {error && <p className="err">The hub did not answer ({error}). Retrying every minute.</p>}
        {!data && !error && <p className="empty">Reading the meters…</p>}
        {data && tab === "now"       && <Now data={data} go={go} />}
        {data && tab === "machines"  && <Machines data={data} />}
        {data && tab === "harnesses" && <Harnesses data={data} />}
        {data && tab === "models"    && <Models data={data} />}
        {tab === "more" && <More onReload={load} />}
      </main>
    </div>
  );
}
