// New payment form with idempotency-key demonstration.
import { api } from "../api.js";
import {
  accountOptions, badge, confirmDialog, dateTime, esc, formValues, gatewayOptions, loading,
  money, newIdempotencyKey, showError, toast,
} from "../ui.js";

export async function render(view, { query }) {
  view.innerHTML = `
    <div class="page-head"><div><h1>New payment</h1>
      <p>Moves money between two accounts in <strong>one Oracle transaction</strong>: the payment row, both balance
      updates, the DR/CR ledger entries and the audit records commit together or not at all.</p></div></div>
    <div class="row g-3">
      <div class="col-lg-6"><div class="card-x" id="formCard"></div></div>
      <div class="col-lg-6"><div class="card-x"><h2><i class="bi bi-clipboard-check"></i>Result</h2>
        <div id="result"><div class="state"><i class="bi bi-send"></i>Submit a payment to see the database outcome here.</div></div></div></div>
    </div>`;
  const formCard = view.querySelector("#formCard");
  loading(formCard);
  let accounts, gateways;
  try {
    [accounts, gateways] = await Promise.all([api.get("/api/accounts"), api.get("/api/gateways")]);
  } catch (err) {
    showError(formCard, err);
    return;
  }
  accounts = accounts.items;
  gateways = gateways.items;
  if (accounts.length < 2) {
    formCard.innerHTML = `<div class="state"><i class="bi bi-inbox"></i>At least two accounts are needed.</div>`;
    return;
  }
  const sender = query.sender || accounts[0].account_id;
  const receiver = accounts.find((a) => String(a.account_id) !== String(sender)).account_id;

  formCard.innerHTML = `
    <h2><i class="bi bi-send"></i>Payment details</h2>
    <form id="payForm" novalidate>
      <div class="mb-3"><label class="form-label" for="sender">From (sender account)</label>
        <select class="form-select" id="sender" name="sender_account_id" required>${accountOptions(accounts, sender)}</select></div>
      <div class="mb-3"><label class="form-label" for="receiver">To (receiver account)</label>
        <select class="form-select" id="receiver" name="receiver_account_id" required>${accountOptions(accounts, receiver)}</select></div>
      <div class="row g-2 mb-3">
        <div class="col-sm-6"><label class="form-label" for="amount">Amount</label>
          <input class="form-control" id="amount" name="amount" inputmode="decimal" placeholder="e.g. 500.00" required>
          <div class="invalid-feedback">Enter an amount greater than 0 with at most 2 decimals.</div></div>
        <div class="col-sm-6"><label class="form-label" for="gateway">Gateway</label>
          <select class="form-select" id="gateway" name="gateway_id">${gatewayOptions(gateways)}</select></div>
      </div>
      <div class="mb-3"><label class="form-label" for="description">Description (optional)</label>
        <input class="form-control" id="description" name="description" maxlength="150"></div>
      <div class="mb-3"><label class="form-label" for="key">Idempotency key</label>
        <div class="input-group">
          <input class="form-control mono" id="key" name="idempotency_key" readonly>
          <button class="btn btn-outline-secondary" type="button" id="newKey" title="Generate a new key"><i class="bi bi-arrow-repeat"></i></button>
        </div>
        <div class="form-text">Generated once per payment. Re-sending the same key can never charge twice
        (it is stored in the UNIQUE column <span class="mono">PAYMENT_TRANSACTIONS.IDEMPOTENCY_KEY</span>).</div></div>
      <button class="btn btn-primary" type="submit" id="submitBtn"><i class="bi bi-send"></i> Review &amp; pay</button>
    </form>`;

  const form = formCard.querySelector("#payForm");
  const keyInput = form.querySelector("#key");
  const result = view.querySelector("#result");
  keyInput.value = newIdempotencyKey();
  form.querySelector("#newKey").addEventListener("click", () => { keyInput.value = newIdempotencyKey(); });

  let lastPayload = null;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = formValues(form);
    const amountInput = form.querySelector("#amount");
    const validAmount = /^\d{1,13}(\.\d{1,2})?$/.test(values.amount.trim()) && Number(values.amount) > 0;
    amountInput.classList.toggle("is-invalid", !validAmount);
    if (!validAmount) return;
    if (values.sender_account_id === values.receiver_account_id) {
      toast("Sender and receiver must be different accounts.", "warning");
      return;
    }
    const payload = {
      sender_account_id: Number(values.sender_account_id),
      receiver_account_id: Number(values.receiver_account_id),
      amount: values.amount.trim(),
      idempotency_key: values.idempotency_key,
      gateway_id: values.gateway_id ? Number(values.gateway_id) : null,
      description: values.description || null,
    };
    const s = accounts.find((a) => String(a.account_id) === values.sender_account_id);
    const r = accounts.find((a) => String(a.account_id) === values.receiver_account_id);
    const ok = await confirmDialog({
      title: "Confirm payment",
      bodyHtml: `<p>Send <strong>${money(payload.amount, s.currency)}</strong> from <strong>${esc(s.full_name)}</strong>
        (${esc(s.masked_account_number)}) to <strong>${esc(r.full_name)}</strong> (${esc(r.masked_account_number)})?</p>
        <p class="hint mb-0">This writes to the Oracle database. Simulated money only.</p>`,
      confirmText: "Pay now",
    });
    if (ok) await submit(payload);
  });

  async function submit(payload, isResend = false) {
    const button = form.querySelector("#submitBtn");
    button.disabled = true;
    loading(result, isResend ? "Re-sending the same request&hellip;" : "Processing the payment in Oracle&hellip;");
    try {
      const res = await api.post("/api/payments", payload);
      lastPayload = payload;
      await showOutcome(res.data.transaction, res.data.replayed ? "REPLAYED" : "SUCCESS", res.data.message);
      toast(res.data.replayed ? "Duplicate request detected - original payment returned." : "Payment completed.", res.data.replayed ? "info" : "success");
      refreshAccounts();
    } catch (err) {
      const txn = err.details && err.details.transaction;
      if (txn) {
        lastPayload = payload;
        await showOutcome(txn, err.details.replayed ? "REPLAYED" : "FAILED", `${err.message} (${err.code})`);
        toast(`Payment rejected: ${err.code}`, "danger");
      } else {
        showError(result, err);
        toast(err.message, "danger");
      }
    } finally {
      button.disabled = false;
    }
  }

  async function showOutcome(txn, outcome, message) {
    const intro = {
      SUCCESS: `<div class="alert alert-success"><i class="bi bi-check-circle-fill me-2"></i>${esc(message)}</div>`,
      FAILED: `<div class="alert alert-danger"><i class="bi bi-x-circle-fill me-2"></i>${esc(message)}<br>
        <span class="small">The attempt is recorded as FAILED; no balance or ledger row changed.</span></div>`,
      REPLAYED: `<div class="alert alert-info"><i class="bi bi-arrow-repeat me-2"></i>${esc(message)}<br>
        <span class="small">Same idempotency key &rarr; same transaction #${txn.transaction_id}. Nothing was processed again.</span></div>`,
    }[outcome];
    let ledgerHtml = "";
    try {
      const ledger = await api.get(`/api/transactions/${txn.transaction_id}/ledger`);
      ledgerHtml = ledger.entries.length
        ? `<h3 class="h6 mt-3">Ledger entries ${ledger.balanced ? '<span class="status status-good"><i class="bi bi-check-circle-fill"></i>DR = CR</span>' : '<span class="status status-critical">UNBALANCED</span>'}</h3>
           <table class="table-x"><tbody>${ledger.entries.map((e) => `<tr><td class="entry-${e.entry_type.toLowerCase()}">${e.entry_type}</td>
           <td>${esc(e.account_holder)} <span class="mono">${esc(e.masked_account_number)}</span></td><td class="num">${money(e.amount, txn.currency)}</td></tr>`).join("")}</tbody></table>`
        : `<p class="hint mt-3">No ledger entries: money did not move.</p>`;
    } catch (e) { /* ledger panel is optional */ }
    result.innerHTML = `${intro}
      <dl class="kv">
        <dt>Transaction</dt><dd><a href="#/transactions/${txn.transaction_id}">#${txn.transaction_id}</a> &middot; ${badge(txn.status)}</dd>
        <dt>Reference</dt><dd class="mono">${esc(txn.transaction_ref)}</dd>
        <dt>Idempotency key</dt><dd class="mono">${esc(txn.idempotency_key)}</dd>
        <dt>Amount</dt><dd>${money(txn.amount, txn.currency)} <span class="hint">fee ${money(txn.fee_amount, txn.currency)}${txn.gateway_name ? ` via ${esc(txn.gateway_name)}` : ""}</span></dd>
        <dt>Initiated / completed</dt><dd>${dateTime(txn.initiated_at)} / ${dateTime(txn.completed_at)}</dd>
      </dl>${ledgerHtml}
      <div class="d-flex flex-wrap gap-2 mt-3">
        <button class="btn btn-outline-primary btn-sm" id="resend"><i class="bi bi-arrow-repeat"></i> Send the same request again (same key)</button>
        <button class="btn btn-outline-secondary btn-sm" id="fresh"><i class="bi bi-plus-lg"></i> New payment (new key)</button>
      </div>
      <p class="hint mt-2">"Send again" simulates a client retry after a timeout: the server finds the key and returns the
      original result instead of charging twice.</p>`;
    result.querySelector("#resend").addEventListener("click", () => submit(lastPayload, true));
    result.querySelector("#fresh").addEventListener("click", () => {
      keyInput.value = newIdempotencyKey();
      form.querySelector("#amount").value = "";
      form.querySelector("#description").value = "";
      form.querySelector("#amount").focus();
    });
  }

  async function refreshAccounts() {
    try {
      accounts = (await api.get("/api/accounts")).items;
      const sel = [form.querySelector("#sender").value, form.querySelector("#receiver").value];
      form.querySelector("#sender").innerHTML = accountOptions(accounts, sel[0]);
      form.querySelector("#receiver").innerHTML = accountOptions(accounts, sel[1]);
    } catch (e) { /* keep old balances in the list */ }
  }
}
