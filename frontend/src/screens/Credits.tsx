import { useCallback, useEffect, useState } from "react";
import { api, type Credits as CreditsData, type Prepaid as PrepaidData } from "../api";
import Bars, { type BarPoint } from "../components/Bars";
import MonthLine from "../components/MonthLine";
import { fmtInt, fmtUsd, fmtUsdShort, pct } from "../lib";
import { Meter } from "./Quota";

/* The Credits tab: one provider at a time (#77). ElevenLabs meters a month of credits; OpenRouter and OpenAI are
   paid ahead, so theirs is a balance that runs down (Prepaid, below).

   The ElevenLabs month (#73), on its own tab (#75). ElevenLabs meters its plans in credits a month and says so through
   its own API: the collector reads the count and the daily stats, the hub lays them against the
   month. The Claude week's treatment: the figure, the meter against an even month, where the
   month's own rate lands by the reset, and the days the credits went. */

/** The month read, for the header's label. */
export type CreditsMonth = { start: string; reset: string; tz: string };

const FILL = "var(--h-elevenlabs)";

const left = (days: number) => {
  const h = Math.max(0, Math.round(days * 24));
  return h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${h}h`;
};

export type CreditsProvider = "elevenlabs" | "openrouter" | "openai";

export default function Credits({ provider, onMonth }: { provider: CreditsProvider; onMonth: (m: CreditsMonth | null) => void }) {
  if (provider === "elevenlabs") return <ElevenLabs onMonth={onMonth} />;
  return <Prepaid key={provider} provider={provider} onMonth={onMonth} />;
}

function ElevenLabs({ onMonth }: { onMonth: (m: CreditsMonth | null) => void }) {
  const [c, setC] = useState<CreditsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api.credits()
      .then((d) => { setC(d); setError(null); onMonth(d.window ? { start: d.window.start, reset: d.window.reset, tz: d.tz } : null); })
      .catch((e) => setError(String(e.message ?? e)));
  }, [onMonth]);
  useEffect(() => { load(); const t = setInterval(load, 60_000); return () => clearInterval(t); }, [load]);

  if (error) return <p className="err">The hub did not answer ({error}). Retrying every minute.</p>;
  if (!c) return <p className="empty">Reading the credits…</p>;
  if (!c.reading || !c.window) {
    return (
      <p className="empty">
        No ElevenLabs reading yet. On the machine that holds the key, run the collector
        with <span className="mono">credits --elevenlabs-env FILE</span>; <span className="mono">run</span> then
        sends the credits every 15 minutes.
      </p>
    );
  }
  return <div className="quota credits"><Month c={c} /></div>;
}

function Month({ c }: { c: CreditsData }) {
  const tz = c.tz;
  const when = (iso: string) => new Date(iso).toLocaleString("en-GB", {
    timeZone: tz, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const dayDate = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { timeZone: tz, weekday: "short", day: "numeric", month: "short" });
  const w = c.window!;
  const r = c.reading!;
  const p = c.pace ?? null;
  const used = c.used ?? null;
  const limit = c.limit ?? 0;
  const plan = `${c.label}${c.plan_label ? ` ${c.plan_label}` : ""}`;
  const tone = p?.status === "ahead" ? "over" : p?.status === "behind" ? "under" : undefined;
  const early = p?.runs_out_at ? (Date.parse(w.reset) - Date.parse(p.runs_out_at)) / 86_400_000 : null;
  const monthUsed = (c.days ?? []).reduce((a, d) => a + d.used, 0);

  // days are UTC, as ElevenLabs counts them; the label is the day of the month
  const points: BarPoint[] = (c.days ?? []).map((d) => {
    const at = `${d.day}T12:00:00Z`;
    return {
      key: d.day,
      label: String(Number(d.day.slice(8))),
      title: `${dayDate(at)}${d.current ? " (today, so far)" : ""}`,
      value: d.used, tokens: d.used, cost_eur: 0,
      note: `${fmtInt(d.used)} credits · ${pct(d.used, Math.max(monthUsed, 1)).toFixed(0)}% of the month's`,
      fill: d.future ? "var(--surface-2)" : FILL,
    };
  });

  return (
    <>
      <section className="reading">
        <div className="reading-label">
          {plan}, this month · resets {when(w.reset)} · {left(w.days_left)} left
        </div>
        <div className="quota-figure">
          <span className="quota-pct">{c.used_pct == null ? "—" : `${c.used_pct.toFixed(0)}%`}</span>
          <span className="quota-of">
            {used === null ? <>the count reset after the last reading · the next one comes within 15 minutes</>
              : <>of the month's credits used · {w.elapsed_pct.toFixed(0)}% of the month gone</>}
          </span>
        </div>
        <Meter used={c.used_pct ?? null} elapsed={w.elapsed_pct} projected={p?.projected_pct ?? null}
               anchored={used !== null} live span="month" />
        <div className="reading-meta">
          {r.stale && <span className="pace-chip over">stale · read {when(r.at)}</span>}
          {p && (
            <span className={`pace-chip${tone ? ` ${tone}` : ""}`}>
              {p.status === "on" ? "on pace" : `${Math.abs(p.delta_pts).toFixed(0)} pts ${p.status === "ahead" ? "ahead of" : "behind"} pace`}
            </span>
          )}
          {p?.runs_out_at && early !== null ? (
            <span className="rank-sub">
              {used !== null && used >= limit ? "used up" : `at this rate, used up ${dayDate(p.runs_out_at)}`}
              {early >= 1 ? `, ${left(early)} before the reset` : ", just before the reset"}
            </span>
          ) : p?.projected_pct != null && (
            <span className="rank-sub">at this rate, {p.projected_pct.toFixed(0)}% by the reset</span>
          )}
          <span className="rank-sub">read {when(r.at)} on <span className="mono">{r.machine}</span></span>
        </div>
      </section>

      <section className="section bare">
        <div className="rail lift">
          <div className="stat">
            <div className="stat-label">Used this month</div>
            <div className="stat-value">{used === null ? "—" : fmtInt(used)}</div>
            <div className="stat-sub">of {fmtInt(limit)} credits</div>
          </div>
          <div className="stat">
            <div className="stat-label">Left</div>
            <div className={`stat-value${c.left === 0 ? " over" : ""}`}>{c.left == null ? "—" : fmtInt(c.left)}</div>
            <div className="stat-sub">
              {c.per_day_to_reset != null ? `${fmtInt(c.per_day_to_reset)} a day lasts to the reset` : `until ${dayDate(w.reset)}`}
            </div>
          </div>
          <div className="stat">
            <div className="stat-label">Per day</div>
            <div className="stat-value">{p?.per_day != null ? fmtInt(p.per_day) : "—"}</div>
            <div className="stat-sub">this month · last 7 days {fmtInt(c.recent_per_day ?? 0)}</div>
          </div>
          <div className="stat">
            <div className="stat-label">By the reset</div>
            <div className={`stat-value${p?.projected_used != null && p.projected_used > limit ? " over" : ""}`}>
              {p?.projected_used != null ? `≈ ${fmtInt(p.projected_used)}` : "—"}
            </div>
            <div className="stat-sub">
              {p?.projected_used == null ? "needs a day of the month"
                : p.projected_used > limit ? `at this month's rate, ${fmtInt(p.projected_used - limit)} over the limit`
                : "at this month's rate, within the limit"}
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">The month against its pace</h2>
          <div className="section-note">point at any day</div>
        </div>
        <MonthLine c={c} />
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">Credits by day</h2>
          <div className="section-note">UTC days, as ElevenLabs counts them</div>
        </div>
        <Bars points={points} unit="tokens" legend={false} />
      </section>

      <p className="rank-sub quota-note">
        The figures are ElevenLabs' own: the collector on the machine that holds the API key asks
        for the subscription's count and the daily usage at most every 15 minutes, and the key never
        leaves that machine. A month runs from one reset to the next; the pace is an even month, and
        the projection carries the month's own rate, from the reset to the last reading, on to the
        next reset.
      </p>
    </>
  );
}

