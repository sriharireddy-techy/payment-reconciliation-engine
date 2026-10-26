// Account overview: masked numbers, owners, balances and payment limits.
import { api } from "../api.js";
import { badge, dateTime, errorHtml, esc, hideDetail, loading, money, showDetail, showError, table } from "../ui.js";

export async function render(view) {
  view.innerHTML = `
    <div class="page-head"><div><h1>Accounts</h1>
      <p>Bank accounts with their owners and payment-profile limits. Account numbers are masked by the database
      query itself (<span class="mono">SUBSTR</span>), so full numbers never reach the browser.</p></div>
      <a class="btn btn-primary btn-sm" href="#/payment"><i class="bi bi-send"></i> New payment</a></div>
    <div class="card-x"><div id="list"></div></div>`;
  const list = view.querySelector("#list");
  loading(list);
  let data;
  try {
    data = await api.get("/api/accounts");
  } catch (err) {
    showError(list, err);
    return;
  }
  if (!data.items.length) {
    list.innerHTML = `<div class="state"><i class="bi bi-inbox"></i>No accounts found. Run the optional demo-data script
      <span class="mono">sql/02_demo_data.sql</span> or add accounts in FreeSQL.</div>`;
    return;
  }
  const tbl = table([
    { label: "ID", render: (a) => `#${a.account_id}` },
    { label: "Holder", render: (a) => `${esc(a.full_name)}<br><span class="hint">KYC ${esc(a.kyc_status)}</span>` },
    { label: "Account", render: (a) => `<span class="mono">${esc(a.masked_account_number)}</span><br><span class="hint">${esc(a.bank_name)} &middot; ${esc(a.ifsc_code)}</span>` },
    { label: "Type", render: (a) => esc(a.account_type) },
    { label: "Balance", cls: "num", render: (a) => money(a.current_balance, a.currency) },
    { label: "Status", render: (a) => badge(a.account_status) },
    { label: "Limits (per txn / day)", cls: "num", render: (a) => a.daily_limit ? `${money(a.single_tx_limit, a.currency)} / ${money(a.daily_limit, a.currency)}` : `<span class="hint">no profile</span>` },
    { label: "Preferred gateway", render: (a) => esc(a.preferred_gateway_name || "-") },
  ], data.items, { onRowClick: (a) => openAccount(a.account_id) });
  list.innerHTML = tbl.html;
  tbl.bind(list);
}

async function openAccount(id) {
  const body = showDetail(`Account #${id}`, "");
  loading(body);
  try {
    const a = await api.get(`/api/accounts/${id}`);
    body.innerHTML = `
      <dl class="kv">
        <dt>Holder</dt><dd>${esc(a.full_name)} (user #${a.user_id}) &middot; ${badge(a.kyc_status)}</dd>
        <dt>Email</dt><dd>${esc(a.email)}</dd>
        <dt>Account</dt><dd><span class="mono">${esc(a.masked_account_number)}</span> &middot; ${esc(a.bank_name)} &middot; ${esc(a.ifsc_code)} &middot; ${esc(a.account_type)}</dd>
        <dt>Balance</dt><dd><strong>${money(a.current_balance, a.currency)}</strong> &middot; ${badge(a.account_status)}</dd>
        <dt>Opened</dt><dd>${dateTime(a.created_at)}</dd>
        <dt>Payment profile</dt><dd>${a.daily_limit
          ? `per payment ${money(a.single_tx_limit, a.currency)}, per day ${money(a.daily_limit, a.currency)} &middot; ${badge(a.profile_status)} &middot; 2FA ${esc(a.two_factor_auth)}`
          : "No payment profile (limits are not enforced)"}</dd>
        <dt>Spent today</dt><dd>${money(a.spent_today, a.currency)} <span class="hint">(successful payments from all of this user's accounts)</span></dd>
        <dt>Ledger</dt><dd>${a.ledger_summary.entry_count} entries &middot; debits ${money(a.ledger_summary.dr_total, a.currency)} &middot; credits ${money(a.ledger_summary.cr_total, a.currency)}</dd>
      </dl>
      <div class="d-flex gap-2 mt-3">
        <a class="btn btn-sm btn-outline-secondary" href="#/transactions?account_id=${a.account_id}" data-go>Payments</a>
        <a class="btn btn-sm btn-outline-secondary" href="#/ledger?account_id=${a.account_id}" data-go>Ledger entries</a>
        <a class="btn btn-sm btn-primary" href="#/payment?sender=${a.account_id}" data-go>Pay from this account</a>
      </div>`;
    body.querySelectorAll("[data-go]").forEach((link) => link.addEventListener("click", (event) => {
      event.preventDefault();
      hideDetail();
      window.location.hash = link.getAttribute("href");
    }));
  } catch (err) {
    body.innerHTML = errorHtml(err);
  }
}
