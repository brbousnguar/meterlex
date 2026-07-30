import { useState } from "react";

export default function InfoTip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="infotip" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <i className="infotip-icon">ⓘ</i>
      {open && <span className="infotip-popover">{text}</span>}
    </span>
  );
}
