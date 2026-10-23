"""The Claude Max quota: the weekly window Anthropic meters, read against the
week's elapsed time and against the tokens Claude Code used in it.

Anthropic gives the quota as a percentage and a reset time (collectors record
what Claude Code hands its status line), never in tokens. The tokens come from
the stored Claude Code turns, and the two together give the week's budget in
tokens: tokens used up to a reading ÷ that reading's percentage. It is an
estimate — Opus and Sonnet weigh differently and cache reads count for little,
and usage on claude.ai or the desktop app is in the percentage but not in the
turns — so the percentage stays the figure to trust.

A plan change (Max 5x to Max 20x, say) keeps the reset but rescales the
percentage, so a figure only compares with figures read on the same plan:
the budget and the past weeks' average use those alone. Whether the new plan's
figure counts the whole week or only the time since the switch is not known,
so after a switch the budget is the tokens between two of its readings ÷ the
points gained between them, which holds either way.
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from models import QuotaReading, Setting, UsageTurn

WEEK = timedelta(days=7)
DAY = timedelta(days=1)
SOURCE = "claude-code"          # the turns that count against Claude Max
PAST_WINDOWS = 8
FIVE = timedelta(hours=5)       # the other window Claude meters: five hours from the first message
FIVE_SLOT = timedelta(minutes=10)
FIVE_WINDOWS = 6                # the 5-hour windows listed, newest first
MIN_PCT_FOR_BUDGET = 3.0        # below this a budget is mostly rounding noise
SAME_RESET = timedelta(hours=1) # resets_at can move by seconds between readings
PACE_BAND = 2.0                 # points either side of the pace that read as "on pace"
STALE_AFTER = timedelta(minutes=30)  # a reading this old with Claude tokens after it is not the figure now


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _local(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone(tz)


def _readings(session: Session, window: str, since: datetime) -> list:
    return list(session.exec(
        select(QuotaReading).where(QuotaReading.window == window, QuotaReading.ts >= since)
        .order_by(QuotaReading.ts)
    ).all())


def _latest(session: Session, window: str) -> Optional[QuotaReading]:
    return session.exec(
        select(QuotaReading).where(QuotaReading.window == window).order_by(QuotaReading.ts.desc())
    ).first()


def current_window(latest: Optional[QuotaReading], now: datetime) -> tuple[datetime, datetime, bool]:
    """(start, reset, anchored). Windows are a fixed week ending at the reset
    Anthropic gives; once that reset passes, the next window is a week later.
    Without any reading yet, the last seven days stand in (anchored = False)."""
    if latest is None:
        return now - WEEK, now, False
    reset = latest.resets_at
    while reset <= now:
        reset += WEEK
    return reset - WEEK, reset, True


def _in_window(r: QuotaReading, reset: datetime) -> bool:
    return abs(r.resets_at - reset) <= SAME_RESET


def _line(rs: list) -> list:
    """The readings that moved the figure, in time order. Within one plan the
    figure only rises: every open Claude Code session repaints the last figure
    it was given, so a lower one is a session that has fallen behind, even a
    fresh one (a session whose own figure just moved). A plan change rescales
    the figure, so a fresh reading that names another plan sets it even when
    it is lower. Once a fresh reading has set the figure, other readings only
    count when they name the same plan (a collector older than 0.6.0 names
    none)."""
    out, top, fresh_plan, plan = [], None, None, None
    for r in sorted(rs, key=lambda r: r.ts):
        if r.fresh:
            fresh_plan = r.plan or ""
        elif fresh_plan is not None and (r.plan or "") != fresh_plan:
            continue
        switch = r.fresh and r.plan is not None and r.plan != plan
        if top is None or r.used_pct > top or (switch and r.used_pct != top):
            out.append(r)
            top = r.used_pct
            plan = r.plan or plan
    return out


def _parts(line: list) -> list:
    """A window's line cut at each switch between two named plans: each part
    is on its own plan's scale."""
    parts, plan = [], None
    for r in line:
        if parts and not (r.plan and plan and r.plan != plan):
            parts[-1].append(r)
        else:
            parts.append([r])
        plan = r.plan or plan
    return parts


def _base(part: list, first: bool, start: datetime) -> Optional[tuple]:
    """(time, percentage) where a part's figure is known to the point. The
    window's first part starts at the reset, from 0%. After a plan change the
    new figure may count the whole week or only the time since the switch, so
    the part starts at its first tick: the first reading above the one before
    it, when the figure has just crossed a whole point."""
    if first:
        return start, 0.0
    tick = next((b for a, b in zip(part, part[1:]) if b.used_pct > a.used_pct), None)
    return (tick.ts, tick.used_pct) if tick else None


