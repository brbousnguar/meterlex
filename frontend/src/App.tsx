import { useEffect, useState } from "react";
import { api, type Health } from "./api";
import Dashboard from "./components/Dashboard";
import ToolDetail from "./components/ToolDetail";
import PricesSettings from "./components/PricesSettings";

type View = { name: "dashboard" } | { name: "tool"; source: string } | { name: "prices" };

const TOOLS = [
  { source: "claude-code", label: "Claude Code", icon: "◆" },
  { source: "codex",       label: "Codex",        icon: "⬡" },
  { source: "antigravity", label: "Antigravity",  icon: "⬢" },
  { source: "gemini-cli",  label: "Gemini CLI",   icon: "✦" },
  { source: "ollama",      label: "Ollama",        icon: "◎" },
  { source: "copilot",     label: "Copilot",       icon: "⊗" },
];

export default function App() {
  const [view, setView]     = useState<View>({ name: "dashboard" });
  const [health, setHealth] = useState<Health | null>(null);

  const refreshHealth = () => api.health().then(setHealth).catch(() => {});

  useEffect(() => {
    refreshHealth();
    const t = setInterval(refreshHealth, 30_000);
    return () => clearInterval(t);
  }, []);

  const go = (v: View) => { setView(v); window.scrollTo(0, 0); };

  return (
    <>
      <aside className="sidebar">
        <div className="brand">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <img src="/brand-mark.svg" alt="" width="34" height="34" style={{ flexShrink: 0 }} />
            <div>
              <div className="brand-name">Meterlex</div>
              <div className="brand-kicker">coding agent tracker</div>
            </div>
          </div>
        </div>

        <nav className="nav">
          <button type="button" data-testid="nav-dashboard" aria-label="Open dashboard" className={`nav-item ${view.name === "dashboard" ? "active" : ""}`} onClick={() => go({ name: "dashboard" })}>
            <span className="nav-icon">📊</span> Dashboard
          </button>

          <div className="nav-section">Tools</div>
          {TOOLS.map((t) => (
            <button type="button" key={t.source} data-testid={`nav-${t.source}`} aria-label={`Open ${t.label} details`}
              className={`nav-item ${view.name === "tool" && view.source === t.source ? "active" : ""}`}
              onClick={() => go({ name: "tool", source: t.source })}
            >
              <span className="nav-icon">{t.icon}</span> {t.label}
            </button>
          ))}

          <div className="nav-section" style={{ marginTop: 8 }}>Config</div>
          <button type="button" data-testid="nav-prices" aria-label="Open prices and settings" className={`nav-item ${view.name === "prices" ? "active" : ""}`} onClick={() => go({ name: "prices" })}>
            <span className="nav-icon">💱</span> Prices &amp; settings
          </button>
        </nav>

        <div className="sidebar-foot">
          {health ? (
            <>
              <div>{health.total_turns.toLocaleString("fr-FR")} turns indexed</div>
              {Object.entries(health.by_source).map(([src, n]) => (
                <div key={src} className="muted">{src}: {n.toLocaleString("fr-FR")}</div>
              ))}
            </>
          ) : "loading…"}
        </div>
      </aside>

      <main className="main">
        {view.name === "dashboard" && <Dashboard onSelectTool={(s) => go({ name: "tool", source: s })} onReload={refreshHealth} />}
        {view.name === "tool"      && <ToolDetail source={view.source} onBack={() => go({ name: "dashboard" })} />}
        {view.name === "prices"    && <PricesSettings onReload={refreshHealth} />}
      </main>
    </>
  );
}
