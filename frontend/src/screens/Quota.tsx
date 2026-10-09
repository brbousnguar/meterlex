import { useCallback, useEffect, useState } from "react";
import { api, type Quota as QuotaData } from "../api";
import Bars, { type BarPoint } from "../components/Bars";
import { RankRows } from "../components/charts";
import WeekLine from "../components/WeekLine";
import { fmtInt, fmtTok, folderLabel, harness, pct } from "../lib";

/* The Claude Max weekly quota. Anthropic meters it as a percentage with a reset
   time; the tokens come from Claude Code's own turns. The percentage is the
   figure to trust, the tokens are what it is made of. */

const CLAUDE = harness("claude-code");

function useClock(tz: string) {
  return {
    when: (iso: string) => new Date(iso).toLocaleString("en-GB", {
      timeZone: tz, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    }),
    day: (iso: string) => new Date(iso).toLocaleDateString("en-GB", { timeZone: tz, weekday: "short" }),
    date: (iso: string) => new Date(iso).toLocaleDateString("en-GB", { timeZone: tz, day: "numeric", month: "short" }),
  };
}

const left = (days: number) => {
  const h = Math.max(0, Math.round(days * 24));
  return h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${h}h`;
};

export default function Quota() {
  const [q, setQ] = useState<QuotaData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api.quota().then((d) => { setQ(d); setError(null); }).catch((e) => setError(String(e.message ?? e)));
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 60_000); return () => clearInterval(t); }, [load]);

  if (error) return <p className="err">The hub did not answer ({error}). Retrying every minute.</p>;
  if (!q) return <p className="empty">Reading the quota…</p>;
  return <QuotaView q={q} />;
}

function QuotaView({ q }: { q: QuotaData }) {
  const clock = useClock(q.tz);
  const used = q.used_pct;
  const t = q.tokens;
  const p = q.pace;
  const lastDay = q.window.days_left !== null && q.window.days_left < 1;
  const tone = p?.status === "ahead" ? "over" : p?.status === "behind" ? "under" : undefined;

  const dayPoints: BarPoint[] = q.days.map((d) => ({
    key: d.start,
    label: clock.day(d.start),
    title: `${clock.day(d.start)} ${clock.date(d.start)}${d.current ? " (today, so far)" : ""}`
      + (d.end_pct !== null ? ` · quota at ${d.end_pct.toFixed(0)}%` : ""),
    value: d.tokens, tokens: d.tokens, cost_eur: 0,
    note: `${pct(d.tokens, t.used).toFixed(0)}% of the week's tokens`,
    fill: d.future ? "var(--surface-2)" : CLAUDE.fill,
  }));
  const pastPeak = Math.max(...q.past.map((w) => w.tokens), t.used, 1);

  return (
    <>
      <section className="reading">
        <div className="reading-label">
          {q.anchored
            ? <>Claude Max, this week · resets {clock.when(q.window.reset)}{q.window.days_left !== null && <> · {left(q.window.days_left)} left</>}</>
            : <>No quota reading yet: the last seven days, in tokens only</>}
        </div>
        <div className="quota-figure">
          <span className="quota-pct">{used === null ? "—" : `${used.toFixed(0)}%`}</span>
          <span className="quota-of">of the week used · {q.window.elapsed_pct.toFixed(0)}% of the week gone</span>
        </div>
        <Meter used={used} elapsed={q.window.elapsed_pct} projected={p?.projected_pct ?? null} anchored={q.anchored} />
        <div className="reading-meta">
          {p && (
            <span className={`pace-chip${tone ? ` ${tone}` : ""}`}>
              {p.status === "on" ? "on pace" : `${Math.abs(p.delta_pts).toFixed(0)} pts ${p.status} of pace`}
            </span>
          )}
          {p?.projected_pct != null && (
            <span className="rank-sub">at this rate, {p.projected_pct.toFixed(0)}% by the reset</span>
          )}
          {q.five_hour && (
            <span className="rank-sub">5-hour window {q.five_hour.used_pct.toFixed(0)}%, resets {clock.when(q.five_hour.reset).split(", ").pop()}</span>
          )}
          {q.reading && (
            <span className="rank-sub">read {clock.when(q.reading.at)} on <span className="mono">{q.reading.machine}</span></span>
          )}
        </div>
      </section>

      <section className="section bare">
        <div className="rail lift">
          <div className="stat">
            <div className="stat-label">Used this week</div>
            <div className="stat-value">{fmtTok(t.used)}</div>
            <div className="stat-sub">tokens through Claude Code</div>
          </div>
          <div className="stat">
            <div className="stat-label">The week's budget</div>
            <div className="stat-value">{t.budget ? `≈ ${fmtTok(t.budget)}` : "—"}</div>
            <div className="stat-sub">
              {t.budget ? `100%, estimated from ${t.budget_from}` : "needs a reading of 3% or more"}
            </div>
          </div>
          {lastDay ? (
            <div className="stat">
              <div className="stat-label">Left before the reset</div>
              <div className="stat-value">{t.left ? fmtTok(t.left.tokens) : "—"}</div>
              <div className="stat-sub">{t.left ? `in about ${left(q.window.days_left ?? 0)}` : "no budget yet"}</div>
            </div>
          ) : (
            <div className="stat">
              <div className="stat-label">To land at 100%</div>
              <div className={`stat-value${t.left?.per_day != null && t.left.per_day < t.per_day_so_far ? " over" : ""}`}>
                {t.left?.per_day != null ? `${fmtTok(t.left.per_day)}/day` : "—"}
              </div>
              <div className="stat-sub">{t.left ? `${fmtTok(t.left.tokens)} left in the budget` : "no budget yet"}</div>
            </div>
          )}
          <div className="stat">
            <div className="stat-label">Per day</div>
            <div className="stat-value">{fmtTok(t.per_day_so_far)}</div>
            <div className="stat-sub">
              {t.avg_day_past ? `this week · past weeks ${fmtTok(t.avg_day_past)}` : "this week so far"}
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">The week against its pace</h2>
          <div className="section-note">point at any hour</div>
        </div>
        <WeekLine q={q} />
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">Tokens by day of the window</h2>
          <div className="section-note">days start at the reset hour</div>
        </div>
        <Bars points={dayPoints} unit="tokens" legend={false} />
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">When you use it</h2>
          <div className="section-note">tokens by weekday and hour, last four weeks</div>
        </div>
        <Heat hours={q.hours} />
      </section>

      <div className="quota-cols">
        <div>
          <section className="section">
            <div className="section-head">
              <h2 className="section-title">Past weeks</h2>
              <div className="section-note">{q.avg_final_pct != null ? `ended at ${q.avg_final_pct.toFixed(0)}% on average` : "same reset hour"}</div>
            </div>
            <RankRows rows={q.past.filter((w) => w.tokens > 0 || w.final_pct !== null).map((w) => ({
              key: w.start,
              name: <span>{`from ${clock.date(w.start)}`}</span>,
              value: fmtTok(w.tokens),
              sub: w.final_pct !== null ? `ended at ${w.final_pct.toFixed(0)}%` : "no quota reading",
              share: pct(w.tokens, pastPeak),
              fill: CLAUDE.fill,
              tone: w.final_pct !== null && w.final_pct >= 95 ? "over" : undefined,
            }))} />
          </section>
          <section className="section">
            <div className="section-head"><h2 className="section-title">Models this week</h2></div>
            <RankRows rows={q.by_model.filter((m) => m.tokens > 0).map((m) => ({
              key: m.key, name: <span className="mono">{m.key}</span>, value: fmtTok(m.tokens),
              share: pct(m.tokens, q.by_model[0]?.tokens ?? 1), fill: "var(--bar)",
            }))} />
          </section>
        </div>
        <div>
          <section className="section">
            <div className="section-head"><h2 className="section-title">Folders this week</h2></div>
            <RankRows rows={q.by_project.map((f) => ({
              key: f.key, name: <span className="mono" title={f.key}>{folderLabel(f.key)}</span>,
              value: fmtTok(f.tokens), share: pct(f.tokens, q.by_project[0]?.tokens ?? 1), fill: CLAUDE.fill,
            }))} />
          </section>
          <section className="section">
            <div className="section-head"><h2 className="section-title">Machines this week</h2></div>
            <RankRows rows={q.by_machine.map((m) => ({
              key: m.key, name: <span className="mono">{m.key}</span>, value: fmtTok(m.tokens),
              sub: `${pct(m.tokens, t.used).toFixed(0)}% of the week's tokens`,
              share: pct(m.tokens, q.by_machine[0]?.tokens ?? 1), fill: "var(--bar)",
            }))} />
          </section>
        </div>
      </div>

      <p className="rank-sub quota-note">
        The percentage is Anthropic's own reading, passed by Claude Code to its status line, and covers
        claude.ai and the desktop app too. Tokens are Claude Code's only, so the budget in tokens is an
        estimate: Opus weighs more than Sonnet and cache reads count for little.
      </p>
    </>
  );
}

