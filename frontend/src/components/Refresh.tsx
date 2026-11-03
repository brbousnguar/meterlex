import { type RefObject, useCallback, useEffect, useRef, useState } from "react";

/* Refreshing, the house way: one mark for the header button and for pull to refresh. First
   shipped in Vitalex and Cooklex (vitalex #259); see references/mobile-patterns.md §15.

   Copy this file to `src/components/Refresh.tsx` and `refresh.css` into the app's stylesheet,
   then map the `--refresh-*` tokens to the app's own colours. Plain JS app: delete the types and
   it runs as JSX. No-build app: `refresh.js` is the same thing without React.

   - `PullToRefresh`   installed app only: a circle follows the finger while its ring fills, fills
                       with the accent when letting go will refresh, then spins and shows a check.
   - `RefreshButton`   a header button for the browser tab and desktop (the CSS hides it in the
                       installed phone app, which has the pull).
   - `useForegroundRefresh`  back in the app after a minute away: the page refreshes quietly.

   `onRefresh` resolves when the data is in, or throws (no check after a failure). Its default
   reloads the page, which with the house network-first service worker also takes the newest
   build. Pass the app's own refetch when it has one: no flash, scroll and drafts kept. */

const TURN = 600;  // ms per turn while loading
const DONE = 900;  // how long the check stays
const READY = 80;  // finger travel, px, that refreshes on release
const REST = 84;   // where the circle waits while it refreshes
const MAX = 120;   // how far it can be pulled

/** Open overlays in this app: a pull does nothing over them (a draft would be lost), and coming
    back to the app doesn't refresh under them either. Meterlex has no sheets: its drafts are the
    forms on More and Credits, which mark themselves `data-draft` while they hold an unsaved edit. */
export const OVERLAYS = "[role=dialog], [aria-modal=true], [data-draft]";
/** The app shell. A pinned shell (#root position:fixed, <main> scrolls) is fixed itself: the walk
    up from the finger stops here, so only fixed things *inside* the app block a pull. */
const SHELL = "#root";

type Phase = "idle" | "busy" | "done";
type Run = () => unknown;

/** A full reload never resolves: the arrow turns until the new page replaces this one. */
export const reload: Run = () => new Promise<never>(() => location.reload());