def _part_budget(part: list, base: Optional[tuple], tokens_between) -> Optional[float]:
    """Tokens from the part's base to its latest reading ÷ the points gained."""
    if base is None:
        return None
    r = part[-1]
    gained = r.used_pct - base[1]
    if r.ts <= base[0] or gained < MIN_PCT_FOR_BUDGET:
        return None
    return tokens_between(base[0], r.ts) / (gained / 100)


def plan_label(plan: Optional[str]) -> Optional[str]:
    """default_claude_max_20x → Max 20x."""
    if not plan:
        return None
    m = re.search(r"max_(\d+)x", plan)
    if m:
        return f"Max {m.group(1)}x"
    for word, label in (("max", "Max"), ("team", "Team"), ("enterprise", "Enterprise"), ("pro", "Pro")):
        if word in plan:
            return label
    return plan


def _plan_of(rs: list) -> Optional[str]:
    """The plan of the newest reading that names one."""
    return next((r.plan for r in sorted(rs, key=lambda r: r.ts, reverse=True) if r.plan), None)


def _last_on(session: Session, plan: Optional[str], before: datetime) -> Optional[datetime]:
    """The last reading on a plan before a time, in either window."""
    return session.exec(select(QuotaReading.ts).where(
        QuotaReading.plan == plan, QuotaReading.ts < before).order_by(QuotaReading.ts.desc())).first()


def _plan_change(session: Session, line: list) -> Optional[dict]:
    """The last switch between two named plans along a window's line. It
    happened after the last reading on the old plan and before the first on
    the new one, in either window: the 5-hour readings come more often, so
    they narrow it. The week line draws its delimiter at `last_at`."""
    change, prev = None, None
    for r in line:
        if r.plan and prev is not None and r.plan != prev.plan:
            change = (prev, r)
        if r.plan:
            prev = r
    if change is None:
        return None
    old, new = change
    last = _last_on(session, old.plan, new.ts)
    first = session.exec(select(QuotaReading.ts).where(
        QuotaReading.plan == new.plan, QuotaReading.ts > (last or old.ts)).order_by(QuotaReading.ts)).first()
    return {"from": old.plan, "to": new.plan, "from_label": plan_label(old.plan), "to_label": plan_label(new.plan),
            "last_at": _iso(max(last or old.ts, old.ts)), "at": _iso(first or new.ts)}


def next_plan(session: Session, plan: Optional[str], tz: ZoneInfo) -> Optional[dict]:
    """The plan the subscription is booked to move to, from the settings
    (`plan_next`, `plan_next_on` as YYYY-MM-DD). The day is the user's own, so
    the switch is marked at its local midnight. Shown until a reading names
    that plan: from then on the readings tell the story."""
    nxt, on = session.get(Setting, "plan_next"), session.get(Setting, "plan_next_on")
    if not (nxt and nxt.value and on and on.value) or nxt.value == plan:
        return None
    try:
        day = datetime.strptime(on.value, "%Y-%m-%d")
    except ValueError:
        return None
    at = day.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    return {"plan": nxt.value, "plan_label": plan_label(nxt.value), "on": on.value, "at": _iso(at)}


def stamp_plan(session: Session, plan: str, before: datetime, apply: bool = False) -> int:
    """Name the plan of the readings before `before` that carry none."""
    rows = session.exec(select(QuotaReading).where(QuotaReading.plan.is_(None), QuotaReading.ts < before)).all()
    if apply:
        for r in rows:
            r.plan = plan
            session.add(r)
        session.commit()
    return len(rows)


def _five_clusters(rs: list) -> list:
    """The 5-hour readings grouped by window, one cluster per reset, each in
    time order; a reset can move by seconds between readings."""
    clusters: list = []
    for r in sorted(rs, key=lambda r: r.ts):
        for c in clusters:
            if abs(c[0].resets_at - r.resets_at) <= SAME_RESET:
                c.append(r)
                break
        else:
            clusters.append([r])
    return clusters


