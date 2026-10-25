// Reconciliation: simulated gateway records, batches, exceptions and manual review.
import { api } from "../api.js";
import {
  badge, confirmDialog, dateOnly, dateTime, esc, formValues, gatewayOptions, hideDetail, loading,
  money, number, pager, showDetail, showError, simBadge, table, toast, todayIso,
} from "../ui.js";

const TYPES = ["AMOUNT_MISMATCH", "STATUS_MISMATCH", "MISSING_IN_GATEWAY", "MISSING_IN_INTERNAL", "DUPLICATE_EXTERNAL"];
const RESOLUTIONS = ["UNRESOLVED", "MANUALLY_RESOLVED", "AUTO_RESOLVED", "WRITTEN_OFF"];

export async function render(view, { query }) {
  view.innerHTML = `
    <div class="page-head"><div><h1>Reconciliation</h1>
      <p>Compares internal payments with the settlement records a payment gateway would send. In this prototype every
      gateway record is <strong>simulated</strong> (reference starts with <span class="mono">SIM-</span>); no real gateway is contacted.
      Reconciliation only reports differences &mdash; it never changes balances and never resolves exceptions by itself.</p></div></div>
    <ul class="nav nav-tabs mb-3" role="tablist">
      <li class="nav-item"><button class="nav-link active" data-tab="gen" type="button">1. Gateway records</button></li>
      <li class="nav-item"><button class="nav-link" data-tab="run" type="button">2. Run reconciliation</button></li>
      <li class="nav-item"><button class="nav-link" data-tab="batches" type="button">Batches</button></li>
      <li class="nav-item"><button class="nav-link" data-tab="exceptions" type="button">Exceptions</button></li>
    </ul>
    <div id="tab"></div>`;

  let gateways = [];
  try {
    gateways = (await api.get("/api/gateways")).items;
  } catch (err) {
    showError(view.querySelector("#tab"), err);
    return;
  }
  const tabEl = view.querySelector("#tab");
  const state = { gateway: gateways[0] ? String(gateways[0].gateway_id) : "", date: todayIso() };
  const tabs = { gen: renderGenerate, run: renderRun, batches: renderBatches, exceptions: renderExceptions };
  const show = (name) => {
    view.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    tabs[name]();
  };
  view.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
  if (!gateways.length) {
    tabEl.innerHTML = `<div class="card-x"><div class="state"><i class="bi bi-inbox"></i>No payment gateways exist yet.</div></div>`;
    return;
  }

  const scopeFields = (prefix) => `
    <div class="col-md-5"><label class="form-label" for="${prefix}-gw">Gateway</label>
      <select class="form-select form-select-sm" id="${prefix}-gw" name="gateway_id">${gatewayOptions(gateways, state.gateway, { allowNone: false })}</select></div>
    <div class="col-md-3"><label class="form-label" for="${prefix}-date">Settlement date</label>
      <input type="date" class="form-control form-control-sm" id="${prefix}-date" name="date" value="${state.date}" required></div>`;
  const remember = (f) => { state.gateway = f.gateway_id; state.date = f.date; };

  // ---------------------------------------------------------------- tab 1
  function renderGenerate() {
    tabEl.innerHTML = `
      <div class="card-x"><h2><i class="bi bi-cloud-arrow-down"></i>Generate a simulated gateway settlement file</h2>
        <p class="section-note">Takes the internal payments routed through the gateway on that date that have no gateway record yet,
        and creates a matching <span class="mono">SIM-</span> record for each &mdash; then deliberately injects the discrepancies you
        choose, so the reconciliation has something to find.</p>
        <form id="genForm" class="row g-2 align-items-end">
          ${scopeFields("g")}
          <div class="col-md-4"><label class="form-label" for="g-seed">Random seed (optional)</label>
            <input class="form-control form-control-sm" id="g-seed" name="seed" inputmode="numeric" placeholder="e.g. 42"></div>
          ${[["amount_mismatches", "Amount mismatches"], ["status_mismatches", "Status mismatches"], ["missing_in_gateway", "Omit payments (missing in gateway)"],
             ["missing_in_internal", "Unknown records (missing internally)"], ["duplicates", "Duplicate settlements"]]
            .map(([n, l]) => `<div class="col-6 col-md"><label class="form-label" for="g-${n}">${l}</label>
              <input type="number" min="0" max="20" class="form-control form-control-sm" id="g-${n}" name="${n}" value="1"></div>`).join("")}
          <div class="col-12"><button class="btn btn-primary btn-sm" type="submit"><i class="bi bi-magic"></i> Generate simulated records</button></div>
        </form>
        <div id="genResult" class="mt-3"></div></div>
      <div class="card-x"><h2><i class="bi bi-pencil-square"></i>Or add one record by hand</h2>
        <p class="section-note">Useful to stage a specific case, e.g. copy a payment's reference from its detail page and report a
        different amount.</p>
        <form id="addForm" class="row g-2 align-items-end">
          ${scopeFields("a")}
          <div class="col-md-4"><label class="form-label" for="a-ref">Internal transaction reference (optional)</label>
            <input class="form-control form-control-sm mono" id="a-ref" name="internal_transaction_ref" maxlength="36"></div>
          <div class="col-6 col-md-3"><label class="form-label" for="a-amt">Reported amount</label>
            <input class="form-control form-control-sm" id="a-amt" name="reported_amount" inputmode="decimal" required></div>
          <div class="col-6 col-md-3"><label class="form-label" for="a-fee">Reported fee</label>
            <input class="form-control form-control-sm" id="a-fee" name="reported_fee" value="0.00" inputmode="decimal"></div>
          <div class="col-6 col-md-3"><label class="form-label" for="a-st">Reported status</label>
            <select class="form-select form-select-sm" id="a-st" name="reported_status"><option>SUCCESS</option><option>FAILURE</option><option>PENDING</option><option>REFUNDED</option></select></div>
          <div class="col-6 col-md-3"><button class="btn btn-outline-primary btn-sm" type="submit"><i class="bi bi-plus-lg"></i> Add record</button></div>
        </form></div>
      <div class="card-x"><h2><i class="bi bi-table"></i>Gateway records for the selected gateway &amp; date</h2>
        <div id="extList"></div><div id="extPager"></div></div>`;

    const genForm = tabEl.querySelector("#genForm");
    const genResult = tabEl.querySelector("#genResult");
    genForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = formValues(genForm);
      remember(f);
      if (f.seed && !/^\d+$/.test(f.seed)) { toast("The seed must be a whole number.", "warning"); return; }
      loading(genResult, "Creating simulated gateway records&hellip;");
      try {
        const { data } = await api.post("/api/reconciliation/simulate-external", {
          gateway_id: Number(f.gateway_id), settlement_date: f.date,
          amount_mismatches: Number(f.amount_mismatches), status_mismatches: Number(f.status_mismatches),
          missing_in_gateway: Number(f.missing_in_gateway), missing_in_internal: Number(f.missing_in_internal),
          duplicates: Number(f.duplicates), seed: f.seed ? Number(f.seed) : null,
        });
        genResult.innerHTML = `
          <div class="alert alert-info"><i class="bi bi-flask me-2"></i>${data.created.length} simulated record(s) created for
            ${esc(data.gateway_name)} on ${dateOnly(data.settlement_date)} from ${data.candidates} internal payment(s) without a gateway record.
            ${data.omitted_payments.length ? `${data.omitted_payments.length} payment(s) were deliberately left out.` : ""}</div>
          ${data.created.length || data.omitted_payments.length ? table([
            { label: "Record", render: (r) => r.ext_transaction_id ? `#${r.ext_transaction_id} ${simBadge()}` : `<span class="hint">not sent</span>` },
            { label: "Payment", render: (r) => r.transaction_id ? `<a href="#/transactions/${r.transaction_id}">#${r.transaction_id}</a>` : `<span class="hint">unknown ref</span>` },
            { label: "Reported", cls: "num", render: (r) => money(r.reported_amount || r.amount) },
            { label: "Status", render: (r) => (r.reported_status ? badge(r.reported_status) : "-") },
            { label: "Injected as", render: (r) => badge(r.injected) },
          ], [...data.created, ...data.omitted_payments]).html : ""}
          ${data.candidates === 0 ? `<p class="hint">Tip: make a few payments through this gateway today first (New payment page), or pick another date.</p>` : ""}`;
        loadExternal(0);
      } catch (err) {
        showError(genResult, err);
      }
    });

    const addForm = tabEl.querySelector("#addForm");
    addForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = formValues(addForm);
      remember(f);
      if (!/^\d{1,13}(\.\d{1,2})?$/.test(f.reported_amount) || Number(f.reported_amount) <= 0) { toast("Enter a valid reported amount.", "warning"); return; }
      try {
        const { data } = await api.post("/api/reconciliation/external-records", {
          gateway_id: Number(f.gateway_id), settlement_date: f.date,
          internal_transaction_ref: f.internal_transaction_ref || null,
          reported_amount: f.reported_amount, reported_fee: f.reported_fee || "0", reported_status: f.reported_status,
        });
        toast(`Simulated record #${data.ext_transaction_id} added.`, "success");
        addForm.reset();
        loadExternal(0);
      } catch (err) {
        toast(err.message, "danger");
      }
    });

    async function loadExternal(offset) {
      const list = tabEl.querySelector("#extList");
      loading(list);
      try {
        const page = await api.get("/api/reconciliation/external-records", { gateway_id: state.gateway, settlement_date: state.date, limit: 50, offset });
        if (!page.items.length) { list.innerHTML = `<div class="state"><i class="bi bi-inbox"></i>No gateway records for this gateway and date.</div>`; tabEl.querySelector("#extPager").innerHTML = ""; return; }
        list.innerHTML = table([
          { label: "Ext ID", render: (r) => `#${r.ext_transaction_id} ${r.is_simulated ? simBadge() : ""}` },
          { label: "External reference", render: (r) => `<span class="ref">${esc(r.external_reference_id)}</span>` },
          { label: "Internal ref", render: (r) => (r.internal_transaction_ref ? `<span class="ref">${esc(r.internal_transaction_ref.slice(0, 13))}&hellip;</span>` : "-") },
          { label: "Matches payment", render: (r) => (r.matched_transaction_id ? `<a href="#/transactions/${r.matched_transaction_id}">#${r.matched_transaction_id}</a>` : `<span class="hint">none</span>`) },
          { label: "Reported", cls: "num", render: (r) => money(r.reported_amount) },
          { label: "Status", render: (r) => badge(r.reported_status) },
        ], page.items).html;
        pager(tabEl.querySelector("#extPager"), page, loadExternal);
      } catch (err) {
        showError(list, err);
      }
    }
    tabEl.querySelectorAll('[name="gateway_id"], [name="date"]').forEach((el) => el.addEventListener("change", (e) => {
      const f = formValues(e.target.form);
      remember(f);
      loadExternal(0);
    }));
    loadExternal(0);
  }

  // ---------------------------------------------------------------- tab 2
  function renderRun() {
    tabEl.innerHTML = `
      <div class="card-x"><h2><i class="bi bi-play-circle"></i>Run a reconciliation batch</h2>
        <p class="section-note">One batch = one gateway and one settlement date, executed as a single Oracle transaction. Matching key:
        <span class="mono">EXTERNAL_TRANSACTIONS.INTERNAL_TRANSACTION_REF = PAYMENT_TRANSACTIONS.TRANSACTION_REF</span>.
        Re-running is safe: a discrepancy already recorded by an earlier batch is not stored twice.</p>
        <form id="runForm" class="row g-2 align-items-end">${scopeFields("r")}
          <div class="col-md-4"><button class="btn btn-primary btn-sm" type="submit"><i class="bi bi-play-fill"></i> Run batch</button></div></form>
        <div id="runResult" class="mt-3"></div></div>`;
    const form = tabEl.querySelector("#runForm");
    const out = tabEl.querySelector("#runResult");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = formValues(form);
      remember(f);
      loading(out, "Reconciling&hellip;");
      try {
        const { data } = await api.post("/api/reconciliation/batches", { gateway_id: Number(f.gateway_id), batch_date: f.date });
        const b = data.batch;
        const findingRows = [
          ...data.new_exceptions.map((x) => ({ ...x, stored: "New exception" })),
          ...data.previously_recorded.map((x) => ({ ...x, stored: `Already recorded in batch #${x.batch_id} (${x.resolution_status})` })),
          ...(data.duplicates_stored ? [] : data.duplicates.map((x) => ({ ...x, stored: "Reported only (needs migration 001)" }))),
        ];
        out.innerHTML = `
          <div class="alert alert-success"><i class="bi bi-check-circle-fill me-2"></i>Batch
            <a href="#/reconciliation/batches/${b.batch_id}">#${b.batch_id}</a> ${badge(b.batch_status)} for ${esc(b.gateway_name)} on ${dateOnly(b.batch_date)}.</div>
          <div class="kpi-grid">
            <div class="kpi"><div class="kpi-label">Records compared</div><div class="kpi-value">${data.total_records}</div></div>
            <div class="kpi"><div class="kpi-label">Matched exactly</div><div class="kpi-value">${data.matched_records}</div></div>
            <div class="kpi"><div class="kpi-label">New exceptions stored</div><div class="kpi-value">${data.new_exceptions.length}</div></div>
            <div class="kpi"><div class="kpi-label">Already recorded</div><div class="kpi-value">${data.previously_recorded.length}</div></div>
            <div class="kpi"><div class="kpi-label">Duplicate settlements</div><div class="kpi-value">${data.duplicates.length}</div></div>
          </div>
          ${data.note ? `<div class="explain mb-3">${esc(data.note)}</div>` : ""}
          ${findingRows.length ? table([
            { label: "Type", render: (x) => badge(x.exception_type) },
            { label: "Payment", render: (x) => (x.internal_transaction_id ? `<a href="#/transactions/${x.internal_transaction_id}">#${x.internal_transaction_id}</a>` : "-") },
            { label: "Gateway record", render: (x) => (x.ext_transaction_id ? `#${x.ext_transaction_id}` : "-") },
            { label: "Difference", cls: "num", render: (x) => money(x.discrepancy_amount) },
            { label: "Explanation", render: (x) => esc(x.notes) },
            { label: "Stored", render: (x) => esc(x.stored) },
          ], findingRows).html : `<div class="state"><i class="bi bi-emoji-smile"></i>No discrepancies: internal and gateway records agree.</div>`}`;
      } catch (err) {
        showError(out, err);
      }
    });
  }

  // ---------------------------------------------------------------- tab 3
  function renderBatches() {
    tabEl.innerHTML = `<div class="card-x"><h2><i class="bi bi-collection"></i>Reconciliation batches</h2><div id="bList"></div><div id="bPager"></div></div>`;
    const list = tabEl.querySelector("#bList");
    const load = async (offset) => {
      loading(list);
      try {
        const page = await api.get("/api/reconciliation/batches", { limit: 25, offset });
        if (!page.items.length) { list.innerHTML = `<div class="state"><i class="bi bi-inbox"></i>No batches yet. Use tab 2 to run one.</div>`; return; }
        const tbl = table([
          { label: "Batch", render: (b) => `#${b.batch_id}` },
          { label: "Gateway", render: (b) => esc(b.gateway_name) },
          { label: "Date", render: (b) => dateOnly(b.batch_date) },
          { label: "Compared", cls: "num", render: (b) => number(b.total_records) },
          { label: "Matched", cls: "num", render: (b) => number(b.matched_records) },
          { label: "New exceptions", cls: "num", render: (b) => number(b.exception_records) },
          { label: "Unresolved", cls: "num", render: (b) => number(b.unresolved_count) },
          { label: "Status", render: (b) => badge(b.batch_status) },
          { label: "Executed", render: (b) => dateTime(b.execution_timestamp) },
        ], page.items, { onRowClick: (b) => { location.hash = `#/reconciliation/batches/${b.batch_id}`; } });
        list.innerHTML = tbl.html;
        tbl.bind(list);
        pager(tabEl.querySelector("#bPager"), page, load);
      } catch (err) {
        showError(list, err);
      }
    };
    load(0);
  }

  // ---------------------------------------------------------------- tab 4
  function renderExceptions() {
    tabEl.innerHTML = `<div class="card-x"><h2><i class="bi bi-exclamation-diamond"></i>Exceptions</h2>
      <form id="xForm" class="row g-2 align-items-end mb-2">
        <div class="col-md-4"><label class="form-label" for="x-type">Type</label><select class="form-select form-select-sm" id="x-type" name="exception_type">
          <option value="">All</option>${TYPES.map((t) => `<option>${t}</option>`).join("")}</select></div>
        <div class="col-md-4"><label class="form-label" for="x-status">Resolution</label><select class="form-select form-select-sm" id="x-status" name="resolution_status">
          <option value="">All</option>${RESOLUTIONS.map((t) => `<option ${t === "UNRESOLVED" ? "selected" : ""}>${t}</option>`).join("")}</select></div>
        <div class="col-md-4"><button class="btn btn-primary btn-sm" type="submit"><i class="bi bi-funnel"></i> Filter</button></div></form>
      <div id="xList"></div><div id="xPager"></div></div>`;
    const form = tabEl.querySelector("#xForm");
    const list = tabEl.querySelector("#xList");
    const load = async (offset) => {
      loading(list);
      try {
        const page = await api.get("/api/reconciliation/exceptions", { ...formValues(form), limit: 50, offset });
        list.innerHTML = page.items.length ? exceptionTable(page.items) : `<div class="state"><i class="bi bi-inbox"></i>No exceptions match.</div>`;
        bindReview(list, page.items, () => load(offset));
        pager(tabEl.querySelector("#xPager"), page, load);
      } catch (err) {
        showError(list, err);
      }
    };
    form.addEventListener("submit", (e) => { e.preventDefault(); load(0); });
    load(0);
  }

  show(query.tab && tabs[query.tab] ? query.tab : "gen");
}

