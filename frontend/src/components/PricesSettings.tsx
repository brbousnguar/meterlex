import { useEffect, useRef, useState } from "react";
import { api, type PriceRow, type Settings, type ManualBill } from "../api";

function PriceCell({ value, onSave }: { value: number; onSave: (v: number) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String((value * 1_000_000).toFixed(4)));
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => { if (editing) ref.current?.select(); }, [editing]);

  const commit = () => {
    const n = parseFloat(draft);
    if (!isNaN(n)) onSave(n / 1_000_000);
    setEditing(false);
  };

  if (editing) {
    return (
      <input
        ref={ref}
        className="price-input"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") setEditing(false); }}
        autoFocus
      />
    );
  }
  return (
    <span
      className="mono"
      style={{ cursor: "pointer", borderBottom: "1px dashed var(--border-solid)", paddingBottom: 1 }}
      onClick={() => { setDraft(String((value * 1_000_000).toFixed(4))); setEditing(true); }}
      title="Click to edit"
    >
      ${(value * 1_000_000).toFixed(2)}
    </span>
  );
}

export default function PricesSettings({ onReload }: { onReload: () => void }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [prices,   setPrices]   = useState<PriceRow[]>([]);
  const [form,     setForm]     = useState<Partial<Settings>>({});
  const [saving,   setSaving]   = useState(false);
  const [saved,    setSaved]    = useState(false);
  const [recompMsg, setRecompMsg] = useState<string | null>(null);
  const [copilotBills, setCopilotBills] = useState<ManualBill[]>([]);
  const [newBillYm, setNewBillYm] = useState("");
  const [newBillAmt, setNewBillAmt] = useState("");

  useEffect(() => {
    Promise.all([api.settings(), api.prices(), api.bills("copilot")]).then(([s, p, b]) => {
      setSettings(s); setForm(s); setPrices(p); setCopilotBills(b);
    });
  }, []);

  const saveSettings = async () => {
    setSaving(true);
    try {
      const updated = await api.patchSettings(form);
      setSettings(updated); setForm(updated);
      setSaved(true); setTimeout(() => setSaved(false), 2500);
      onReload();
    } catch (e) { console.error(e); }
    finally { setSaving(false); }
  };

  const recompute = async () => {
    setSaving(true);
    try {
      const r = await api.recompute();
      setRecompMsg(`Recomputed ${r.rows_updated} rows`);
      setTimeout(() => setRecompMsg(null), 3000);
      onReload();
    } catch (e) { console.error(e); }
    finally { setSaving(false); }
  };

  const patchPrice = async (id: string, field: "prompt" | "completion" | "cache_read" | "cache_write", val: number) => {
    try {
      await api.patchPrice(id, { [field]: val });
      setPrices((prev) => prev.map((p) => p.model_id === id ? { ...p, [field]: val } : p));
    } catch (e) { console.error(e); }
  };

  const saveBill = async (year_month: string, amount_eur: number) => {
    try {
      const updated = await api.patchBill("copilot", year_month, { amount_eur });
      setCopilotBills((prev) => {
        const idx = prev.findIndex((b) => b.year_month === year_month);
        if (idx >= 0) return prev.map((b, i) => i === idx ? updated : b);
        return [...prev, updated].sort((a, b) => a.year_month.localeCompare(b.year_month));
      });
    } catch (e) { console.error(e); }
  };

  const addBill = async () => {
    if (!newBillYm || !newBillAmt) return;
    await saveBill(newBillYm, parseFloat(newBillAmt));
    setNewBillYm(""); setNewBillAmt("");
  };

  if (!settings) return <div className="content"><div className="empty">Loading…</div></div>;

  const numInput = (key: keyof Settings, label: string, step = "0.01", unit = "€") => (
    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <label style={{ fontSize: ".68rem", textTransform: "uppercase", letterSpacing: ".08em", color: "var(--text-dim)", fontFamily: "var(--mono)" }}>{label}</label>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: ".84rem", color: "var(--text-dim)" }}>{unit}</span>
        <input
          type="number"
          step={step}
          value={form[key] ?? ""}
          onChange={(e) => setForm((f) => ({ ...f, [key]: parseFloat(e.target.value) || 0 }))}
          style={{ width: 110 }}
        />
      </div>
    </div>
  );

  return (
    <>
      <div className="topbar">
        <div className="page-title">Prices &amp; settings</div>
        <div className="topbar-actions">
          <button className="btn ghost sm" onClick={recompute} disabled={saving}>Recompute all costs</button>
          {recompMsg && <span style={{ fontFamily: "var(--mono)", fontSize: ".76rem", color: "var(--accent2)" }}>{recompMsg}</span>}
        </div>
      </div>

      <div className="content">
        {/* Subscription settings */}
        <div className="chart-box">
          <div className="section-title">Monthly subscriptions</div>
          <p style={{ fontSize: ".78rem", color: "var(--text-dim)", marginBottom: 18, fontFamily: "var(--mono)", lineHeight: 1.5 }}>
            Compared against API-equivalent token cost to compute savings. Set to your actual monthly cost for each tool.
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 18, marginBottom: 20 }}>
            {numInput("sub_claude_code_eur", "Claude Code / mo")}
            {numInput("sub_codex_eur",       "Codex / mo")}
            {numInput("sub_antigravity_eur", "Antigravity / mo")}
            {numInput("sub_ollama_eur",      "Ollama / mo")}
            {numInput("sub_copilot_eur",     "Copilot / mo (default)")}
            {numInput("fx_rate",             "USD → EUR rate", "0.001", "")}
          </div>
          <div className="row-flex">
            <button className="btn sm" onClick={saveSettings} disabled={saving}>{saving ? "Saving…" : "Save settings"}</button>
            {saved && <span style={{ fontFamily: "var(--mono)", fontSize: ".76rem", color: "var(--accent2)" }}>✓ Saved</span>}
          </div>
        </div>

        {/* Copilot billing history */}
        <div className="chart-box">
          <div className="section-title">Copilot billing history</div>
          <p style={{ fontSize: ".78rem", color: "var(--text-dim)", marginBottom: 14, fontFamily: "var(--mono)", lineHeight: 1.5 }}>
            Per-month actual billing for GitHub Copilot (varies with seat pricing). Overrides the default above for each listed month.
          </p>
          <table className="tbl" style={{ marginBottom: 16 }}>
            <thead>
              <tr>
                <th>Month</th>
                <th className="right">Amount (€)</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {copilotBills.map((b) => (
                <BillRow key={b.year_month} bill={b} onSave={saveBill} />
              ))}
            </tbody>
          </table>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <input
              type="month"
              value={newBillYm}
              onChange={(e) => setNewBillYm(e.target.value)}
              style={{ width: 160 }}
            />
            <input
              type="number"
              step="0.01"
              placeholder="€ amount"
              value={newBillAmt}
              onChange={(e) => setNewBillAmt(e.target.value)}
              style={{ width: 120 }}
            />
            <button className="btn sm" onClick={addBill}>Add month</button>
          </div>
        </div>

        {/* Model prices */}
        <div className="chart-box">
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <div className="section-title" style={{ margin: 0 }}>Model prices</div>
            <span style={{ fontFamily: "var(--mono)", fontSize: ".68rem", color: "var(--text-faint)" }}>USD per 1M tokens — click a price to edit</span>
          </div>
          <table className="tbl">
            <thead>
              <tr>
                <th>Model</th>
                <th>Provider</th>
                <th className="right">
                  Input /M
                  <InfoTipInline text="Prompt token price in USD per 1M tokens." />
                </th>
                <th className="right">Output /M</th>
                <th className="right">Cache R /M</th>
                <th className="right">Cache W /M</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {prices.map((p) => (
                <tr key={p.model_id}>
                  <td className="mono">{p.display_name || p.model_id}</td>
                  <td className="dim" style={{ fontSize: ".78rem" }}>{p.provider}</td>
                  <td className="right"><PriceCell value={p.prompt}      onSave={(v) => patchPrice(p.model_id, "prompt",      v)} /></td>
                  <td className="right"><PriceCell value={p.completion}  onSave={(v) => patchPrice(p.model_id, "completion",  v)} /></td>
                  <td className="right"><PriceCell value={p.cache_read}  onSave={(v) => patchPrice(p.model_id, "cache_read",  v)} /></td>
                  <td className="right"><PriceCell value={p.cache_write} onSave={(v) => patchPrice(p.model_id, "cache_write", v)} /></td>
                  <td>
                    <span className={`badge ${p.source === "manual" ? "manual" : p.source === "seed" ? "seed" : ""}`}>
                      {p.source}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ fontFamily: "var(--mono)", fontSize: ".68rem", color: "var(--text-faint)", marginTop: 12 }}>
            After editing prices, click "Recompute all costs" above to apply the new rates to all historical turns.
          </p>
        </div>
      </div>
    </>
  );
}

function BillRow({ bill, onSave }: { bill: ManualBill; onSave: (ym: string, amt: number) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(bill.amount_eur));

  const commit = () => {
    const n = parseFloat(draft);
    if (!isNaN(n)) onSave(bill.year_month, n);
    setEditing(false);
  };

  return (
    <tr>
      <td className="mono">{bill.year_month}</td>
      <td className="right mono">
        {editing ? (
          <input
            className="price-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") setEditing(false); }}
            autoFocus
            style={{ width: 80 }}
          />
        ) : (
          <span
            style={{ cursor: "pointer", borderBottom: "1px dashed var(--border-solid)", paddingBottom: 1 }}
            onClick={() => { setDraft(String(bill.amount_eur)); setEditing(true); }}
          >
            €{bill.amount_eur.toFixed(2)}
          </span>
        )}
      </td>
      <td className="dim" style={{ fontSize: ".72rem" }}>click to edit</td>
    </tr>
  );
}

function InfoTipInline({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="infotip" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <i className="infotip-icon">ⓘ</i>
      {open && <span className="infotip-popover">{text}</span>}
    </span>
  );
}