def five_hour_view(session: Session, now: datetime, tokens_between) -> Optional[dict]:
    """The 5-hour window Claude meters beside the week: the one that runs out
    mid-session. It opens with the first message and resets five hours later,
    so its start is the reset less five hours. The open window is the one
    whose reset is still ahead, read with its pace, its projection (at this
    rate: the figure by the reset, or when it hits 100%), its readings for the
    line, its tokens in ten-minute slots and a budget from them, as the week
    has. The last windows are listed newest first, each with its final figure
    and when it was capped, if it was. None without any reading."""
    rs = [r for r in _readings(session, "five_hour", now - 3 * DAY) if r.ts <= now]
    if not rs:
        return None
    windows = []
    for cluster in _five_clusters(rs):
        line = _line(cluster)
        reset = line[-1].resets_at
        start = reset - FIVE
        capped = next((r for r in line if r.used_pct >= 100), None)
        windows.append({
            "start": start, "reset": reset, "line": line,
            "final_pct": line[-1].used_pct, "capped_at": capped.ts if capped else None,
            "tokens": tokens_between(start, min(reset, now)),
        })
    windows.sort(key=lambda w: w["reset"], reverse=True)
    ahead = [w for w in windows if w["reset"] > now]
    open_ = max(ahead, key=lambda w: w["line"][-1].ts) if ahead else None

    detail = None
    if open_ is not None:
        start, reset, line = open_["start"], open_["reset"], open_["line"]
        last = line[-1]
        used = last.used_pct
        elapsed = min(1.0, max(0.0, (now - start) / FIVE))
        delta = used - elapsed * 100
        projected = runs_out = None
        # a quarter of an hour in (a burst projects nonsense), and not once it is capped
        if now - start >= 0.05 * FIVE and elapsed > 0 and used < 100:
            projected = used / elapsed
            if projected > 100 and used > 0:
                runs_out = start + (now - start) * (100 / used)
        budget = _part_budget(line, (start, 0.0), tokens_between)
        left = max(0.0, budget * (100 - used) / 100 - tokens_between(last.ts, now)) if budget else None
        slots = [tokens_between(start + i * FIVE_SLOT, min(now, start + (i + 1) * FIVE_SLOT))
                 if start + i * FIVE_SLOT < now else 0 for i in range(int(FIVE / FIVE_SLOT))]
        detail = {
            "used_pct": used, "reset": _iso(reset), "at": _iso(last.ts), "machine": last.machine,
            "start": _iso(start), "elapsed_pct": round(elapsed * 100, 1),
            "minutes_left": max(0, round((reset - now) / timedelta(minutes=1))),
            "capped_at": _iso(open_["capped_at"]),
            "pace": {
                "delta_pts": round(delta, 1),
                "status": "ahead" if delta > PACE_BAND else "behind" if delta < -PACE_BAND else "on",
                "projected_pct": round(projected, 1) if projected is not None else None,
                "runs_out_at": _iso(runs_out),
            },
            "history": [{"at": _iso(r.ts), "pct": r.used_pct} for r in line],
            "slots": slots,
            "tokens": {
                "used": open_["tokens"],
                "budget": round(budget) if budget else None,
                "left": round(left) if left is not None else None,
            },
        }
    return {
        "open": detail,
        "windows": [{
            "start": _iso(w["start"]), "reset": _iso(w["reset"]), "final_pct": w["final_pct"],
            "tokens": w["tokens"], "capped_at": _iso(w["capped_at"]), "current": w is open_,
            "last_read": _iso(w["line"][-1].ts),
        } for w in windows[:FIVE_WINDOWS]],
    }


