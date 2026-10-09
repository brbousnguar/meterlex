import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** Moves one thumb to the current child of a control, so a switch slides instead of
 *  jumping. The thumb is an element that stays: rebuild it and it cannot slide. */
export function useThumb<T extends HTMLElement>(current: string) {
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
export default function Seg<T extends string>({ label, value, options, onPick }: {
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