export async function renderBatch(view, { params }) {
  const id = params[0];
  view.innerHTML = `<div class="page-head"><div><a class="small" href="#/reconciliation?tab=batches"><i class="bi bi-arrow-left"></i> Batches</a>
    <h1>Reconciliation batch #${esc(id)}</h1></div></div><div id="body"></div>`;
  const body = view.querySelector("#body");
  const load = async () => {
    loading(body);
    try {
      const d = await api.get(`/api/reconciliation/batches/${id}`);
      const b = d.batch;
      body.innerHTML = `
        <div class="kpi-grid">
          <div class="kpi"><div class="kpi-label">Gateway / date</div><div class="kpi-value small">${esc(b.gateway_name)}</div><div class="kpi-sub">${dateOnly(b.batch_date)}</div></div>
          <div class="kpi"><div class="kpi-label">Status</div><div class="kpi-value small">${badge(b.batch_status)}</div><div class="kpi-sub">${dateTime(b.execution_timestamp)}</div></div>
          <div class="kpi"><div class="kpi-label">Compared</div><div class="kpi-value">${b.total_records}</div></div>
          <div class="kpi"><div class="kpi-label">Matched</div><div class="kpi-value">${b.matched_records}</div></div>
          <div class="kpi"><div class="kpi-label">Exceptions stored</div><div class="kpi-value">${b.exception_records}</div></div>
        </div>
        <div class="card-x"><h2><i class="bi bi-exclamation-diamond"></i>Exceptions recorded by this batch</h2>
          ${d.exceptions.length ? exceptionTable(d.exceptions) : `<div class="state"><i class="bi bi-inbox"></i>This batch stored no new exceptions.</div>`}</div>`;
      bindReview(body, d.exceptions, load);
    } catch (err) {
      showError(body, err);
    }
  };
  await load();
}

