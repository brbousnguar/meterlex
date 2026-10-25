import { useCallback, useEffect, useState } from "react";
import Seg, { useThumb } from "./components/Seg";
import { api, QUOTA_WEEKS_BACK, type Overview, type Period } from "./api";
import { PERIODS, periodLabel, recall, remember, stepRef, UNITS, windowLabel, type Unit } from "./lib";
import Now from "./screens/Now";
import Machines from "./screens/Machines";
import Harnesses from "./screens/Harnesses";
import Models from "./screens/Models";
import More from "./screens/More";
import Quota, { type QuotaMode, type QuotaWeek } from "./screens/Quota";
import Credits, { type CreditsMonth, type CreditsProvider } from "./screens/Credits";

export type Tab = "now" | "quota" | "credits" | "machines" | "harnesses" | "models" | "more";

/* Every icon is cut square, like the rest of the app. `title` heads the page on a desktop. */
const TABS: { id: Tab; label: string; title: string; icon: JSX.Element }[] = [
  { id: "now",       label: "Now",      title: "The reading", icon: <Icon d="M3 17h4V8H3v9Zm7 0h4V4h-4v13Zm7 0h4v-6h-4v6Z" /> },
  { id: "quota",     label: "Quota",    title: "Claude Max quota", icon: <Icon d="M3 4h18v6H3V4Zm2 2v2h8V6H5Zm-2 8h18v6H3v-6Zm2 2v2h4v-2H5Z" /> },
  { id: "credits",   label: "Credits",  title: "Credits", icon: <Icon d="M3 9h3v6H3V9Zm5-5h3v16H8V4Zm5 3h3v10h-3V7Zm5 4h3v2h-3v-2Z" /> },
  { id: "machines",  label: "Machines", title: "Machines",    icon: <Icon d="M4 4h16v10H4V4Zm2 2v6h12V6H6ZM8 18h8v2H8v-2Z" /> },
  { id: "harnesses", label: "Harness",  title: "Harnesses",   icon: <Icon d="M4 4h7v16H4V4Zm9 0h7v7h-7V4Zm0 9h7v7h-7v-7Z" /> },
  { id: "models",    label: "Models",   title: "Models",      icon: <Icon d="M12 2 3 7l9 5 9-5-9-5Zm0 20 9-5v-5l-9 5-9-5v5l9 5Z" /> },
  { id: "more",      label: "More",     title: "Rates and collectors", icon: <Icon d="M3 10h4v4H3v-4Zm7 0h4v4h-4v-4Zm7 0h4v4h-4v-4Z" /> },
];
/** The Credits header's switch: one provider at a time. */
const CREDIT_PROVIDERS: { id: CreditsProvider; short: string }[] = [
  { id: "elevenlabs", short: "ElevenLabs" }, { id: "openrouter", short: "OpenRouter" }, { id: "openai", short: "OpenAI" }];
/** The Quota header's switch: the week, or the 5-hour window. */
const QUOTA_MODES: { id: QuotaMode; short: string }[] = [{ id: "week", short: "Week" }, { id: "five", short: "5 hours" }];

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

function Icon({ d }: { d: string }) {
  return <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d={d} /></svg>;
}