/* ── Prepaid: OpenRouter and OpenAI (#77) ─────────────────────────────────── */

const PREPAID_FILL: Record<PrepaidData["provider"], string> = {
  openrouter: "var(--h-openrouter)",
  openai: "var(--h-openai)",
};

function Prepaid({ provider, onMonth }: { provider: PrepaidData["provider"]; onMonth: (m: CreditsMonth | null) => void }) {
  const [c, setC] = useState<PrepaidData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api.prepaid(provider)
      .then((d) => { setC(d); setError(null); })
      .catch((e) => setError(String(e.message ?? e)));
  }, [provider]);
  useEffect(() => { onMonth(null); load(); const t = setInterval(load, 60_000); return () => clearInterval(t); }, [load, onMonth]);

  if (error) return <p className="err">The hub did not answer ({error}). Retrying every minute.</p>;
  if (!c) return <p className="empty">Reading the balance…</p>;
  if (!c.reading) {
    return (
      <div className="quota credits" style={{ ["--meter" as string]: PREPAID_FILL[provider] }}>
        {provider === "openrouter" ? (
          <p className="empty">
            No OpenRouter reading yet. On the machine that holds the key, run the collector
            with <span className="mono">credits --openrouter-env FILE</span> (a file
            holding <span className="mono">OPENROUTER_API_KEY=…</span>); <span className="mono">run</span> then reads
            the balance every 15 minutes.
          </p>
        ) : (
          <p className="empty">
            No OpenAI reading yet. OpenAI shows its spend only to an admin key: make one on
            platform.openai.com, under Settings → Organization → Admin keys, put it in a file
            as <span className="mono">OPENAI_ADMIN_KEY=…</span>, and run the collector
            with <span className="mono">credits --openai-env FILE</span>.
          </p>
        )}
        {provider === "openai" && <BalanceForm c={c} onSaved={setC} />}
      </div>
    );
  }
  return (
    <div className="quota credits" style={{ ["--meter" as string]: PREPAID_FILL[provider] }}>
      <Balance c={c} onSaved={setC} />
    </div>
  );
}