function exceptionTable(items) {
  return table([
    { label: "ID", render: (x) => `#${x.exception_id}` },
    { label: "Batch", render: (x) => `<a href="#/reconciliation/batches/${x.batch_id}">#${x.batch_id}</a>` },
    { label: "Type", render: (x) => badge(x.exception_type) },
    { label: "Payment", render: (x) => (x.internal_transaction_id ? `<a href="#/transactions/${x.internal_transaction_id}">#${x.internal_transaction_id}</a> ${badge(x.internal_status)}` : "-") },
    { label: "Gateway record", render: (x) => (x.ext_transaction_id ? `#${x.ext_transaction_id} ${String(x.external_reference_id || "").startsWith("SIM-") ? simBadge() : ""}<br>${x.reported_status ? badge(x.reported_status) : ""}` : "-") },
    { label: "Internal / reported", cls: "num", render: (x) => `${money(x.internal_amount)} / ${money(x.reported_amount)}` },
    { label: "Difference", cls: "num", render: (x) => money(x.discrepancy_amount) },
    { label: "Resolution", render: (x) => `${badge(x.resolution_status)}${x.resolution_notes ? `<br><span class="hint">${esc(x.resolution_notes)}</span>` : ""}` },
    { label: "", render: (x) => (x.resolution_status === "AUTO_RESOLVED" ? "" : `<button class="btn btn-sm btn-outline-secondary" data-review="${x.exception_id}">Review</button>`) },
  ], items).html;
}