export default function App() {
  const [tab, setTab] = useState<Tab>(() => (recall("mx.tab") as Tab) || "now");
  const [period, setPeriod] = useState<Period>(() => (recall("mx.period") as Period) || "weekly");
  const [unit, setUnit] = useState<Unit>(() => (recall("mx.unit") as Unit) || "tokens");
  const [ref, setRef] = useState<string | null>(null);      // null = the current period
  const [back, setBack] = useState(0);                      // the Quota week in view, weeks before this one
  const [week, setWeek] = useState<QuotaWeek | null>(null); // the window the Quota screen last read
  const [mode, setMode] = useState<QuotaMode>(() => recall("mx.quota") === "five" ? "five" : "week");
  const [month, setMonth] = useState<CreditsMonth | null>(null); // the ElevenLabs month last read
  const [provider, setProvider] = useState<CreditsProvider>(() =>
    CREDIT_PROVIDERS.find((p) => p.id === recall("mx.credits"))?.id ?? "elevenlabs");
  const pickProvider = (p: CreditsProvider) => { setProvider(p); remember("mx.credits", p); };
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

  const stepWeek = (delta: number) => setBack((b) => Math.max(0, Math.min(QUOTA_WEEKS_BACK, b + delta)));
  // the 5-hour window is live only: picking it brings the week back to this one
  const pickMode = (m: QuotaMode) => { setMode(m); remember("mx.quota", m); if (m === "five") setBack(0); };
  // ← → step the Quota week when nothing else wants the keys (the week line takes them while focused)
  useEffect(() => {
    if (tab !== "quota" || mode !== "week") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      if ((e.target as HTMLElement | null)?.closest("input, select, textarea, [tabindex]")) return;
      stepWeek(e.key === "ArrowLeft" ? 1 : -1);
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab, mode]);
  const dayOf = (iso: string, tz: string) => new Date(iso).toLocaleDateString("en-GB", { timeZone: tz, day: "numeric", month: "short" });
  const hhmm = (iso: string, tz: string) => new Date(iso).toLocaleTimeString("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit" });

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

        <div className="period" data-fixed={tab === "quota" || tab === "credits" ? "" : undefined}>
          <h1 className="page-title">{TABS.find((t) => t.id === tab)?.title}</h1>
          {tab === "credits" && <>
          <Seg label="Credits of" value={provider} options={CREDIT_PROVIDERS} onPick={pickProvider} />
          <div className="period-end">
          <div className="period-label">
            {provider === "elevenlabs" ? "This month" : "Paid ahead"}
            <small>{provider !== "elevenlabs" ? "the last 30 days" : month ? `${dayOf(month.start, month.tz)} → ${dayOf(month.reset, month.tz)}` : " "}</small>
          </div>
          </div>
          </>}
          {tab === "quota" && <>
          {/* The two windows Claude meters, one at a time. */}
          <Seg label="Quota window" value={mode} options={QUOTA_MODES} onPick={pickMode} />
          {/* The steppers and the label keep to the right end, so the switch stays put when the steppers go. */}
          <div className="period-end">
          {mode === "week" ? <>
          {/* The Quota week: the same steppers, a week at a time, back to the oldest week listed. */}
          <div className="steps">
            <button className="step" onClick={() => stepWeek(1)} disabled={back >= QUOTA_WEEKS_BACK} aria-label="The week before">‹</button>
            <button className="step" onClick={() => stepWeek(-1)} disabled={back === 0} aria-label="The week after">›</button>
          </div>
          <div className="period-label">
            {back === 0 ? "This week" : back === 1 ? "Last week" : `${back} weeks back`}
            <small>{week && week.back === back ? `${dayOf(week.start, week.tz)} → ${dayOf(week.reset, week.tz)}` : " "}</small>
          </div>
          </> : (
          <div className="period-label">
            {!week ? "5-hour window" : week.five ? "This window" : "No window open"}
            <small>{!week ? " " : week.five ? `${hhmm(week.five.start, week.tz)} → ${hhmm(week.five.reset, week.tz)}` : "opens with the first message"}</small>
          </div>
          )}
          </div>
          </>}
          {tab !== "quota" && tab !== "credits" && <>
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
        {error && tab !== "quota" && tab !== "credits" && <p className="err">The hub did not answer ({error}). Retrying every minute.</p>}
        {!data && !error && tab !== "quota" && tab !== "credits" && <p className="empty">Reading the meters…</p>}
        {data && tab === "now"       && <Now data={data} unit={unit} go={go} />}
        {tab === "quota"             && <Quota back={back} mode={mode} onBack={setBack} onWeek={setWeek} />}
        {tab === "credits"           && <Credits provider={provider} onMonth={setMonth} />}
        {data && tab === "machines"  && <Machines data={data} unit={unit} />}
        {data && tab === "harnesses" && <Harnesses data={data} unit={unit} />}
        {data && tab === "models"    && <Models data={data} unit={unit} />}
        {tab === "more" && <More onReload={load} />}
      </main>
    </div>
  );
}
