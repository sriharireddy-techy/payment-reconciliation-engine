// Ledger viewer and double-entry integrity check.
import { api } from "../api.js";
import { badge, dateTime, esc, formValues, loading, money, pager, showError, table } from "../ui.js";

export async function render(view, { query }) {
  view.innerHTML = `
    <div class="page-head"><div><h1>Ledger</h1>
      <p>Every money movement is written twice: a debit (DR) on the paying account and a credit (CR) on the receiving
      account. The integrity check below verifies that debits equal credits for the whole ledger and for every payment.</p></div></div>
    <div class="card-x"><h2><i class="bi bi-clipboard2-check"></i>Integrity check
      <button class="btn btn-sm btn-primary ms-auto" id="runCheck"><i class="bi bi-play-fill"></i> Run check</button></h2>
      <div id="integrity"><p class="hint mb-0">Reads all payments and ledger rows (read-only) and applies the design rules:
      SUCCESS = exactly one DR and one CR of the payment amount; FAILED/INITIATED/PROCESSING = no entries.</p></div></div>
    <div class="card-x">
      <form id="filters" class="row g-2 align-items-end mb-2">
        <div class="col-6 col-md-3"><label class="form-label" for="l-account">Account ID</label>
          <input class="form-control form-control-sm" id="l-account" name="account_id" inputmode="numeric" value="${esc(query.account_id || "")}"></div>
        <div class="col-6 col-md-3"><label class="form-label" for="l-txn">Transaction ID</label>
          <input class="form-control form-control-sm" id="l-txn" name="transaction_id" inputmode="numeric" value="${esc(query.transaction_id || "")}"></div>
        <div class="col-6 col-md-3"><label class="form-label" for="l-type">Entry type</label>
          <select class="form-select form-select-sm" id="l-type" name="entry_type"><option value="">DR and CR</option><option>DR</option><option>CR</option></select></div>
        <div class="col-6 col-md-3 d-flex gap-1"><button class="btn btn-primary btn-sm" type="submit"><i class="bi bi-funnel"></i> Filter</button>
          <button class="btn btn-outline-secondary btn-sm" type="reset">Clear</button></div>
      </form>
      <div id="list"></div><div id="pager"></div></div>`;

  const integrity = view.querySelector("#integrity");
  view.querySelector("#runCheck").addEventListener("click", async () => {
    loading(integrity, "Checking every payment&hellip;");
    try {
      const r = await api.get("/api/ledger/integrity");
      const g = r.global;
      integrity.innerHTML = `
        <div class="kpi-grid mb-2">
          <div class="kpi"><div class="kpi-label">Total debits</div><div class="kpi-value small">${money(g.dr_total)}</div></div>
          <div class="kpi"><div class="kpi-label">Total credits</div><div class="kpi-value small">${money(g.cr_total)}</div></div>
          <div class="kpi"><div class="kpi-label">Ledger balanced</div><div class="kpi-value small">${badge(g.balanced ? "SUCCESS" : "FAILED")}</div></div>
          <div class="kpi"><div class="kpi-label">Payments checked</div><div class="kpi-value small">${r.transactions_checked}</div>
            <div class="kpi-sub">${r.violation_count} with problems</div></div>
        </div>
        ${r.violations.length ? `<p class="hint">Payments that do not follow the rules (rows created outside this application may appear here):</p>
          ${table([
            { label: "Payment", render: (v) => `<a href="#/transactions/${v.transaction_id}">#${v.transaction_id}</a>` },
            { label: "Status", render: (v) => badge(v.status) },
            { label: "Amount", cls: "num", render: (v) => money(v.amount) },
            { label: "DR / CR", cls: "num", render: (v) => `${money(v.dr_total)} / ${money(v.cr_total)}` },
            { label: "Problems", render: (v) => v.problems.map(esc).join("<br>") },
          ], r.violations).html}` : `<div class="alert alert-success mb-0"><i class="bi bi-check-circle-fill me-2"></i>Every payment's ledger entries follow the double-entry rules.</div>`}`;
    } catch (err) {
      showError(integrity, err);
    }
  });

  const form = view.querySelector("#filters");
  if (query.entry_type) form.querySelector("#l-type").value = query.entry_type;
  const list = view.querySelector("#list");
  let offset = 0;

  async function load() {
    loading(list);
    const f = formValues(form);
    for (const key of ["account_id", "transaction_id"]) {
      if (f[key] && !/^\d+$/.test(f[key])) { showError(list, { message: "IDs must be whole numbers." }); return; }
    }
    try {
      const page = await api.get("/api/ledger", { ...f, limit: 50, offset });
      if (!page.items.length) {
        list.innerHTML = `<div class="state"><i class="bi bi-journal"></i>No ledger entries match.</div>`;
        view.querySelector("#pager").innerHTML = "";
        return;
      }
      const tbl = table([
        { label: "Entry", render: (e) => `#${e.entry_id}` },
        { label: "When", render: (e) => dateTime(e.entry_timestamp) },
        { label: "Payment", render: (e) => `#${e.transaction_id} ${badge(e.transaction_status)}` },
        { label: "Account", render: (e) => `#${e.account_id} ${esc(e.account_holder)} <span class="mono">${esc(e.masked_account_number)}</span>` },
        { label: "Debit", cls: "num", render: (e) => (e.entry_type === "DR" ? `<span class="entry-dr">${money(e.amount, e.currency)}</span>` : "") },
        { label: "Credit", cls: "num", render: (e) => (e.entry_type === "CR" ? `<span class="entry-cr">${money(e.amount, e.currency)}</span>` : "") },
        { label: "Description", render: (e) => esc(e.description || "") },
      ], page.items, { onRowClick: (e) => { location.hash = `#/transactions/${e.transaction_id}`; } });
      list.innerHTML = tbl.html;
      tbl.bind(list);
      pager(view.querySelector("#pager"), page, (o) => { offset = o; load(); });
    } catch (err) {
      showError(list, err);
    }
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); offset = 0; load(); });
  form.addEventListener("reset", () => setTimeout(() => { offset = 0; load(); }, 0));
  await load();
}
