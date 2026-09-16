import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

// The service worker only makes the shell survive a dropped connection; if it
// cannot register (no HTTPS, a private window), the app still works.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
