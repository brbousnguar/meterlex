import { useCallback, useEffect, useRef, useState } from "react";
import { api, type FiveHourOpen, type Quota as QuotaData } from "../api";
import Bars, { type BarPoint } from "../components/Bars";
import { RankRows } from "../components/charts";
import WeekLine from "../components/WeekLine";
import WindowLine from "../components/WindowLine";
import { ago, fmtInt, fmtTok, folderLabel, harness, pct } from "../lib";

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
    dayDate: (iso: string) => new Date(iso).toLocaleDateString("en-GB", { timeZone: tz, weekday: "short", day: "numeric", month: "short" }),
  };
}

const left = (days: number) => {
  const h = Math.max(0, Math.round(days * 24));
  return h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${h}h`;
};

/** The two windows Claude meters, one at a time behind the header's switch. */
export type QuotaMode = "week" | "five";
/** The window read, for the header's label: the week, and the open 5-hour window if there is one. */
export type QuotaWeek = { back: number; start: string; reset: string; tz: string; five: { start: string; reset: string } | null };

/** `back` is the week in view, as weeks before the live one: the header's steppers own it
 *  and the Past weeks rows set it. Each window read is reported up for the header's label. */
export default function Quota({ back, mode, onBack, onWeek }: {
  back: number; mode: QuotaMode; onBack: (back: number) => void; onWeek: (w: QuotaWeek) => void;
}) {
  const [q, setQ] = useState<QuotaData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);        // a slow answer for an earlier week must not land on a later one
  const load = useCallback(() => {
    const n = ++seq.current;
    api.quota(back)
      .then((d) => {
        if (n !== seq.current) return;
        setQ(d); setError(null);
        onWeek({ back: d.window.back, start: d.window.start, reset: d.window.reset, tz: d.tz,
                 five: d.five_hour?.open ? { start: d.five_hour.open.start, reset: d.five_hour.open.reset } : null });
      })
      .catch((e) => { if (n === seq.current) setError(String(e.message ?? e)); });
  }, [back, onWeek]);
  useEffect(() => { load(); const t = setInterval(load, 60_000); return () => clearInterval(t); }, [load]);

  if (error) return <p className="err">The hub did not answer ({error}). Retrying every minute.</p>;
  if (!q) return <p className="empty">Reading the quota…</p>;
  // the page keeps the week it has while the next one loads, dimmed so it never reads as the new one
  return (
    <div className="quota" data-stale={q.window.back !== back ? "" : undefined}>
      <QuotaView q={q} mode={mode} onBack={onBack} />
    </div>
  );
}

function QuotaView({ q, mode, onBack }: { q: QuotaData; mode: QuotaMode; onBack: (back: number) => void }) {
  return mode === "five" ? <FiveView q={q} /> : <WeekView q={q} onBack={onBack} />;
}

/** The week: the figure Claude meters over seven days from the reset, and what it is made of. */
function WeekView({ q, onBack }: { q: QuotaData; onBack: (back: number) => void }) {
  const clock = useClock(q.tz);
  const used = q.used_pct;
  const t = q.tokens;
  const p = q.pace;
  const live = q.window.back === 0;
  const week = live ? "this week" : "that week";
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
          {!q.anchored ? <>No quota reading yet: the last seven days, in tokens only</>
            : live ? <>Claude {q.plan_label ?? "Max"}, this week · resets {clock.when(q.window.reset)}{q.window.days_left !== null && <> · {left(q.window.days_left)} left</>}</>
            : <>Claude {q.plan_label ?? "Max"}, week of {clock.dayDate(q.window.start)} · reset {clock.when(q.window.reset)}</>}
        </div>
        <div className="quota-figure">
          <span className="quota-pct">{used === null ? "—" : `${used.toFixed(0)}%`}</span>
          <span className="quota-of">
            {live ? <>of the week used · {q.window.elapsed_pct.toFixed(0)}% of the week gone</>
              : used === null ? <>no quota reading that week</> : <>of the week used by the reset</>}
          </span>
        </div>
        <Meter used={used} elapsed={q.window.elapsed_pct} projected={p?.projected_pct ?? null} anchored={q.anchored} live={live} />
        <div className="reading-meta">
          {q.reading?.stale && <span className="pace-chip over">stale · read {ago(q.reading.at).text}</span>}
          {p && (
            <span className={`pace-chip${tone ? ` ${tone}` : ""}`}>
              {p.status === "on" ? "on pace" : `${Math.abs(p.delta_pts).toFixed(0)} pts ${p.status === "ahead" ? "ahead of" : "behind"} pace`}
            </span>
          )}
          {p?.projected_pct != null && (
            <span className="rank-sub">at this rate, {p.projected_pct.toFixed(0)}% by the reset</span>
          )}
          {q.reading && (
            <span className="rank-sub">
              {live ? "read" : "last read"} {clock.when(q.reading.at)} on <span className="mono">{q.reading.machine}</span>
              {q.reading.stale && <>, {fmtTok(q.reading.tokens_since)} tokens since</>}
            </span>
          )}
          {q.plan_change && (
            <span className="rank-sub">{q.plan_change.from_label} → {q.plan_change.to_label} after {clock.when(q.plan_change.last_at)}</span>
          )}
          {q.plan_next && (
            <span className="rank-sub">
              {q.plan_next.plan_label} from {clock.dayDate(q.plan_next.at)}, as booked{Date.parse(q.plan_next.at) <= Date.parse(q.now) ? " · no reading on it yet" : ""}
            </span>
          )}
        </div>
      </section>

      <section className="section bare">
        <div className="rail lift">
          <div className="stat">
            <div className="stat-label">Used {week}</div>
            <div className="stat-value">{fmtTok(t.used)}</div>
            <div className="stat-sub">tokens through Claude Code</div>
          </div>
          <div className="stat">
            <div className="stat-label">The week's budget</div>
            <div className="stat-value">{t.budget ? `≈ ${fmtTok(t.budget)}` : "—"}</div>
            <div className="stat-sub">
              {t.budget ? `100%${q.plan_label ? ` of ${q.plan_label}` : ""}, estimated from ${t.budget_from}`
                : q.plan_change ? `${live ? "needs" : "needed"} 3 points gained on ${q.plan_change.to_label}`
                : `${live ? "needs" : "needed"} a reading of 3% or more`}
            </div>
          </div>
          {!live ? (
            <div className="stat">
              <div className="stat-label">Unused at the reset</div>
              <div className="stat-value">{t.left ? fmtTok(t.left.tokens) : "—"}</div>
              <div className="stat-sub">{t.left ? "of the budget, by the last reading" : "no budget that week"}</div>
            </div>
          ) : lastDay ? (
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
              {t.avg_day_past ? `${week} · ${live ? "past weeks" : "the weeks before"} ${fmtTok(t.avg_day_past)}` : live ? "this week so far" : "that week"}
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
          <div className="section-note">tokens by weekday and hour, {live ? "last four weeks" : `the four weeks to ${clock.date(q.window.reset)}`}</div>
        </div>
        <Heat hours={q.hours} />
      </section>

      <div className="quota-cols">
        <div>
          <section className="section">
            <div className="section-head">
              <h2 className="section-title">Past weeks</h2>
              <div className="section-note">
                {q.avg_final_pct != null ? `ended at ${q.avg_final_pct.toFixed(0)}% on average${q.plan_label ? ` on ${q.plan_label}` : ""}`
                  : q.past.some((w) => w.final_pct !== null && !w.same_plan) ? "read on another plan" : "same reset hour"}
              </div>
            </div>
            <RankRows rows={q.past.filter((w) => w.tokens > 0 || w.final_pct !== null).map((w) => ({
              key: w.start,
              name: <span>{`from ${clock.date(w.start)}`}</span>,
              current: w.back === q.window.back,
              onClick: () => onBack(w.back),
              value: fmtTok(w.tokens),
              sub: w.final_pct !== null
                ? `ended at ${w.final_pct.toFixed(0)}%${w.switched_from_label ? ` · ${w.switched_from_label} → ${w.plan_label}`
                  : !w.same_plan && w.plan_label ? ` on ${w.plan_label}` : ""}`
                : "no quota reading",
              share: pct(w.tokens, pastPeak),
              fill: CLAUDE.fill,
              tone: w.final_pct !== null && w.final_pct >= 95 ? "over" : undefined,
            }))} />
          </section>
          <section className="section">
            <div className="section-head"><h2 className="section-title">Models {week}</h2></div>
            <RankRows rows={q.by_model.filter((m) => m.tokens > 0).map((m) => ({
              key: m.key, name: <span className="mono">{m.key}</span>, value: fmtTok(m.tokens),
              share: pct(m.tokens, q.by_model[0]?.tokens ?? 1), fill: "var(--bar)",
            }))} />
          </section>
        </div>
        <div>
          <section className="section">
            <div className="section-head"><h2 className="section-title">Folders {week}</h2></div>
            <RankRows rows={q.by_project.map((f) => ({
              key: f.key, name: <span className="mono" title={f.key}>{folderLabel(f.key)}</span>,
              value: fmtTok(f.tokens), share: pct(f.tokens, q.by_project[0]?.tokens ?? 1), fill: CLAUDE.fill,
            }))} />
          </section>
          <section className="section">
            <div className="section-head"><h2 className="section-title">Machines {week}</h2></div>
            <RankRows rows={q.by_machine.map((m) => ({
              key: m.key, name: <span className="mono">{m.key}</span>, value: fmtTok(m.tokens),
              sub: `${pct(m.tokens, t.used).toFixed(0)}% of the week's tokens`,
              share: pct(m.tokens, q.by_machine[0]?.tokens ?? 1), fill: "var(--bar)",
            }))} />
          </section>
        </div>
      </div>

      <p className="rank-sub quota-note">
        The percentage is Anthropic's own reading, and covers claude.ai and the desktop app too. Claude
        Code's status line passes it on; the desktop app has none, so while Claude Code runs there the
        collector asks for it with one tiny Haiku call, at most every ten minutes. Tokens are Claude Code's only, so the budget in tokens is an
        estimate that moves with the model mix: a token of a dearer model takes more of the quota, and
        cache reads count for little. A plan change keeps the reset but rescales the percentage, so the
        budget and the past weeks' average only use readings on the current plan, and after a switch
        the budget comes from the points gained since.
      </p>
    </>
  );
}

