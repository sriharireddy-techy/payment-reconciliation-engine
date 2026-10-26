// Transaction history (search + filters) and the full detail view of one payment.
import { api } from "../api.js";
import {
  badge, dateTime, esc, formValues, gatewayOptions, loading, money, pager, parseDetails,
  showError, simBadge, table,
} from "../ui.js";

const STATUSES = ["SUCCESS", "FAILED", "INITIATED", "PROCESSING", "REVERSED"];

export async function renderList(view, { query }) {
  view.innerHTML = `
    <div class="page-head"><div><h1>Transactions</h1>
      <p>Every payment attempt, including rejected ones (status FAILED). Click a row for its ledger entries,
      audit trail and reconciliation status.</p></div></div>
    <div class="card-x">
      <form id="filters" class="row g-2 align-items-end">
        <div class="col-md-3"><label class="form-label" for="f-search">Search</label>
          <input class="form-control form-control-sm" id="f-search" name="search" placeholder="ID, reference, key or name" value="${esc(query.search || "")}"></div>
        <div class="col-6 col-md-2"><label class="form-label" for="f-status">Status</label>
          <select class="form-select form-select-sm" id="f-status" name="status"><option value="">All</option>
          ${STATUSES.map((s) => `<option ${query.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="col-6 col-md-2"><label class="form-label" for="f-account">Account ID</label>
          <input class="form-control form-control-sm" id="f-account" name="account_id" inputmode="numeric" value="${esc(query.account_id || "")}"></div>
        <div class="col-6 col-md-2"><label class="form-label" for="f-gateway">Gateway</label>
          <select class="form-select form-select-sm" id="f-gateway" name="gateway_id"><option value="">All</option></select></div>
        <div class="col-6 col-md-1"><label class="form-label" for="f-from">From</label>
          <input type="date" class="form-control form-control-sm" id="f-from" name="date_from" value="${esc(query.date_from || "")}"></div>
        <div class="col-6 col-md-1"><label class="form-label" for="f-to">To</label>
          <input type="date" class="form-control form-control-sm" id="f-to" name="date_to" value="${esc(query.date_to || "")}"></div>
        <div class="col-6 col-md-1 d-flex gap-1">
          <button class="btn btn-primary btn-sm" type="submit" title="Apply filters"><i class="bi bi-funnel"></i></button>
          <button class="btn btn-outline-secondary btn-sm" type="reset" title="Clear filters"><i class="bi bi-x-lg"></i></button></div>
      </form>
    </div>
    <div class="card-x"><div id="list"></div><div id="pager"></div></div>`;

  const form = view.querySelector("#filters");
  api.get("/api/gateways").then((g) => {
    form.querySelector("#f-gateway").innerHTML = gatewayOptions(g.items, query.gateway_id, { noneLabel: "All" });
  }).catch(() => {});

  const list = view.querySelector("#list");
  let offset = 0;

  async function load() {
    loading(list);
    const f = formValues(form);
    if (f.account_id && !/^\d+$/.test(f.account_id)) { showError(list, { message: "Account ID must be a number." }); return; }
    try {
      const page = await api.get("/api/transactions", { ...f, limit: 25, offset });
      if (!page.items.length) {
        list.innerHTML = `<div class="state"><i class="bi bi-search"></i>No transactions match these filters.</div>`;
        view.querySelector("#pager").innerHTML = "";
        return;
      }
      const tbl = table([
        { label: "ID", render: (r) => `#${r.transaction_id}` },
        { label: "Reference", render: (r) => `<span class="ref">${esc(r.transaction_ref.slice(0, 13))}&hellip;</span>` },
        { label: "From", render: (r) => `${esc(r.sender_name)}<br><span class="hint">acct #${r.sender_account_id}</span>` },
        { label: "To", render: (r) => `${esc(r.receiver_name)}<br><span class="hint">acct #${r.receiver_account_id}</span>` },
        { label: "Amount", cls: "num", render: (r) => money(r.amount, r.currency) },
        { label: "Gateway", render: (r) => esc(r.gateway_name || "-") },
        { label: "Status", render: (r) => `${badge(r.status)}${r.rejection_code ? `<br><span class="hint">${esc(r.rejection_code)}</span>` : ""}` },
        { label: "Initiated", render: (r) => dateTime(r.initiated_at) },
      ], page.items, { onRowClick: (r) => { location.hash = `#/transactions/${r.transaction_id}`; } });
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