const installed = () =>
  matchMedia("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, Math.max(ms, 0)));

/** Runs `run` one at a time and drives the mark: "busy" while it loads and while the arrow
    finishes its turn, "done" while the check shows (not after a failure), then "idle". Only the
    arrow's own svg turns, so it turns about its centre: turning the circle around it knocked
    Cooklex's icon 8px off centre. */
export function useRefreshRun(run: Run) {
  const [phase, setPhase] = useState<Phase>("idle");
  const arrow = useRef<SVGSVGElement>(null);
  const runRef = useRef(run);
  runRef.current = run;
  const running = useRef(false);

  // `from`: the angle the arrow is already at (the pull turns it).
  const start = useCallback(async (from = 0) => {
    if (running.current) return;
    running.current = true;
    setPhase("busy");
    const el = arrow.current;
    const spin = el && !reducedMotion()
      ? el.animate([{ transform: `rotate(${from}deg)` }, { transform: `rotate(${from + 360}deg)` }], { duration: TURN, iterations: Infinity })
      : null;
    const t0 = performance.now();
    let ok = true;
    try { await runRef.current(); } catch { ok = false; }
    if (!el || !spin) {
      // Nothing turns with reduced motion: the dimmed arrow still stays a turn's length, so a
      // 10 ms load isn't a flash too short to see.
      await sleep(TURN - (performance.now() - t0));
    } else {
      // From where it is to the next whole turn, a quarter turn at least, at the speed it was
      // spinning and slowing to rest: a 10 ms load still gets one smooth turn, not three frames
      // and a snap back to 0°.
      const at = (from + ((Number(spin.currentTime) || 0) / TURN) * 360) % 360;
      const to = at > 270 ? 720 : 360;
      spin.cancel();
      const travel = to - at;
      await el.animate([{ transform: `rotate(${at}deg)` }, { transform: `rotate(${to}deg)` }], {
        duration: Math.min(Math.max((2 * travel * TURN) / 360, 300), 1000),
        easing: "cubic-bezier(0.5, 1, 0.89, 1)", // ease-out quad: starts at twice its mean speed
      }).finished.catch(() => {});
    }
    if (ok) {
      setPhase("done");
      await sleep(DONE);
    }
    setPhase("idle");
    running.current = false;
  }, []);

  return { phase, start, arrow };
}

/** The arrow and the check share one cell and cross-fade (refresh.css). */
export function RefreshMark({ phase, arrow, turn, size = 20 }: {
  phase: Phase; arrow: RefObject<SVGSVGElement>; turn?: number; size?: number;
}) {
  const svg = { viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor", strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true } as const;
  return (
    <span className="refresh-mark" data-phase={phase}>
      <svg ref={arrow} className="refresh-arrow" strokeWidth={1.75} style={turn ? { transform: `rotate(${turn}deg)` } : undefined} {...svg}>
        <path d="M20 12a8 8 0 1 1-2.3-5.6" />
        <path d="M20 4v5h-5" />
      </svg>
      <svg className="refresh-check" strokeWidth={2.25} {...svg}>
        <path d="M5 12.5l4.5 4.5L19 7.5" />
      </svg>
    </span>
  );
}

/** A 44px header button: the mark in a 36px disc that takes the accent while the check shows. */
export function RefreshButton({ onRefresh = reload, className = "" }: { onRefresh?: Run; className?: string }) {
  const { phase, start, arrow } = useRefreshRun(onRefresh);
  return (
    <>
      <button type="button" onClick={() => start()} aria-label="Refresh" title="Refresh" aria-busy={phase === "busy"}
              className={`refresh-button ${className}`} data-phase={phase}>
        <span className="refresh-disc"><RefreshMark phase={phase} arrow={arrow} /></span>
      </button>
      <span className="refresh-sr" role="status">{phase === "done" ? "Updated" : ""}</span>
    </>
  );
}

function canPull(target: EventTarget | null) {
  if (!(target instanceof Element) || document.querySelector(OVERLAYS)) return false;
  if ((document.scrollingElement?.scrollTop ?? window.scrollY) > 0) return false;
  for (let el: Element | null = target; el && el !== document.body && !el.matches(SHELL); el = el.parentElement) {
    if (el.scrollTop > 0 || getComputedStyle(el).position === "fixed" || el.matches("input, textarea, select, [data-no-pull]")) return false;
  }
  return true;
}

/** Mount once at the app root, after the sheets. Off in a browser tab (it has its own reload),
    over an open overlay, from inside anything fixed and from a pane that has scrolled. */
export function PullToRefresh({ onRefresh = reload }: { onRefresh?: Run }) {
  const [pull, setPull] = useState(0);
  const { phase, start, arrow } = useRefreshRun(onRefresh);
  // In refs: the listeners are bound once and must not re-bind mid-pull.
  const startRef = useRef(start);
  startRef.current = start;
  const busy = useRef(false);
  busy.current = phase !== "idle";

  useEffect(() => {
    if (!installed()) return;
    let origin: { x: number; y: number } | null = null;
    let travel = 0;
    const reset = () => { origin = null; travel = 0; setPull(0); };
    const down = (e: TouchEvent) => {
      const t = e.touches[0];
      origin = e.touches.length === 1 && !busy.current && canPull(e.target) ? { x: t.clientX, y: t.clientY } : null;
      travel = 0;
    };
    const move = (e: TouchEvent) => {
      if (!origin) return;
      const dx = e.touches[0].clientX - origin.x;
      const dy = e.touches[0].clientY - origin.y;
      // A finger going up first is a scroll, sideways a swipe or a strip: let it be.
      if (travel === 0 && (dy <= 0 || Math.abs(dx) > dy)) { origin = null; return; }
      if (e.cancelable) e.preventDefault(); // the page stays put while the circle comes down
      travel = Math.max(dy, 0);
      setPull(travel);
    };
    const up = () => {
      if (!origin) return;
      const go = travel >= READY;
      reset();
      if (go) startRef.current(180);
    };
    document.addEventListener("touchstart", down, { passive: true });
    document.addEventListener("touchmove", move, { passive: false });
    document.addEventListener("touchend", up, { passive: true });
    document.addEventListener("touchcancel", reset, { passive: true });
    return () => {
      document.removeEventListener("touchstart", down);
      document.removeEventListener("touchmove", move);
      document.removeEventListener("touchend", up);
      document.removeEventListener("touchcancel", reset);
    };
  }, []);

  const progress = Math.min(pull / READY, 1);
  const state = phase !== "idle" ? phase : !pull ? "idle" : progress >= 1 ? "ready" : "pull";
  // 1:1 with the finger up to READY, then it resists.
  const y = phase !== "idle" ? REST : Math.min(pull <= READY ? pull : READY + (pull - READY) * 0.3, MAX);
  return (
    <div className="ptr" data-testid="ptr" data-state={state} aria-hidden={state === "idle"} style={{ transform: `translate(-50%, ${y}px)` }}>
      <svg className="ptr-ring" viewBox="0 0 40 40" aria-hidden="true">
        <circle cx="20" cy="20" r="17" pathLength={100} strokeDasharray={`${progress * 100} 100`} />
      </svg>
      <RefreshMark phase={phase} arrow={arrow} turn={phase === "idle" ? progress * 180 : 0} />
      <span className="refresh-sr" role="status">
        {state === "pull" ? "Pull to refresh" : state === "ready" ? "Release to refresh" : state === "busy" ? "Refreshing" : state === "done" ? "Updated" : ""}
      </span>
    </div>
  );
}

/** Back in the app after `away` ms or more: `run` refreshes the page quietly, without the mark.
    Never under an open overlay. Pass the app's own refetch, never `reload`: a reload nobody asked
    for loses the screen he came back to. */
export function useForegroundRefresh(run: Run, away = 60_000) {
  const runRef = useRef(run);
  runRef.current = run;
  useEffect(() => {
    let hiddenAt: number | null = null;
    const onChange = () => {
      if (document.visibilityState === "hidden") { hiddenAt = Date.now(); return; }
      if (hiddenAt !== null && Date.now() - hiddenAt >= away && !document.querySelector(OVERLAYS)) {
        Promise.resolve().then(() => runRef.current()).catch(() => {});
      }
      hiddenAt = null;
    };
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, [away]);
}
