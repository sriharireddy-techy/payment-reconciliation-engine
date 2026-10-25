// Hash router, navigation, database status pill and theme toggle.
import { api } from "./api.js";
import * as dashboard from "./pages/dashboard.js";
import * as accounts from "./pages/accounts.js";
import * as payment from "./pages/payment.js";
import * as transactions from "./pages/transactions.js";
import * as ledger from "./pages/ledger.js";
import * as concurrency from "./pages/concurrency.js";
import * as failure from "./pages/failure.js";
import * as reconciliation from "./pages/reconciliation.js";
import * as audit from "./pages/audit.js";
import * as system from "./pages/system.js";

const ROUTES = [
  { re: /^\/?(dashboard)?$/, nav: "dashboard", page: dashboard.render },
  { re: /^\/accounts$/, nav: "accounts", page: accounts.render },
  { re: /^\/payment$/, nav: "payment", page: payment.render },
  { re: /^\/transactions$/, nav: "transactions", page: transactions.renderList },
  { re: /^\/transactions\/(\d+)$/, nav: "transactions", page: transactions.renderDetail },
  { re: /^\/ledger$/, nav: "ledger", page: ledger.render },
  { re: /^\/concurrency$/, nav: "concurrency", page: concurrency.render },
  { re: /^\/failure$/, nav: "failure", page: failure.render },
  { re: /^\/reconciliation$/, nav: "reconciliation", page: reconciliation.render },
  { re: /^\/reconciliation\/batches\/(\d+)$/, nav: "reconciliation", page: reconciliation.renderBatch },
  { re: /^\/audit$/, nav: "audit", page: audit.render },
  { re: /^\/system$/, nav: "system", page: system.render },
];

let cleanup = null;

function parseHash() {
  const raw = window.location.hash.replace(/^#/, "") || "/dashboard";
  const [path, queryString] = raw.split("?");
  return { path, query: Object.fromEntries(new URLSearchParams(queryString || "")) };
}

async function route() {
  const { path, query } = parseHash();
  const view = document.getElementById("view");
  if (typeof cleanup === "function") { try { cleanup(); } catch (e) { /* ignore */ } }
  cleanup = null;
  const match = ROUTES.map((r) => ({ r, m: r.re.exec(path) })).find((x) => x.m);
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", match && a.dataset.nav === match.r.nav));
  document.getElementById("sidebar").classList.remove("open");
  if (!match) {
    view.innerHTML = `<div class="state"><i class="bi bi-signpost"></i>Page not found. <a href="#/dashboard">Go to the dashboard</a>.</div>`;
    return;
  }
  window.scrollTo(0, 0);
  cleanup = await match.r.page(view, { params: match.m.slice(1), query });
}

export async function refreshDbPill() {
  const pill = document.getElementById("dbPill");
  const label = pill.querySelector(".label");
  pill.classList.remove("ok", "warn", "down");
  try {
    const h = await api.get("/api/health/database");
    pill.classList.add(h.status === "ok" ? "ok" : "warn");
    label.textContent = h.status === "ok" ? `Oracle connected (${h.database_user})` : "Connected - schema mismatch";
  } catch (err) {
    pill.classList.add("down");
    const status = err.body && err.body.status;
    label.textContent = status === "not_configured" ? "Database not configured" : "Database unavailable";
  }
}

function setupTheme() {
  const button = document.getElementById("themeToggle");
  const apply = (theme) => {
    document.documentElement.setAttribute("data-bs-theme", theme);
    button.innerHTML = theme === "dark" ? '<i class="bi bi-sun"></i>' : '<i class="bi bi-moon-stars"></i>';
  };
  apply(document.documentElement.getAttribute("data-bs-theme") || "light");
  button.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
    apply(next);
    try { localStorage.setItem("prc-theme", next); } catch (e) { /* storage unavailable */ }
    route(); // re-render so charts pick up the new colours
  });
}

document.getElementById("menuToggle").addEventListener("click", () => document.getElementById("sidebar").classList.toggle("open"));
window.addEventListener("hashchange", route);
setupTheme();
refreshDbPill();
route();
