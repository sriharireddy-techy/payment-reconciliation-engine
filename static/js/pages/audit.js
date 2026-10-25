// Audit log viewer with filters.
import { api } from "../api.js";
import { badge, dateTime, esc, formValues, loading, pager, parseDetails, showError, table } from "../ui.js";

const TABLES = ["PAYMENT_TRANSACTIONS", "BANK_ACCOUNTS", "EXTERNAL_TRANSACTIONS", "RECONCILIATION_BATCHES", "RECONCILIATION_EXCEPTIONS"];

export async function render(view, { query }) {
  view.innerHTML = `
    <div class="page-head"><div><h1>Audit log</h1>
      <p>Rows of <span class="mono">AUDIT_LOGS</span>. Each event's name and context are stored as JSON in
      <span class="mono">DETAILS</span>; <span class="mono">PERFORMED_BY</span> is the database user (column default).
      Secrets and passwords are never logged.</p></div></div>
    <div class="card-x">
      <form id="filters" class="row g-2 align-items-end mb-2">
        <div class="col-md-3"><label class="form-label" for="a-search">Search details</label>
          <input class="form-control form-control-sm" id="a-search" name="search" placeholder="e.g. PAYMENT_REJECTED" value="${esc(query.search || "")}"></div>
        <div class="col-6 col-md-3"><label class="form-label" for="a-table">Table</label>
          <select class="form-select form-select-sm" id="a-table" name="table_name"><option value="">All</option>${TABLES.map((t) => `<option>${t}</option>`).join("")}</select></div>
        <div class="col-6 col-md-2"><label class="form-label" for="a-action">Action</label>
          <select class="form-select form-select-sm" id="a-action" name="action_type"><option value="">All</option><option>INSERT</option><option>UPDATE</option><option>DELETE</option><option>SECURITY</option></select></div>
        <div class="col-6 col-md-1"><label class="form-label" for="a-txn">Txn ID</label>
          <input class="form-control form-control-sm" id="a-txn" name="transaction_id" inputmode="numeric" value="${esc(query.transaction_id || "")}"></div>
        <div class="col-6 col-md-1"><label class="form-label" for="a-acc">Acct ID</label>
          <input class="form-control form-control-sm" id="a-acc" name="account_id" inputmode="numeric"></div>
        <div class="col-md-2 d-flex gap-1"><button class="btn btn-primary btn-sm" type="submit"><i class="bi bi-funnel"></i> Filter</button>
          <button class="btn btn-outline-secondary btn-sm" type="reset">Clear</button></div>
      </form>
      <div id="list"></div><div id="pager"></div></div>`;

  const form = view.querySelector("#filters");
  const list = view.querySelector("#list");
  let offset = 0;

  async function load() {
    loading(list);
    const f = formValues(form);
    for (const key of ["transaction_id", "account_id"]) {
      if (f[key] && !/^\d+$/.test(f[key])) { showError(list, { message: "IDs must be whole numbers." }); return; }
    }
    try {
      const page = await api.get("/api/audit-logs", { ...f, limit: 50, offset });
      if (!page.items.length) {
        list.innerHTML = `<div class="state"><i class="bi bi-inbox"></i>No audit events match.</div>`;
        view.querySelector("#pager").innerHTML = "";
        return;
      }
      list.innerHTML = table([
        { label: "Log", render: (r) => `#${r.log_id}` },
        { label: "When", render: (r) => dateTime(r.action_timestamp) },
        { label: "Event", render: (r) => `<span class="mono">${esc(r.event || "-")}</span>` },
        { label: "Table", render: (r) => esc(r.table_name) },
        { label: "Action", render: (r) => (r.action_type === "SECURITY" ? badge("SECURITY") : esc(r.action_type)) },
        { label: "Record", render: (r) => `#${esc(r.record_id)}` },
        { label: "Payment", render: (r) => (r.transaction_id ? `<a href="#/transactions/${r.transaction_id}">#${r.transaction_id}</a>` : "-") },
        { label: "By", render: (r) => esc(r.performed_by) },
        { label: "Details", render: (r) => detailsCell(r.details) },
      ], page.items).html;
      pager(view.querySelector("#pager"), page, (o) => { offset = o; load(); });
    } catch (err) {
      showError(list, err);
    }
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); offset = 0; load(); });
  form.addEventListener("reset", () => setTimeout(() => { offset = 0; load(); }, 0));
  await load();
}

function detailsCell(details) {
  if (!details) return "-";
  const parsed = parseDetails(details);
  const text = parsed ? JSON.stringify(parsed, null, 2) : details;
  return `<details><summary class="small">show</summary><pre class="json">${esc(text)}</pre></details>`;
}