const minutes = (m: number) => m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}` : `${m} min`;

/** The 5-hour window, the other figure Claude meters: it opens with the first message, resets five
 *  hours later, and is the one that runs out mid-session. Behind the header's switch it is the page:
 *  the open window with the week's treatment (figure, meter, pace, line), then the last windows,
 *  the open one in the accent. It is live only; a past week in hand has no window to show. */
function FiveView({ q }: { q: QuotaData }) {
  const clock = useClock(q.tz);
  const hhmm = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { timeZone: q.tz, hour: "2-digit", minute: "2-digit" });
  const f = q.window.back === 0 ? q.five_hour : null;
  if (!f) return <p className="empty">{q.window.back === 0 ? "No 5-hour reading yet: Claude Code's status line sends one with the first message." : "Reading the window…"}</p>;
  const o = f.open;
  const last = f.windows[0];
  return (
    <>
      <section className="reading">
        <div className="reading-label">
          {o ? <>Claude {q.plan_label ?? "Max"}, the 5-hour window · opened {hhmm(o.start)} · resets {hhmm(o.reset)} · {minutes(o.minutes_left)} left</>
            : <>Claude {q.plan_label ?? "Max"}, the 5-hour window · the next one opens with the first message</>}
        </div>
        {o ? <OpenWindow o={o} now={q.now} tz={q.tz} hhmm={hhmm} /> : (
          <div className="quota-figure">
            <span className="quota-pct">—</span>
            <span className="quota-of">
              no window open{last && <> · the last ended {clock.when(last.reset)} at {last.final_pct.toFixed(0)}%{last.capped_at ? `, capped at ${hhmm(last.capped_at)}` : ""}</>}
            </span>
          </div>
        )}
      </section>

      <section className="section five-windows">
        <div className="section-head">
          <h2 className="section-title">The last windows</h2>
          <div className="section-note">five hours from the first message</div>
        </div>
        <RankRows rows={f.windows.map((w) => ({
          key: w.reset,
          name: <span className="mono">{clock.day(w.start)} {hhmm(w.start)} → {hhmm(w.reset)}</span>,
          current: w.current,
          value: `${w.final_pct.toFixed(0)}%`,
          sub: w.current ? `${fmtTok(w.tokens)} tokens so far`
            : `${fmtTok(w.tokens)} tokens${w.capped_at ? `, capped at ${hhmm(w.capped_at)}` : ""}`,
          share: w.final_pct, fill: CLAUDE.fill,
          tone: w.final_pct >= 95 ? "over" : undefined,
        }))} />
      </section>
    </>
  );
}

function OpenWindow({ o, now, tz, hhmm }: { o: FiveHourOpen; now: string; tz: string; hhmm: (iso: string) => string }) {
  const p = o.pace;
  const tone = p.status === "ahead" ? "over" : p.status === "behind" ? "under" : undefined;
  const before = p.runs_out_at ? Math.round((Date.parse(o.reset) - Date.parse(p.runs_out_at)) / 60_000) : null;
  return (
    <>
      <div className="quota-figure">
        <span className="quota-pct">{o.used_pct.toFixed(0)}%</span>
        <span className="quota-of">of the window used · {o.elapsed_pct.toFixed(0)}% of the window gone</span>
      </div>
      <Meter used={o.used_pct} elapsed={o.elapsed_pct} projected={p.projected_pct} anchored live />
      <div className="reading-meta">
        <span className={`pace-chip${tone ? ` ${tone}` : ""}`}>
          {p.status === "on" ? "on pace" : `${Math.abs(p.delta_pts).toFixed(0)} pts ${p.status === "ahead" ? "ahead of" : "behind"} pace`}
        </span>
        {p.projected_pct != null && (
          <span className="rank-sub">
            {p.runs_out_at && before !== null
              ? `at this rate, 100% at ${hhmm(p.runs_out_at)}, ${minutes(before)} before the reset`
              : `at this rate, ${p.projected_pct.toFixed(0)}% by the reset`}
          </span>
        )}
        {o.capped_at && <span className="rank-sub">capped at {hhmm(o.capped_at)}</span>}
        <span className="rank-sub">read {hhmm(o.at)} on <span className="mono">{o.machine}</span></span>
        <span className="rank-sub">
          {fmtTok(o.tokens.used)} tokens since {hhmm(o.start)}
          {o.tokens.budget ? ` · ≈ ${fmtTok(o.tokens.budget)} for the window, ${fmtTok(o.tokens.left ?? 0)} left` : ""}
        </span>
      </div>
      <WindowLine open={o} now={now} tz={tz} />
    </>
  );
}

/** One track for the week: the fill is what is used, the tick is where the week is. */
export function Meter({ used, elapsed, projected, anchored, live, span = "week", alarm = false }: { used: number | null; elapsed: number; projected: number | null; anchored: boolean; live: boolean; span?: string; alarm?: boolean }) {
  const u = Math.min(100, used ?? 0);
  const ahead = used !== null && used > elapsed;
  return (
    <div className="quota-meter" role="img"
         aria-label={anchored ? `${(used ?? 0).toFixed(0)}% used, ${elapsed.toFixed(0)}% of the ${span} gone` : "no reading yet"}>
      <i className={`quota-fill${alarm ? " alarm" : ""}`} style={{ width: `${u}%` }} />
      {ahead && <i className="quota-over" style={{ left: `${elapsed}%`, width: `${u - elapsed}%` }} />}
      {projected !== null && projected > u && (
        <i className="quota-proj" style={{ left: `${u}%`, width: `${Math.min(100, projected) - u}%` }} />
      )}
      {anchored && live && <i className="quota-now" style={{ left: `${elapsed}%` }}><b>now</b></i>}
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
