// Failure & rollback demonstration.
import { api } from "../api.js";
import { accountOptions, checksHtml, confirmDialog, dateTime, esc, formValues, loading, money, parseDetails, showError } from "../ui.js";

const WORKFLOW = [
  ["Check the idempotency key and read both accounts", null],
  ["INSERT the payment row (status INITIATED)", "AFTER_TRANSACTION_INSERT"],
  ["Lock the profile and both account rows, re-read balances, apply the business rules", null],
  ["Set PROCESSING and debit the sender (guarded UPDATE)", "AFTER_DEBIT"],
  ["Credit the receiver", "AFTER_BALANCE_UPDATES"],
  ["Write DR + CR ledger rows, verify DR = CR, set SUCCESS, write audit rows", "BEFORE_COMMIT"],
  ["COMMIT", null],
];

export async function render(view) {
  view.innerHTML = `
    <div class="page-head"><div><h1>Failure &amp; rollback</h1>
      <p>Runs a real payment and deliberately throws an error at a chosen step, before COMMIT. Oracle then rolls the whole
      transaction back. The page compares the database before, inside the failing transaction, and after the rollback.</p></div></div>
    <div id="body"></div>`;
  const body = view.querySelector("#body");
  loading(body);
  let status, accounts;
  try {
    [status, accounts] = await Promise.all([api.get("/api/failure-simulation"), api.get("/api/accounts")]);
  } catch (err) {
    showError(body, err);
    return;
  }
  accounts = accounts.items;
  const descriptions = Object.fromEntries(status.failure_points.map((p) => [p.name, p.description]));
  const disabledNote = status.enabled ? "" : `
    <div class="alert alert-warning"><i class="bi bi-lock-fill me-2"></i><strong>Failure simulation is switched off</strong>
    (the safe default). To demonstrate it, set <span class="mono">ENABLE_FAILURE_SIMULATION=true</span> in <span class="mono">.env</span>
    and restart the server. Ordinary payments can never trigger these hooks.</div>`;

  body.innerHTML = `${disabledNote}
    <div class="row g-3">
      <div class="col-lg-5"><div class="card-x">
        <h2><i class="bi bi-diagram-3"></i>Payment workflow &amp; failure points</h2>
        <form id="failForm">
          <ol class="steps">
            ${WORKFLOW.map(([text, point], i) => `<li>${esc(text)}</li>${point ? `
              <div class="fail-point"><label><input type="radio" name="failure_point" value="${point}" ${i === 3 ? "checked" : ""}>
              <span><i class="bi bi-lightning-fill"></i> Fail here: ${esc(descriptions[point] || point)}</span></label></div>` : ""}`).join("")}
          </ol>
          <hr>
          <div class="mb-2"><label class="form-label" for="f-sender">Sender</label>
            <select class="form-select form-select-sm" id="f-sender" name="sender_account_id">${accountOptions(accounts, accounts[0] && accounts[0].account_id)}</select></div>
          <div class="mb-2"><label class="form-label" for="f-receiver">Receiver</label>
            <select class="form-select form-select-sm" id="f-receiver" name="receiver_account_id">${accountOptions(accounts, accounts[1] && accounts[1].account_id)}</select></div>
          <div class="mb-3"><label class="form-label" for="f-amount">Amount</label>
            <input class="form-control form-control-sm" id="f-amount" name="amount" value="100.00" inputmode="decimal"></div>
          <button class="btn btn-danger" type="submit" ${status.enabled ? "" : "disabled"}><i class="bi bi-lightning-fill"></i> Run payment with failure</button>
        </form></div></div>
      <div class="col-lg-7"><div class="card-x"><h2><i class="bi bi-clipboard-data"></i>What the database shows</h2>
        <div id="result"><div class="state"><i class="bi bi-arrow-counterclockwise"></i>Pick a failure point and run.</div></div></div></div>
    </div>`;

  const form = body.querySelector("#failForm");
  const result = body.querySelector("#result");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = formValues(form);
    if (!/^\d{1,13}(\.\d{1,2})?$/.test(f.amount) || Number(f.amount) <= 0) { result.innerHTML = `<div class="alert alert-warning">Enter a valid amount.</div>`; return; }
    if (f.sender_account_id === f.receiver_account_id) { result.innerHTML = `<div class="alert alert-warning">Choose two different accounts.</div>`; return; }
    const ok = await confirmDialog({
      title: "Run a failing payment?",
      bodyHtml: `<p>A payment of <strong>${esc(f.amount)}</strong> will start and then fail on purpose at
        <span class="mono">${esc(f.failure_point)}</span>. Oracle should roll everything back.</p>`,
      confirmText: "Run",
      tone: "danger",
    });
    if (!ok) return;
    loading(result, "Running the payment and forcing the failure&hellip;");
    try {
      const { data } = await api.post("/api/failure-simulation/run", {
        sender_account_id: Number(f.sender_account_id),
        receiver_account_id: Number(f.receiver_account_id),
        amount: f.amount,
        failure_point: f.failure_point,
      });
      show(data);
    } catch (err) {
      showError(result, err);
    }
  });

  function show(r) {
    if (r.result !== "ROLLED_BACK") {
      result.innerHTML = `<div class="alert alert-info">The business rules rejected this payment
        (${esc(r.rejection ? r.rejection.code : r.result)}) before it reached <span class="mono">${esc(r.failure_point)}</span>,
        so the failure hook never ran. The rejected attempt was recorded as FAILED with no money moved.
        Try a smaller amount or the first failure point.</div>`;
      return;
    }
    const col = (title, s, cls = "") => `<div class="col-md-4"><div class="snapshot-col ${cls}"><h3>${title}</h3>
      <dl class="kv small"><dt>Sender</dt><dd>${money(s.sender_balance)}</dd><dt>Receiver</dt><dd>${money(s.receiver_balance)}</dd>
      ${s.transaction_status !== undefined ? `<dt>Payment row</dt><dd>${esc(s.transaction_status || "none")}</dd><dt>Ledger rows</dt><dd>${s.ledger_entries} for this payment</dd>`
        : `<dt>Payment rows</dt><dd>${s.payments_with_key}</dd><dt>Ledger rows</dt><dd>${s.ledger_entries} on both accounts</dd>`}</dl></div></div>`;
    const audit = r.audit;
    result.innerHTML = `
      <div class="alert ${r.all_checks_passed ? "alert-success" : "alert-danger"}"><i class="bi bi-arrow-counterclockwise me-2"></i>
        Failure injected at <strong>${esc(r.failure_point)}</strong>: ${esc(r.failure_point_description)}.
        Transaction #${r.rolled_back_transaction_id} was rolled back.</div>
      <div class="row g-2 mb-3">
        ${col("Before", r.before)}
        ${col("Inside the failing transaction (never committed)", r.inside_transaction, "inside")}
        ${col("After rollback", r.after)}
      </div>
      <h3 class="h6">Checks</h3>${checksHtml(r.checks)}
      ${audit ? `<h3 class="h6 mt-3">Audit record written after the rollback</h3>
        <dl class="kv small"><dt>Log</dt><dd>#${audit.log_id} &middot; ${esc(audit.table_name)} &middot; ${esc(audit.action_type)} &middot; ${dateTime(audit.action_timestamp)}</dd>
        <dt>Details</dt><dd><pre class="json">${esc(JSON.stringify(parseDetails(audit.details) || audit.details, null, 2))}</pre></dd></dl>` : ""}
      <p class="hint mt-2">The middle column is read by the failing transaction itself, which can see its own uncommitted
      changes. No other session could ever see those values, and nothing intermediate was committed to show them.</p>`;
  }
}