def view(session: Session, now: datetime, tz: ZoneInfo, back: int = 0) -> dict:
    """The window `back` weeks before the current one (0: this week), read to
    its own end. A past window is complete, so its figures are the week's
    finals; the pace, the projection, the 5-hour window and the booked next
    plan belong to the live week and are left out."""
    latest_week = _latest(session, "seven_day")
    cur_start, cur_reset, anchored = current_window(latest_week, now)
    back = max(0, min(PAST_WINDOWS, back)) if anchored else 0
    live = back == 0
    start, reset = cur_start - back * WEEK, cur_reset - back * WEEK
    end = min(now, reset)                   # the window's own "now"
    oldest = cur_start - PAST_WINDOWS * WEEK

    turns = session.exec(
        select(UsageTurn.ts, UsageTurn.total_tokens, UsageTurn.model_id, UsageTurn.project, UsageTurn.machine)
        .where(UsageTurn.source == SOURCE, UsageTurn.ts >= min(oldest, end - 28 * DAY), UsageTurn.ts < now)
    ).all()
    week_readings = _readings(session, "seven_day", oldest - WEEK)
    plan = _plan_of(week_readings)          # the plan now

    def tokens_between(a: datetime, b: datetime) -> int:
        return sum(t[1] for t in turns if a <= t[0] < b)

    # ── this window ─────────────────────────────────────────────────────────
    # Within a window the percentage only grows, but every open Claude Code
    # session reports the last figure it was given, so a stale one can arrive
    # late: the reading is the highest so far, and the line its running max.
    current = _line([r for r in week_readings if _in_window(r, reset)])
    reading = current[-1] if current else None
    # the plan the window in view was read on; a past week without a reading
    # names none rather than borrowing the plan of today
    wplan = _plan_of(current) or (plan if live else None)
    used_pct = reading.used_pct if reading else (0.0 if live and anchored else None)
    elapsed = min(1.0, max(0.0, (end - start) / WEEK))
    used_tokens = tokens_between(start, end)

    # each plan's part of the line has its own budget; the window's plan's is the week's
    parts = _parts(current)
    bases = [_base(part, i == 0, start) for i, part in enumerate(parts)]
    budgets = [_part_budget(part, base, tokens_between) for part, base in zip(parts, bases)]
    budget, budget_from = None, None
    if parts and budgets[-1] and _plan_of(parts[-1]) == wplan:
        budget = budgets[-1]
        budget_from = ("this week" if live else "the week's readings") if len(parts) == 1 \
            else f"the {parts[-1][-1].used_pct - bases[-1][1]:.0f} points since the switch"

    # ── past windows, on the live window's anchor: the list to browse ───────
    past = []
    for k in range(1, PAST_WINDOWS + 1):
        p_reset = cur_reset - k * WEEK
        p_start = p_reset - WEEK
        p_line = _line([r for r in week_readings if _in_window(r, p_reset)])
        top = p_line[-1] if p_line else None
        tok = tokens_between(p_start, p_reset)
        p_parts = _parts(p_line)
        p_budget = _part_budget(p_parts[-1], _base(p_parts[-1], len(p_parts) == 1, p_start), tokens_between) \
            if p_parts else None
        p_plan = top.plan if top is not None else None
        past.append({
            "back": k, "start": _iso(p_start), "reset": _iso(p_reset),
            "start_local": _local(p_start, tz).isoformat(),
            "tokens": tok, "final_pct": top.used_pct if top else None,
            "budget": round(p_budget) if p_budget else None,
            "plan": p_plan, "plan_label": plan_label(p_plan), "same_plan": p_plan == plan,
            # a week with a switch names the plan it started on too
            "switched_from_label": plan_label(_plan_of(p_parts[0])) if len(p_parts) > 1 else None,
        })
    if budget is None:
        # the weeks before the one in view, on its plan
        known = [p["budget"] for p in past if p["budget"] and p["back"] > back and p["plan"] == wplan][:4]
        if known:
            n = len(known)
            budget = sum(known) / n
            budget_from = f"the last {n} week{'s' if n > 1 else ''}" if live else f"the {n} week{'s' if n > 1 else ''} before"
    if parts:
        budgets[-1] = budget

    # the line's estimate from tokens: each part from its base, on its own budget,
    # until the last reading on its plan
    estimate = []
    for i, (part, base, b) in enumerate(zip(parts, bases, budgets)):
        if base is None or not b:
            continue
        to = None if i == len(parts) - 1 else max(part[-1].ts, _last_on(session, _plan_of(part), parts[i + 1][0].ts) or part[-1].ts)
        estimate.append({"from": _iso(base[0]), "to": _iso(to), "pct": base[1], "tokens": tokens_between(start, base[0]),
                         "budget": round(b), "plan_label": plan_label(_plan_of(part))})

    full_past = [p for p in past if p["tokens"] > 0 and p["back"] > back][:4]
    avg_day = sum(p["tokens"] for p in full_past) / (7 * len(full_past)) if full_past else None
    finals = [p["final_pct"] for p in past[:4] if p["final_pct"] is not None and p["same_plan"]]

    # ── pace ────────────────────────────────────────────────────────────────
    # "At this rate" runs at the current plan's own rate: from the window's
    # start for its first plan, from the part's base after a switch, and only
    # after a day on the new plan, since a short burst projects nonsense.
    pace = None
    if live and used_pct is not None and anchored:
        delta = used_pct - elapsed * 100
        first = len(parts) <= 1
        base = (start, 0.0) if first else bases[-1]
        projected = None
        if base is not None and now - base[0] >= (0.04 * WEEK if first else DAY):
            projected = used_pct + (used_pct - base[1]) * ((reset - now) / (now - base[0]))
        pace = {
            "delta_pts": round(delta, 1),
            "status": "ahead" if delta > PACE_BAND else "behind" if delta < -PACE_BAND else "on",
            "projected_pct": round(projected, 1) if projected is not None else None,
        }

    # a figure the status line keeps repeating is still current: stamp it with
    # the latest reading that confirms it, not the first one that reached it
    confirmed = max((r.ts for r in week_readings
                     if reading and _in_window(r, reset) and r.ts >= reading.ts
                     and r.used_pct == reading.used_pct and (not r.plan or r.plan == reading.plan)),
                    default=reading.ts if reading else None)
    unread = tokens_between(confirmed, end) if reading and live else 0

    left = None
    days_left = (reset - end) / DAY if anchored else None
    if budget:
        # what the percentage leaves, less what was spent since it was read;
        # for a past window, what the reset found unused
        remaining = max(0.0, budget * (100 - (used_pct or 0)) / 100 - tokens_between(reading.ts if reading else start, end))
        left = {
            "tokens": round(remaining),
            "per_day": round(remaining / days_left) if days_left and days_left > 0.04 else None,
        }

    # ── the window's days: 24h steps from its start ─────────────────────────
    day_pts = []
    for i in range(7):
        a, b = start + i * DAY, start + (i + 1) * DAY
        end_reading = None
        for r in current:
            if r.ts < b:
                end_reading = r.used_pct
        day_pts.append({
            "start": _iso(a), "start_local": _local(a, tz).isoformat(),
            "tokens": tokens_between(a, min(b, end)) if a < end else 0,
            "future": a >= end, "current": a <= end < b,
            "end_pct": end_reading if a < end else None,
            "pace_pct": round((i + 1) / 7 * 100, 1),
        })

    # ── the window hour by hour, for the line's readout ─────────────────────
    hourly = [0] * (7 * 24)
    for ts, tok, *_ in turns:
        if start <= ts < end:
            hourly[min(len(hourly) - 1, int((ts - start) / timedelta(hours=1)))] += tok

    # ── when: weekday × hour over the four weeks to the window's end, local time
    grid = [[0] * 24 for _ in range(7)]
    since = end - 28 * DAY
    by_project, by_machine, by_model = defaultdict(int), defaultdict(int), defaultdict(int)
    for ts, tok, model, project, machine in turns:
        if since <= ts < end:
            loc = _local(ts, tz)
            grid[loc.weekday()][loc.hour] += tok
        if start <= ts < end:
            by_project[project or "(no folder)"] += tok
            by_machine[machine] += tok
            by_model[model] += tok

    def ranked(d: dict, n: int) -> list:
        return [{"key": k, "tokens": v} for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:n]]

    five_hour = five_hour_view(session, now, tokens_between) if live else None

    return {
        "now": _iso(now),
        "tz": str(tz),
        "anchored": anchored,
        "plan": wplan,
        "plan_label": plan_label(wplan),
        "plan_change": _plan_change(session, current),
        "plan_next": next_plan(session, plan, tz) if live else None,
        "window": {
            "back": back, "start": _iso(start), "reset": _iso(reset), "end": _iso(end),
            "start_local": _local(start, tz).isoformat(), "reset_local": _local(reset, tz).isoformat(),
            "elapsed_pct": round(elapsed * 100, 1),
            "days_left": round(days_left, 2) if days_left is not None else None,
        },
        "reading": {
            "used_pct": reading.used_pct, "at": _iso(confirmed), "machine": reading.machine,
            # Claude Code spent after it and nothing read it since (#67): the page says so
            "tokens_since": unread, "stale": live and unread > 0 and end - confirmed > STALE_AFTER,
        } if reading else None,
        "used_pct": used_pct,
        "pace": pace,
        "tokens": {
            "used": used_tokens,
            "per_day_so_far": round(used_tokens / max(elapsed * 7, 1 / 24)) if anchored else round(used_tokens / 7),
            "avg_day_past": round(avg_day) if avg_day is not None else None,
            "budget": round(budget) if budget else None,
            "budget_from": budget_from,
            "estimate": estimate,
            "left": left,
        },
        "five_hour": five_hour,
        "history": [{"at": _iso(r.ts), "pct": r.used_pct, "plan_label": plan_label(r.plan)} for r in current],
        "days": day_pts,
        "hourly": hourly,
        "past": past,
        "avg_final_pct": round(sum(finals) / len(finals), 1) if finals else None,
        "hours": grid,
        "by_project": ranked(by_project, 8),
        "by_machine": ranked(by_machine, 6),
        "by_model": ranked(by_model, 6),
    }