function Balance({ c, onSaved }: { c: PrepaidData; onSaved: (c: PrepaidData) => void }) {
  const tz = c.tz;
  const when = (iso: string) => new Date(iso).toLocaleString("en-GB", {
    timeZone: tz, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const dayDate = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { timeZone: tz, weekday: "short", day: "numeric", month: "short" });
  const sinceDay = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", day: "numeric", month: "short" });
  const r = c.reading!;
  const s = c.spent;
  const left = c.left ?? null;
  const fill = PREPAID_FILL[c.provider];
  // the share gone, of what was bought (OpenRouter) or of the balance saved from the billing page (OpenAI)
  const whole = c.provider === "openrouter" ? c.bought ?? null : c.balance_set?.usd ?? null;
  const gone = whole && left !== null ? Math.max(0, Math.min(100, 100 * (1 - left / whole))) : null;
  const short = c.lasts_days != null && c.lasts_days < 7;
  const monthSpent = (c.days ?? []).reduce((a, d) => a + d.spent, 0);

  const points: BarPoint[] = (c.days ?? []).map((d) => ({
    key: d.day,
    label: String(Number(d.day.slice(8))),
    title: `${dayDate(`${d.day}T12:00:00Z`)}${d.current ? " (today, so far)" : ""}`,
    value: d.spent, tokens: 0, cost_eur: 0,
    note: !d.known ? "before the record starts" : `${pct(d.spent, Math.max(monthSpent, 1e-9)).toFixed(0)}% of the 30 days'`,
    fill: d.known ? fill : "var(--surface-2)",
  }));

  return (
    <>
      <section className="reading">
        <div className="reading-label">{c.label}, paid ahead · read {when(r.at)}</div>
        <div className="quota-figure">
          <span className={`quota-pct${left !== null && left <= 0 ? " over" : ""}`}>{left === null ? "—" : fmtUsd(left)}</span>
          <span className="quota-of">
            {c.provider === "openrouter"
              ? (left === null ? <>OpenRouter sent no balance</>
                : <>left of {fmtUsd(c.bought ?? 0)} bought · {fmtUsd(c.used ?? 0)} spent in all</>)
              : c.balance_set
                ? <>left of the {fmtUsd(c.balance_set.usd)} on the billing page {dayDate(c.balance_set.at)} · {fmtUsd(c.spent_since_balance ?? 0)} spent since</>
                : <>no balance yet: type the one on the billing page below, and what OpenAI shows spent from then on comes off it</>}
          </span>
        </div>
        {gone !== null && (
          <div className="quota-meter" role="img" aria-label={`${gone.toFixed(0)}% spent`}>
            <i className="quota-fill" style={{ width: `${gone}%` }} />
          </div>
        )}
        <div className="reading-meta">
          {r.stale && <span className="pace-chip over">stale · read {when(r.at)}</span>}
          {c.lasts_days != null && (
            <span className={`pace-chip${short ? " over" : ""}`}>
              {left !== null && left <= 0 ? "used up" : `lasts ${c.lasts_days < 1 ? "under a day" : `about ${Math.round(c.lasts_days)} day${Math.round(c.lasts_days) === 1 ? "" : "s"}`}`}
            </span>
          )}
          {c.runs_out_at && left !== null && left > 0 && (
            <span className="rank-sub">at {fmtUsd(c.per_day ?? 0)} a day, used up around {dayDate(c.runs_out_at)}</span>
          )}
          <span className="rank-sub">read on <span className="mono">{r.machine}</span></span>
        </div>
      </section>

      {c.provider === "openai" && !c.balance_set && <BalanceForm c={c} onSaved={onSaved} />}

      <section className="section bare">
        <div className="rail lift">
          <div className="stat">
            <div className="stat-label">Today</div>
            <div className="stat-value">{s ? fmtUsd(s.today) : "—"}</div>
            <div className="stat-sub">UTC day, so far</div>
          </div>
          <div className="stat">
            <div className="stat-label">Last 7 days</div>
            <div className="stat-value">{s ? fmtUsd(s.week) : "—"}</div>
            <div className="stat-sub">{s && !s.week_complete ? `since ${sinceDay(s.since)}, when the record starts` : "today and the 6 before"}</div>
          </div>
          <div className="stat">
            <div className="stat-label">This month</div>
            <div className="stat-value">{s ? fmtUsd(s.month) : "—"}</div>
            <div className="stat-sub">{s && !s.month_complete ? `since ${sinceDay(s.since)}, when the record starts` : "from the 1st"}</div>
          </div>
          <div className="stat">
            <div className="stat-label">Per day</div>
            <div className="stat-value">{c.per_day != null ? fmtUsd(c.per_day) : "—"}</div>
            <div className="stat-sub">{c.per_day != null ? "over the last 7 days" : "needs a few hours of readings"}</div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">Spent by day</h2>
          <div className="section-note">the last 30 UTC days</div>
        </div>
        <Bars points={points} unit="money" legend={false} format={{ value: fmtUsd, tick: fmtUsdShort }} />
      </section>

      {c.provider === "openai" && c.balance_set && <BalanceForm c={c} onSaved={onSaved} />}

      <p className="rank-sub quota-note">
        {c.provider === "openrouter"
          ? <>The figures are OpenRouter's own: the collector on the machine that holds an API key asks for what
              the account bought and used, all time, at most every 15 minutes. What was used since the reading
              before goes to that reading's day, so the days start with the first reading. The key never leaves
              that machine.</>
          : <>OpenAI has no API for the balance. The spend by day comes from its costs API, read with an admin key
              by the collector on the machine that holds it, at most every 15 minutes; it can lag a few hours.
              What is left is the balance you typed from the billing page, less what was spent since.</>}
      </p>
    </>
  );
}

/** OpenAI's balance, typed from its billing page: there is no API for it. */
function BalanceForm({ c, onSaved }: { c: PrepaidData; onSaved: (c: PrepaidData) => void }) {
  const [value, setValue] = useState(c.balance_set ? c.balance_set.usd.toFixed(2) : "");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const save = (usd: number | null) => {
    setBusy(true);
    api.setOpenAIBalance(usd)
      .then((d) => { onSaved(d); setNote(usd === null ? "Cleared." : "Saved. What was spent from now on comes off it."); if (usd === null) setValue(""); })
      .catch((e) => setNote(`Not saved (${String(e.message ?? e)}).`))
      .finally(() => setBusy(false));
  };
  const usd = parseFloat(value);
  return (
    <section className="section">
      <div className="section-head">
        <h2 className="section-title">Balance on the billing page</h2>
        <div className="section-note">platform.openai.com → Settings → Billing</div>
      </div>
      <div className="field">
        <label htmlFor="openai-balance">
          Credit balance now, in dollars <span className="hint">· type it again after a top-up</span>
        </label>
        <input id="openai-balance" type="number" inputMode="decimal" step="0.01" min="0" value={value}
               onChange={(e) => setValue(e.target.value)} />
      </div>
      <div className="actions">
        <button className="linkout" disabled={busy || !(usd >= 0)} onClick={() => save(usd)}><b>Save</b></button>
        {c.balance_set && <button className="linkout" disabled={busy} onClick={() => save(null)}><b>Clear</b></button>}
      </div>
      {note && <p className="hint" style={{ marginTop: 10 }}>{note}</p>}
    </section>
  );
}