export async function renderDetail(view, { params }) {
  const id = params[0];
  view.innerHTML = `<div class="page-head"><div><a href="#/transactions" class="small"><i class="bi bi-arrow-left"></i> Transactions</a>
    <h1>Transaction #${esc(id)}</h1></div></div><div id="body"></div>`;
  const body = view.querySelector("#body");
  loading(body);
  let d;
  try {
    d = await api.get(`/api/transactions/${id}`);
  } catch (err) {
    showError(body, err);
    return;
  }
  const t = d.transaction;
  const cur = t.currency;
  const ledger = d.ledger;
  body.innerHTML = `
    <div class="row g-3">
      <div class="col-lg-6"><div class="card-x"><h2><i class="bi bi-receipt"></i>Payment ${badge(t.status)}</h2>
        <dl class="kv">
          <dt>Amount</dt><dd><strong>${money(t.amount, cur)}</strong> <span class="hint">gateway fee ${money(t.fee_amount, cur)}${t.fee_percentage ? ` (${esc(t.fee_percentage)}%)` : ""}</span></dd>
          <dt>From</dt><dd>${esc(t.sender_name)} &middot; <span class="mono">${esc(t.sender_masked_account)}</span> &middot; ${esc(t.sender_bank)} (acct #${t.sender_account_id})</dd>
          <dt>To</dt><dd>${esc(t.receiver_name)} &middot; <span class="mono">${esc(t.receiver_masked_account)}</span> &middot; ${esc(t.receiver_bank)} (acct #${t.receiver_account_id})</dd>
          <dt>Gateway</dt><dd>${esc(t.gateway_name || "None")}</dd>
          <dt>Reference</dt><dd class="mono">${esc(t.transaction_ref)}</dd>
          <dt>Idempotency key</dt><dd class="mono">${esc(t.idempotency_key)}</dd>
          <dt>Initiated</dt><dd>${dateTime(t.initiated_at)}</dd>
          <dt>Completed</dt><dd>${dateTime(t.completed_at)}</dd>
          ${t.rejection_code ? `<dt>Rejected because</dt><dd>${badge("REJECTED")} <span class="mono">${esc(t.rejection_code)}</span></dd>` : ""}
          <dt>Description</dt><dd>${esc(t.description || "-")}</dd>
        </dl></div></div>
      <div class="col-lg-6"><div class="card-x"><h2><i class="bi bi-journal-text"></i>Ledger entries
        ${ledger.entries.length ? (ledger.balanced ? '<span class="status status-good"><i class="bi bi-check-circle-fill"></i>DR = CR</span>' : '<span class="status status-critical"><i class="bi bi-x-circle-fill"></i>UNBALANCED</span>') : ""}</h2>
        ${ledger.entries.length ? `
          <div class="table-wrap"><table class="table-x"><thead><tr><th>Entry</th><th>Type</th><th>Account</th><th class="num">Debit</th><th class="num">Credit</th></tr></thead><tbody>
          ${ledger.entries.map((e) => `<tr><td>#${e.entry_id}</td><td class="entry-${e.entry_type.toLowerCase()}">${e.entry_type}</td>
            <td>${esc(e.account_holder)} <span class="mono">${esc(e.masked_account_number)}</span></td>
            <td class="num">${e.entry_type === "DR" ? money(e.amount, cur) : ""}</td><td class="num">${e.entry_type === "CR" ? money(e.amount, cur) : ""}</td></tr>`).join("")}
          <tr><th colspan="3">Total</th><th class="num">${money(ledger.dr_total, cur)}</th><th class="num">${money(ledger.cr_total, cur)}</th></tr>
          </tbody></table></div>
          <div class="explain mt-3"><strong>Double entry:</strong> the sender's account is debited (DR) and the receiver's
          account is credited (CR) by the same amount, so total debits always equal total credits.</div>`
        : `<div class="state"><i class="bi bi-journal-x"></i>No ledger entries. ${t.status === "FAILED" ? "A rejected payment moves no money." : ""}</div>`}
      </div></div>
    </div>
    <div class="card-x"><h2><i class="bi bi-shield-check"></i>Audit trail</h2>
      ${d.audit.length ? table([
        { label: "When", render: (a) => dateTime(a.action_timestamp) },
        { label: "Event", render: (a) => `<span class="mono">${esc((parseDetails(a.details) || {}).event || "-")}</span>` },
        { label: "Table / action", render: (a) => `${esc(a.table_name)} &middot; ${esc(a.action_type)}` },
        { label: "Record", render: (a) => `#${esc(a.record_id)}` },
        { label: "By", render: (a) => esc(a.performed_by) },
      ], d.audit).html : `<div class="state"><i class="bi bi-inbox"></i>No audit records reference this payment.</div>`}
    </div>
    <div class="row g-3">
      <div class="col-lg-6"><div class="card-x"><h2><i class="bi bi-cloud-arrow-down"></i>Gateway records</h2>
        ${d.external_records.length ? table([
          { label: "Ext ID", render: (e) => `#${e.ext_transaction_id} ${e.is_simulated ? simBadge() : ""}` },
          { label: "Reference", render: (e) => `<span class="ref">${esc(e.external_reference_id)}</span>` },
          { label: "Reported", cls: "num", render: (e) => money(e.reported_amount, cur) },
          { label: "Status", render: (e) => badge(e.reported_status) },
          { label: "Settled", render: (e) => dateTime(e.settlement_date) },
        ], d.external_records).html : `<div class="state"><i class="bi bi-inbox"></i>No gateway record references this payment yet.</div>`}
      </div></div>
      <div class="col-lg-6"><div class="card-x"><h2><i class="bi bi-exclamation-diamond"></i>Reconciliation exceptions</h2>
        ${d.reconciliation_exceptions.length ? table([
          { label: "Exception", render: (x) => `#${x.exception_id} (batch <a href="#/reconciliation/batches/${x.batch_id}">#${x.batch_id}</a>)` },
          { label: "Type", render: (x) => badge(x.exception_type) },
          { label: "Difference", cls: "num", render: (x) => money(x.discrepancy_amount, cur) },
          { label: "Resolution", render: (x) => badge(x.resolution_status) },
        ], d.reconciliation_exceptions).html : `<div class="state"><i class="bi bi-inbox"></i>No exceptions recorded for this payment.</div>`}
      </div></div>
    </div>`;
}