function bindReview(container, items, reload) {
  container.querySelectorAll("[data-review]").forEach((button) => button.addEventListener("click", () => {
    const x = items.find((i) => String(i.exception_id) === button.dataset.review);
    const body = showDetail(`Review exception #${x.exception_id}`, `
      <p>${badge(x.exception_type)} &middot; difference ${money(x.discrepancy_amount)}</p>
      <form id="reviewForm">
        <div class="mb-2"><label class="form-label" for="rv-status">Decision</label>
          <select class="form-select" id="rv-status" name="resolution_status">
            ${["UNRESOLVED", "MANUALLY_RESOLVED", "WRITTEN_OFF"].map((s) => `<option ${s === x.resolution_status ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="mb-3"><label class="form-label" for="rv-notes">Notes</label>
          <textarea class="form-control" id="rv-notes" name="resolution_notes" maxlength="500" rows="3">${esc(x.resolution_notes || "")}</textarea></div>
        <p class="hint">Recording a decision does not move any money. It is written to the audit log.</p>
        <button class="btn btn-primary" type="submit">Save decision</button></form>`);
    body.querySelector("#reviewForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = formValues(e.target);
      const ok = await confirmDialog({ title: "Save review decision?", bodyHtml: `<p>Set exception #${x.exception_id} to <strong>${esc(f.resolution_status)}</strong>?</p>` });
      if (!ok) return;
      try {
        await api.patch(`/api/reconciliation/exceptions/${x.exception_id}`, { resolution_status: f.resolution_status, resolution_notes: f.resolution_notes || null });
        hideDetail();
        toast("Decision saved and audited.", "success");
        reload();
      } catch (err) {
        toast(err.message, "danger");
      }
    });
  }));
}
