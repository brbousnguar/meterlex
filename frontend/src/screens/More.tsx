import { useEffect, useState } from "react";
import { api, type Config, type Health, type MachineInfo, type Settings } from "../api";
import { ago, fmtEur, fmtInt, fmtTok, harness } from "../lib";

const SUBS: { key: keyof Settings; source: string }[] = [
  { key: "sub_claude_code_eur",  source: "claude-code" },
  { key: "sub_codex_eur",        source: "codex" },
  { key: "sub_antigravity_eur",  source: "antigravity" },
  { key: "sub_ollama_eur",       source: "ollama" },
  { key: "sub_copilot_eur",      source: "copilot" },
  // OpenClaw has no line here: its agents run on the plans above, and a fee
  // entered twice is a fee counted twice.
];

/** Everything that is not a reading: the link to the rate card, what the
 *  subscriptions cost, and the state of the collectors. */
export default function More({ onReload }: { onReload: () => void }) {
  const [cfg, setCfg] = useState<Config | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [form, setForm] = useState<Partial<Settings>>({});
  const [machines, setMachines] = useState<MachineInfo[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.config(), api.settings(), api.machines(), api.health()])
      .then(([c, s, m, h]) => { setCfg(c); setSettings(s); setForm(s); setMachines(m); setHealth(h); })
      .catch(() => setNote("Could not reach the hub."));
  }, []);

  const save = async () => {
    setBusy(true);
    try {
      const updated = await api.patchSettings(form);
      setSettings(updated); setForm(updated);
      setNote("Saved. Costs are unchanged until you recompute.");
      onReload();
    } catch { setNote("Could not save."); }
    finally { setBusy(false); }
  };

  const recompute = async () => {
    setBusy(true);
    try {
      const r = await api.recompute();
      setNote(`Repriced ${fmtInt(r.rows_updated)} replies.`);
      onReload();
    } catch { setNote("Could not recompute."); }
    finally { setBusy(false); }
  };

  return (
    <>
      <section className="section" style={{ marginTop: 6 }}>
        <div className="section-head"><h2 className="section-title">Rates</h2></div>
        {cfg?.prices_url ? (
          <a className="linkout" href={cfg.prices_url} target="_blank" rel="noopener noreferrer">
            <div>
              <b>Rate card</b>
              <span>Prices per model live in the model-prices app. Meterlex mirrors them; it never keeps its own.</span>
            </div>
          </a>
        ) : (
          <p className="hint">
            Set <span className="mono">PRICES_URL</span> in the hub's <span className="mono">.env</span> to link
            the rate card from here.
          </p>
        )}
      </section>

      {cfg?.openclaw_spend_url && (
        <section className="section">
          <div className="section-head"><h2 className="section-title">Agents</h2></div>
          <a className="linkout" href={cfg.openclaw_spend_url} target="_blank" rel="noopener noreferrer">
            <div>
              <b>OpenClaw spend</b>
              <span>
                Per agent, per session and per channel. Meterlex counts the same runs as the
                OpenClaw harness; that app is where the detail lives.
              </span>
            </div>
          </a>
        </section>
      )}

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">What you pay</h2>
          <div className="section-note">per month, in euros</div>
        </div>
        {!settings ? <div className="empty">Loading…</div> : (
          <>
            {SUBS.map(({ key, source }) => (
              <div className="field" key={key}>
                <label htmlFor={key}>{harness(source).label} <span className="hint">· {harness(source).note}</span></label>
                <input id={key} type="number" inputMode="decimal" step="0.01"
                       value={form[key] ?? ""}
                       onChange={(e) => setForm((f) => ({ ...f, [key]: parseFloat(e.target.value) || 0 }))} />
              </div>
            ))}
            <div className="field">
              <label htmlFor="fx">Dollar to euro</label>
              <input id="fx" type="number" inputMode="decimal" step="0.001"
                     value={form.fx_rate ?? ""}
                     onChange={(e) => setForm((f) => ({ ...f, fx_rate: parseFloat(e.target.value) || 0 }))} />
            </div>
            <div style={{ display: "flex", gap: 10, marginTop: 14, flexWrap: "wrap" }}>
              <button className="linkout" style={{ flex: 1, minWidth: 150, justifyContent: "center" }}
                      onClick={save} disabled={busy}>
                <b>Save</b>
              </button>
              <button className="linkout" style={{ flex: 1, minWidth: 150, justifyContent: "center" }}
                      onClick={recompute} disabled={busy}>
                <b>Reprice everything</b>
              </button>
            </div>
            {note && <p className="hint" style={{ marginTop: 10 }}>{note}</p>}
          </>
        )}
      </section>

      <section className="section">
        <div className="section-head">
          <h2 className="section-title">Collectors</h2>
          <div className="section-note">{machines.filter((m) => !m.revoked).length} machines</div>
        </div>
        <div className="rank">
          {machines.map((m) => {
            const seen = ago(m.last_seen_at);
            return (
              <div className="rank-row" key={m.name}>
                <div className="rank-name">
                  <span className="mono">{m.name}</span>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div className="rank-value">{fmtTok(m.total_tokens)}</div>
                  <div className="rank-sub">{fmtInt(m.turns)} replies</div>
                </div>
                <div className="meter-foot" style={{ gridColumn: "1 / -1" }}>
                  <span className="live" data-state={seen.state}>{seen.text}</span>
                  <span>projects stored as {m.labels === "full" ? "full paths" : m.labels === "basename" ? "folder names" : "hashes"}</span>
                  {m.collector_version && <span>collector {m.collector_version}</span>}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="section">
        <div className="section-head"><h2 className="section-title">This hub</h2></div>
        <div className="meter-foot" style={{ display: "grid", gap: 4 }}>
          <span>{health ? `${fmtInt(health.total_turns)} replies stored` : "…"}</span>
          <span>periods are counted in {cfg?.tz ?? "local time"}, weeks start Monday</span>
          <span>prices mirrored from the central rate card{health?.last_ingested_at ? `, last reading ${ago(health.last_ingested_at).text}` : ""}</span>
          {health && (
            <span>
              {Object.entries(health.by_source).sort((a, b) => b[1] - a[1])
                .map(([s, n]) => `${harness(s).label} ${fmtInt(n)}`).join(" · ")}
            </span>
          )}
        </div>
      </section>
    </>
  );
}
