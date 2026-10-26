// Shared UI helpers: escaping, formatting, badges, states, dialogs and toasts.
// All dynamic text goes through esc() before it is placed in HTML.

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

const moneyFormats = new Map();
// Amounts arrive as exact decimal strings ("1500.00"); Intl formats them without float maths.
export function money(value, currency = "INR") {
  if (value === null || value === undefined || value === "") return "&ndash;";
  const code = currency || "INR";
  if (!moneyFormats.has(code)) {
    try {
      moneyFormats.set(code, new Intl.NumberFormat("en-IN", { style: "currency", currency: code, minimumFractionDigits: 2 }));
    } catch (e) {
      moneyFormats.set(code, new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
    }
  }
  return esc(moneyFormats.get(code).format(value));
}

export function number(value) {
  if (value === null || value === undefined) return "&ndash;";
  return esc(new Intl.NumberFormat("en-IN").format(value));
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Timestamps are database server time without a zone; show them as-is (no browser conversion).
export function dateTime(iso) {
  if (!iso) return "&ndash;";
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(iso);
  if (!m) return esc(iso);
  const date = `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
  return m[4] ? `${date}, ${m[4]}:${m[5]}:${m[6] || "00"}` : date;
}
export const dateOnly = (iso) => (iso ? dateTime(String(iso).slice(0, 10)) : "&ndash;");

export function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const STATUS = {
  SUCCESS: ["good", "bi-check-circle-fill"],
  COMPLETED: ["good", "bi-check-circle-fill"],
  ACTIVE: ["good", "bi-check-circle-fill"],
  VERIFIED: ["good", "bi-patch-check-fill"],
  MATCH: ["good", "bi-check-circle-fill"],
  MANUALLY_RESOLVED: ["good", "bi-check2-square"],
  AUTO_RESOLVED: ["good", "bi-check2-square"],
  FAILED: ["critical", "bi-x-circle-fill"],
  FAILURE: ["critical", "bi-x-circle-fill"],
  ERROR: ["critical", "bi-exclamation-octagon-fill"],
  CLOSED: ["critical", "bi-x-circle-fill"],
  REJECTED: ["critical", "bi-x-circle-fill"],
  UNRESOLVED: ["critical", "bi-exclamation-circle-fill"],
  INITIATED: ["warning", "bi-hourglass-split"],
  PROCESSING: ["warning", "bi-arrow-repeat"],
  PENDING: ["warning", "bi-hourglass-split"],
  OPEN: ["warning", "bi-hourglass-split"],
  FROZEN: ["warning", "bi-snow"],
  SUSPENDED: ["warning", "bi-pause-circle-fill"],
  MAINTENANCE: ["warning", "bi-tools"],
  REVERSED: ["serious", "bi-arrow-counterclockwise"],
  REFUNDED: ["serious", "bi-arrow-counterclockwise"],
  WRITTEN_OFF: ["serious", "bi-journal-x"],
  INACTIVE: ["neutral", "bi-dash-circle"],
  REPLAYED: ["info", "bi-arrow-repeat"],
  AMOUNT_MISMATCH: ["critical", "bi-cash-coin"],
  STATUS_MISMATCH: ["serious", "bi-signpost-split"],
  MISSING_IN_GATEWAY: ["warning", "bi-cloud-slash"],
  MISSING_IN_INTERNAL: ["warning", "bi-database-x"],
  DUPLICATE_EXTERNAL: ["serious", "bi-files"],
};

export function badge(status) {
  if (!status) return "&ndash;";
  const [tone, icon] = STATUS[status] || ["neutral", "bi-circle"];
  return `<span class="status status-${tone}"><i class="bi ${icon}"></i>${esc(String(status).replaceAll("_", " "))}</span>`;
}

export const simBadge = () => `<span class="sim-badge" title="Simulated record - not real gateway data"><i class="bi bi-flask"></i>SIM</span>`;

export function loading(el, text = "Loading from Oracle&hellip;") {
  el.innerHTML = `<div class="state"><div class="spinner-border spinner-border-sm text-secondary mb-2" role="status"></div><div>${text}</div></div>`;
}

export function empty(el, text) {
  el.innerHTML = `<div class="state"><i class="bi bi-inbox"></i>${esc(text)}</div>`;
}

export function errorHtml(err) {
  const notConfigured = err && (err.code === "DB_NOT_CONFIGURED" || err.code === "DB_UNAVAILABLE");
  const hint = notConfigured
    ? `<div class="mt-2"><a href="#/system">Open Database health</a> for the setup steps.</div>`
    : "";
  return `<div class="state error"><i class="bi bi-exclamation-triangle"></i>
    <div>${esc(err && err.message ? err.message : "Something went wrong.")}</div>
    ${err && err.code ? `<div class="code">${esc(err.code)}</div>` : ""}${hint}</div>`;
}

export function showError(el, err) {
  el.innerHTML = errorHtml(err);
}

export function toast(message, tone = "info") {
  const icons = { success: "bi-check-circle-fill", danger: "bi-x-circle-fill", warning: "bi-exclamation-triangle-fill", info: "bi-info-circle-fill" };
  const holder = document.getElementById("toasts");
  const node = document.createElement("div");
  node.className = `toast align-items-center text-bg-${tone} border-0`;
  node.setAttribute("role", "status");
  node.innerHTML = `<div class="d-flex"><div class="toast-body"><i class="bi ${icons[tone] || icons.info} me-2"></i>${esc(message)}</div>
    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button></div>`;
  holder.appendChild(node);
  const t = new bootstrap.Toast(node, { delay: 5000 });
  node.addEventListener("hidden.bs.toast", () => node.remove());
  t.show();
}

// Promise-based confirmation using a Bootstrap modal (never window.confirm).
export function confirmDialog({ title, bodyHtml, confirmText = "Confirm", tone = "primary" }) {
  return new Promise((resolve) => {
    const el = document.getElementById("confirmModal");
    document.getElementById("confirmTitle").textContent = title;
    document.getElementById("confirmBody").innerHTML = bodyHtml;
    const ok = document.getElementById("confirmOk");
    ok.textContent = confirmText;
    ok.className = `btn btn-${tone}`;
    const modal = bootstrap.Modal.getOrCreateInstance(el);
    let accepted = false;
    const onOk = () => { accepted = true; modal.hide(); };
    ok.addEventListener("click", onOk, { once: true });
    el.addEventListener("hidden.bs.modal", () => { ok.removeEventListener("click", onOk); resolve(accepted); }, { once: true });
    modal.show();
  });
}

export function showDetail(title, bodyHtml) {
  document.getElementById("detailTitle").textContent = title;
  document.getElementById("detailBody").innerHTML = bodyHtml;
  bootstrap.Modal.getOrCreateInstance(document.getElementById("detailModal")).show();
  return document.getElementById("detailBody");
}

export function hideDetail() {
  bootstrap.Modal.getOrCreateInstance(document.getElementById("detailModal")).hide();
}

// Simple table builder: columns = [{label, render(row) -> html, cls}]
export function table(columns, rows, { onRowClick, rowKey } = {}) {
  const head = columns.map((c) => `<th class="${c.cls || ""}">${esc(c.label)}</th>`).join("");
  const body = rows
    .map((row, i) => `<tr class="${onRowClick ? "clickable" : ""}" data-row="${i}">${columns.map((c) => `<td class="${c.cls || ""}">${c.render(row)}</td>`).join("")}</tr>`)
    .join("");
  const html = `<div class="table-wrap"><table class="table-x"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  return {
    html,
    bind(container) {
      if (!onRowClick) return;
      container.querySelectorAll("tbody tr[data-row]").forEach((tr) => {
        tr.addEventListener("click", () => onRowClick(rows[Number(tr.dataset.row)], rowKey));
      });
    },
  };
}

export function pager(container, { total, limit, offset }, onChange) {
  if (total <= limit) { container.innerHTML = total ? `<div class="hint mt-2">${number(total)} record(s)</div>` : ""; return; }
  const from = offset + 1;
  const to = Math.min(offset + limit, total);
  container.innerHTML = `<div class="d-flex align-items-center gap-2 mt-3">
    <button class="btn btn-sm btn-outline-secondary" data-p="prev" ${offset === 0 ? "disabled" : ""}><i class="bi bi-chevron-left"></i></button>
    <span class="hint">${number(from)}&ndash;${number(to)} of ${number(total)}</span>
    <button class="btn btn-sm btn-outline-secondary" data-p="next" ${to >= total ? "disabled" : ""}><i class="bi bi-chevron-right"></i></button></div>`;
  container.querySelector('[data-p="prev"]').addEventListener("click", () => onChange(Math.max(0, offset - limit)));
  container.querySelector('[data-p="next"]').addEventListener("click", () => onChange(offset + limit));
}

export function checksHtml(checks) {
  if (!checks || !checks.length) return "";
  return `<ul class="check-list">${checks
    .map((c) => `<li><i class="bi ${c.passed ? "bi-check-circle-fill" : "bi-x-circle-fill"}"></i>
      <div><div><strong>${esc(c.name)}</strong> <span class="visually-hidden">${c.passed ? "passed" : "failed"}</span></div>
      <div class="detail">${esc(c.detail)}</div></div></li>`)
    .join("")}</ul>`;
}

export function newIdempotencyKey(prefix = "PAY") {
  const id = window.crypto && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${id}`;
}

export function accountLabel(a) {
  return `#${a.account_id} &middot; ${esc(a.full_name)} &middot; ${esc(a.masked_account_number)} &middot; ${money(a.current_balance, a.currency)}${a.account_status !== "ACTIVE" ? ` (${esc(a.account_status)})` : ""}`;
}

export function accountOptions(accounts, selected) {
  return accounts
    .map((a) => `<option value="${a.account_id}" ${String(selected) === String(a.account_id) ? "selected" : ""}>${accountLabel(a)}</option>`)
    .join("");
}

export function gatewayOptions(gateways, selected, { allowNone = true, noneLabel = "Sender's preferred gateway (or none)" } = {}) {
  const none = allowNone ? `<option value="">${esc(noneLabel)}</option>` : "";
  return none + gateways
    .map((g) => `<option value="${g.gateway_id}" ${String(selected) === String(g.gateway_id) ? "selected" : ""}>${esc(g.gateway_name)} &middot; fee ${esc(g.fee_percentage)}% &middot; ${esc(g.gateway_status)}</option>`)
    .join("");
}

export function parseDetails(details) {
  if (!details) return null;
  try { return JSON.parse(details); } catch (e) { return null; }
}

export function formValues(form) {
  return Object.fromEntries(new FormData(form).entries());
}
