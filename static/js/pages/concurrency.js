// Concurrency lab: N genuinely simultaneous payment requests against one account.
import { api } from "../api.js";
import { legendHtml, statusColor, timeline } from "../charts.js";
import { accountOptions, badge, checksHtml, confirmDialog, esc, formValues, loading, money, showError, table } from "../ui.js";

export async function render(view) {
  const charts = [];
  view.innerHTML = `
    <div class="page-head"><div><h1>Concurrency lab</h1>
      <p>Fires several <strong>real</strong> payment requests from the same account at the same moment, each on its own
      thread and its own Oracle session, then reads the database back and checks that nothing was overspent or double-counted.</p></div></div>
    <div class="row g-3">
      <div class="col-lg-5"><div class="card-x" id="formCard"></div>
        <div class="card-x"><h2><i class="bi bi-lock"></i>How the database keeps this correct</h2>
          <ol class="small mb-0 ps-3">
            <li class="mb-2"><strong>Row locks in a fixed order.</strong> Each payment runs <span class="mono">SELECT &hellip; FOR UPDATE WAIT 30</span>
              on both accounts, lowest <span class="mono">ACCOUNT_ID</span> first, so requests touching the same account queue up and
              cannot deadlock each other.</li>
            <li class="mb-2"><strong>Re-read after locking.</strong> The balance is read only once the lock is held, so every request
              sees the effect of the ones before it (no lost update).</li>
            <li class="mb-2"><strong>Guarded update.</strong> <span class="mono">UPDATE &hellip; WHERE current_balance &gt;= :amount</span> refuses
              to debit if the money is gone, and the <span class="mono">CHK_ACCOUNT_BALANCE</span> constraint (balance &ge; 0) is the last line of defence.</li>
            <li><strong>Idempotency key.</strong> In "same key" mode every request carries one key; the UNIQUE constraint lets only
              one payment exist and the others get the original result back.</li>
          </ol>
          <p class="hint mt-2 mb-0">No Python lock is used: all protection comes from Oracle.</p></div>
      </div>
      <div class="col-lg-7"><div class="card-x"><h2><i class="bi bi-activity"></i>Results</h2>
        <div id="result"><div class="state"><i class="bi bi-lightning-charge"></i>Choose the parameters and run the test.</div></div></div></div>
    </div>`;

  const formCard = view.querySelector("#formCard");
  loading(formCard);
  let accounts;
  try {
    accounts = (await api.get("/api/accounts")).items;
  } catch (err) {
    showError(formCard, err);
    return () => charts.forEach((c) => c.destroy());
  }
  if (accounts.length < 2) {
    formCard.innerHTML = `<div class="state"><i class="bi bi-inbox"></i>At least two accounts are needed.</div>`;
    return () => {};
  }
  const preferred = accounts.find((a) => /concurrency/i.test(a.full_name)) || accounts[0];
  const receiver = accounts.find((a) => a.account_id !== preferred.account_id);

  formCard.innerHTML = `
    <h2><i class="bi bi-sliders"></i>Test parameters</h2>
    <form id="concForm">
      <div class="mb-2"><label class="form-label" for="c-sender">Paying account (shared by every request)</label>
        <select class="form-select form-select-sm" id="c-sender" name="sender_account_id">${accountOptions(accounts, preferred.account_id)}</select></div>
      <div class="mb-2"><label class="form-label" for="c-receiver">Receiving account</label>
        <select class="form-select form-select-sm" id="c-receiver" name="receiver_account_id">${accountOptions(accounts, receiver.account_id)}</select></div>
      <div class="row g-2 mb-2">
        <div class="col-6"><label class="form-label" for="c-amount">Amount per request</label>
          <input class="form-control form-control-sm" id="c-amount" name="amount" value="300.00" inputmode="decimal"></div>
        <div class="col-6"><label class="form-label" for="c-n">Simultaneous requests: <strong id="c-n-val">10</strong></label>
          <input type="range" class="form-range" id="c-n" name="num_requests" min="2" max="25" value="10"></div>
      </div>
      <div class="mb-3"><span class="form-label d-block">Mode</span>
        <div class="form-check"><input class="form-check-input" type="radio" name="mode" id="m1" value="unique_keys" checked>
          <label class="form-check-label" for="m1">Different payments (unique key each) &mdash; tests overspending</label></div>
        <div class="form-check"><input class="form-check-input" type="radio" name="mode" id="m2" value="same_key">
          <label class="form-check-label" for="m2">Same request retried (one shared key) &mdash; tests duplicate protection</label></div>
      </div>
      <div class="explain mb-3" id="c-predict"></div>
      <button class="btn btn-primary" type="submit" id="c-run"><i class="bi bi-lightning-charge"></i> Run concurrent payments</button>
    </form>`;

  const form = formCard.querySelector("#concForm");
  const predict = () => {
    const f = formValues(form);
    const a = accounts.find((x) => String(x.account_id) === f.sender_account_id);
    const amount = Number(f.amount);
    form.querySelector("#c-n-val").textContent = f.num_requests;
    if (!a || !(amount > 0)) { form.querySelector("#c-predict").innerHTML = "Enter a valid amount."; return; }
    const affordable = Math.floor(Number(a.current_balance) / amount);
    form.querySelector("#c-predict").innerHTML = f.mode === "same_key"
      ? `<strong>Expected:</strong> exactly one payment is created; the other ${f.num_requests - 1} requests are replayed.`
      : `<strong>Expected:</strong> balance ${money(a.current_balance, a.currency)} covers at most <strong>${affordable}</strong> payment(s) of
         ${money(f.amount, a.currency)}; the rest must be rejected with INSUFFICIENT_FUNDS (or a limit rule).`;
  };
  form.addEventListener("input", predict);
  predict();

  const result = view.querySelector("#result");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const f = formValues(form);
    if (!/^\d{1,13}(\.\d{1,2})?$/.test(f.amount) || Number(f.amount) <= 0) { result.innerHTML = `<div class="alert alert-warning">Enter a valid amount.</div>`; return; }
    if (f.sender_account_id === f.receiver_account_id) { result.innerHTML = `<div class="alert alert-warning">Choose two different accounts.</div>`; return; }
    const ok = await confirmDialog({
      title: "Run the concurrency test?",
      bodyHtml: `<p>This sends <strong>${esc(f.num_requests)}</strong> real payment requests at once. Successful ones are committed
        to the database (simulated money) and appear in the history and ledger.</p>`,
      confirmText: "Run test",
    });
    if (!ok) return;
    const button = form.querySelector("#c-run");
    button.disabled = true;
    charts.splice(0).forEach((c) => c.destroy());
    loading(result, `Running ${esc(f.num_requests)} simultaneous payments&hellip;`);
    try {
      const { data } = await api.post("/api/concurrency/run", {
        sender_account_id: Number(f.sender_account_id),
        receiver_account_id: Number(f.receiver_account_id),
        amount: f.amount,
        num_requests: Number(f.num_requests),
        mode: f.mode,
      });
      showResult(data);
      accounts = (await api.get("/api/accounts")).items;
      form.querySelector("#c-sender").innerHTML = accountOptions(accounts, f.sender_account_id);
      form.querySelector("#c-receiver").innerHTML = accountOptions(accounts, f.receiver_account_id);
      predict();
    } catch (err) {
      showError(result, err);
    } finally {
      button.disabled = false;
    }
  });

  function showResult(r) {
    const cur = r.currency;
    const outcomes = ["SUCCESS", "FAILED", "REPLAYED", "ERROR"].filter((o) => r.requests.some((q) => q.outcome === o));
    result.innerHTML = `
      <div class="alert ${r.all_checks_passed ? "alert-success" : "alert-danger"}">
        <i class="bi ${r.all_checks_passed ? "bi-check-circle-fill" : "bi-x-circle-fill"} me-2"></i>
        ${r.all_checks_passed ? "All consistency checks passed." : "Some consistency checks failed - see below."}
        <span class="small d-block">Run ${esc(r.run_id)} &middot; ${r.attempted} requests in ${r.elapsed_ms} ms &middot;
        up to ${r.db_pool_max} Oracle sessions in parallel</span></div>
      <div class="kpi-grid">
        <div class="kpi"><div class="kpi-label">Attempted</div><div class="kpi-value">${r.attempted}</div></div>
        <div class="kpi"><div class="kpi-label">Succeeded</div><div class="kpi-value">${r.succeeded}</div></div>
        <div class="kpi"><div class="kpi-label">Rejected</div><div class="kpi-value">${r.rejected}</div></div>
        <div class="kpi"><div class="kpi-label">Replayed / errors</div><div class="kpi-value">${r.replayed} / ${r.errors}</div></div>
      </div>
      <div class="row g-2 mb-3">
        <div class="col-sm-6"><div class="snapshot-col"><h3>Sender #${r.sender.account_id}</h3>
          ${money(r.sender.balance_before, cur)} &rarr; <strong>${money(r.sender.balance_after, cur)}</strong></div></div>
        <div class="col-sm-6"><div class="snapshot-col"><h3>Receiver #${r.receiver.account_id}</h3>
          ${money(r.receiver.balance_before, cur)} &rarr; <strong>${money(r.receiver.balance_after, cur)}</strong></div></div>
      </div>
      <h3 class="h6">Consistency checks (read back from Oracle)</h3>${checksHtml(r.checks)}
      <h3 class="h6 mt-3">Timeline</h3>
      <p class="hint">Each bar is one request, from the moment it started to the moment its database transaction finished.
      Overlapping bars show the requests really ran at the same time.</p>
      <div class="chart-box tall"><canvas id="tl" role="img" aria-label="Timeline of concurrent requests"></canvas></div>
      ${legendHtml(outcomes.map((o) => ({ label: o, color: (statusColor[o] || statusColor.ERROR)() })))}
      <h3 class="h6 mt-3">Requests</h3>
      ${table([
        { label: "#", render: (q) => q.request },
        { label: "Outcome", render: (q) => badge(q.outcome) },
        { label: "Reason", render: (q) => esc(q.reason || "") },
        { label: "Payment", render: (q) => (q.transaction_id ? `<a href="#/transactions/${q.transaction_id}">#${q.transaction_id}</a>` : "-") },
        { label: "Start (ms)", cls: "num", render: (q) => q.started_ms },
        { label: "Duration (ms)", cls: "num", render: (q) => q.duration_ms },
      ], r.requests).html}`;
    charts.push(timeline(result.querySelector("#tl"), r.requests));
  }

  return () => charts.forEach((c) => c.destroy());
}