/** One track for the week: the fill is what is used, the tick is where the week is. */
function Meter({ used, elapsed, projected, anchored }: { used: number | null; elapsed: number; projected: number | null; anchored: boolean }) {
  const u = Math.min(100, used ?? 0);
  const ahead = used !== null && used > elapsed;
  return (
    <div className="quota-meter" role="img"
         aria-label={anchored ? `${(used ?? 0).toFixed(0)}% used, ${elapsed.toFixed(0)}% of the week gone` : "no reading yet"}>
      <i className="quota-fill" style={{ width: `${u}%` }} />
      {ahead && <i className="quota-over" style={{ left: `${elapsed}%`, width: `${u - elapsed}%` }} />}
      {projected !== null && projected > u && (
        <i className="quota-proj" style={{ left: `${u}%`, width: `${Math.min(100, projected) - u}%` }} />
      )}
      {anchored && <i className="quota-now" style={{ left: `${elapsed}%` }}><b>now</b></i>}
    </div>
  );
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Weekday × hour, one cell each; the deeper the cell, the more tokens. */
function Heat({ hours }: { hours: number[][] }) {
  const peak = Math.max(1, ...hours.flat());
  const total = hours.flat().reduce((a, b) => a + b, 0);
  if (!total) return <div className="empty">Nothing recorded in the last four weeks.</div>;
  return (
    <div className="heat" role="table" aria-label="Tokens by weekday and hour">
      <span />
      {Array.from({ length: 24 }, (_, h) => <span key={h} className="heat-h">{h % 6 === 0 ? h : ""}</span>)}
      {hours.map((row, d) => (
        <div className="heat-row" role="row" key={d} style={{ display: "contents" }}>
          <span className="heat-d">{WEEKDAYS[d]}</span>
          {row.map((v, h) => (
            <i key={h} className="heat-c" title={`${WEEKDAYS[d]} ${String(h).padStart(2, "0")}:00 · ${fmtInt(v)} tokens`}
               style={{ opacity: v ? 0.12 + 0.88 * Math.sqrt(v / peak) : 1, background: v ? CLAUDE.fill : "var(--surface-2)" }} />
          ))}
        </div>
      ))}
    </div>
  );
}
