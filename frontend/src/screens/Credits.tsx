import { useCallback, useEffect, useState } from "react";
import { api, type Credits as CreditsData } from "../api";
import Bars, { type BarPoint } from "../components/Bars";
import MonthLine from "../components/MonthLine";
import { fmtInt, pct } from "../lib";
import { Meter } from "./Quota";

/* The ElevenLabs month (#73), on its own tab (#75). ElevenLabs meters its plans in credits a month and says so through
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

export default function Credits({ onMonth }: { onMonth: (m: CreditsMonth | null) => void }) {
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
