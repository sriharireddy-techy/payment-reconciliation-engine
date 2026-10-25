// Dashboard: live KPIs, charts and recent activity, all computed by Oracle queries.
import { api } from "../api.js";
import { horizontalBar, legendHtml, stackedColumns, statusColor } from "../charts.js";
import { badge, dateOnly, dateTime, esc, money, number, showError, table } from "../ui.js";

export async function render(view) {
  const charts = [];
  view.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Financial operations dashboard</h1>
        <p>Every figure below is calculated live from the Oracle tables. Gateway records whose reference starts with
        <span class="mono">SIM-</span> are simulated for the reconciliation demonstration.</p>
      </div>
      <div class="d-flex gap-2 align-items-center">
        <span class="hint" id="updated"></span>
        <button class="btn btn-outline-secondary btn-sm" id="refresh"><i class="bi bi-arrow-clockwise"></i> Refresh</button>
      </div>
    </div>
    <div id="body"></div>`;

  const body = view.querySelector("#body");

  async function load() {
    charts.splice(0).forEach((c) => c.destroy());
    body.innerHTML = `<div class="kpi-grid">${'<div class="kpi placeholder-glow"><span class="placeholder col-6"></span><div class="kpi-value"><span class="placeholder col-4"></span></div></div>'.repeat(8)}</div>`;
    let d;
    try {
      d = await api.get("/api/dashboard");
    } catch (err) {
      showError(body, err);
      return;
    }
    view.querySelector("#updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    const t = d.transactions;
    const c = d.counts || {};
    const valueLines = t.success_value_by_currency.length
      ? t.success_value_by_currency.map((v) => money(v.total_value, v.currency)).join("<br>")
      : money("0", "INR");
    const balanceLines = d.balances.length
      ? d.balances.map((b) => money(b.total_balance, b.currency)).join("<br>")
      : money("0", "INR");

    body.innerHTML = `
      <div class="kpi-grid">
        ${kpi("bi-receipt", "Total payments", number(t.total), `${number(c.ledger_entry_count)} ledger entries`)}
        ${kpi("bi-check-circle", "Successful", number(t.success), share(t.success, t.total))}
        ${kpi("bi-x-circle", "Failed / rejected", number(t.failed), share(t.failed, t.total))}
        ${kpi("bi-hourglass-split", "Pending", number(t.pending), "INITIATED or PROCESSING")}
        ${kpi("bi-cash-stack", "Value of successful payments", valueLines, "grouped by currency", true)}
        ${kpi("bi-wallet2", "Available balance", balanceLines, `${number(c.account_count)} accounts (ACTIVE only)`, true)}
        ${kpi("bi-exclamation-diamond", "Unresolved exceptions", number(d.reconciliation.unresolved_exceptions), `${number(c.batch_count)} reconciliation batches`)}
        ${kpi("bi-question-octagon", "Unmatched gateway records", number(c.unmatched_external_count), `${number(c.simulated_external_count)} of ${number(c.external_record_count)} gateway records are simulated`)}
      </div>
      <div class="row g-3">
        <div class="col-lg-5"><div class="card-x"><h2><i class="bi bi-bar-chart"></i>Payments by status</h2>
          <div class="chart-box"><canvas id="statusChart" role="img" aria-label="Number of payments in each status"></canvas></div>
          <div id="statusLegend"></div></div></div>
        <div class="col-lg-7"><div class="card-x"><h2><i class="bi bi-calendar3"></i>Last 14 days</h2>
          <div class="chart-box"><canvas id="dailyChart" role="img" aria-label="Successful and failed payments per day"></canvas></div>
          <div id="dailyLegend"></div></div></div>
      </div>
      <div class="row g-3">
        <div class="col-xl-7"><div class="card-x"><h2><i class="bi bi-clock-history"></i>Recent payments
          <a class="ms-auto small" href="#/transactions">View all</a></h2><div id="recent"></div></div></div>
        <div class="col-xl-5"><div class="card-x"><h2><i class="bi bi-intersect"></i>Reconciliation batches
          <a class="ms-auto small" href="#/reconciliation">Open</a></h2><div id="batches"></div>
          <h2 class="mt-3"><i class="bi bi-exclamation-diamond"></i>Exceptions by type</h2>
          <div class="chart-box" style="height:180px"><canvas id="excChart" role="img" aria-label="Reconciliation exceptions by type"></canvas></div>
          <div id="excEmpty"></div></div></div>
      </div>
      <div class="card-x"><h2><i class="bi bi-shield-check"></i>Recent audit events <a class="ms-auto small" href="#/audit">View all</a></h2>
        <div id="audit"></div></div>`;

    // Status chart (status colours + labelled legend, never colour alone)
    const statuses = Object.entries(t.by_status).sort((a, b) => b[1] - a[1]);
    if (statuses.length) {
      const colors = statuses.map(([s]) => (statusColor[s] || statusColor.ERROR)());
      charts.push(horizontalBar(body.querySelector("#statusChart"), statuses.map(([s]) => s), statuses.map(([, n]) => n), colors, "Payments"));
      body.querySelector("#statusLegend").innerHTML = legendHtml(statuses.map(([s, n], i) => ({ label: `${esc(s)} (${n})`, color: colors[i] })));
    } else {
      body.querySelector("#statusChart").parentElement.innerHTML = `<div class="state"><i class="bi bi-inbox"></i>No payments yet.</div>`;
    }

    if (d.daily_activity.length) {
      const good = statusColor.SUCCESS();
      const bad = statusColor.FAILED();
      charts.push(stackedColumns(body.querySelector("#dailyChart"), d.daily_activity.map((r) => dateOnly(r.day)), [
        { label: "Successful", values: d.daily_activity.map((r) => r.success_count), color: good },
        { label: "Failed", values: d.daily_activity.map((r) => r.failed_count), color: bad },
      ]));
      body.querySelector("#dailyLegend").innerHTML = legendHtml([{ label: "Successful", color: good }, { label: "Failed", color: bad }]);
    } else {
      body.querySelector("#dailyChart").parentElement.innerHTML = `<div class="state"><i class="bi bi-inbox"></i>No payments in the last 14 days.</div>`;
    }

    const exc = Object.entries(d.reconciliation.exceptions_by_type);
    if (exc.length) {
      const blue = getComputedStyle(document.documentElement).getPropertyValue("--series-1").trim();
      charts.push(horizontalBar(body.querySelector("#excChart"), exc.map(([k]) => k.replaceAll("_", " ")), exc.map(([, n]) => n), blue, "Exceptions"));
    } else {
      body.querySelector("#excChart").parentElement.innerHTML = `<div class="state"><i class="bi bi-inbox"></i>No reconciliation exceptions recorded.</div>`;
    }

    const recent = body.querySelector("#recent");
    if (d.recent_transactions.length) {
      const tbl = table([
        { label: "ID", render: (r) => `#${r.transaction_id}` },
        { label: "From → To", render: (r) => `${esc(r.sender_name)} → ${esc(r.receiver_name)}` },
        { label: "Amount", cls: "num", render: (r) => money(r.amount, r.currency) },
        { label: "Status", render: (r) => badge(r.status) },
        { label: "Initiated", render: (r) => dateTime(r.initiated_at) },
      ], d.recent_transactions, { onRowClick: (r) => { location.hash = `#/transactions/${r.transaction_id}`; } });
      recent.innerHTML = tbl.html;
      tbl.bind(recent);
    } else {
      recent.innerHTML = `<div class="state"><i class="bi bi-inbox"></i>No payments yet. <a href="#/payment">Make the first one</a>.</div>`;
    }

    const batches = body.querySelector("#batches");
    if (d.reconciliation.recent_batches.length) {
      const tbl = table([
        { label: "Batch", render: (r) => `#${r.batch_id}` },
        { label: "Gateway / date", render: (r) => `${esc(r.gateway_name)}<br><span class="hint">${dateOnly(r.batch_date)}</span>` },
        { label: "Matched", cls: "num", render: (r) => `${number(r.matched_records)} / ${number(r.total_records)}` },
        { label: "Exceptions", cls: "num", render: (r) => number(r.exception_records) },
        { label: "Status", render: (r) => badge(r.batch_status) },
      ], d.reconciliation.recent_batches, { onRowClick: (r) => { location.hash = `#/reconciliation/batches/${r.batch_id}`; } });
      batches.innerHTML = tbl.html;
      tbl.bind(batches);
    } else {
      batches.innerHTML = `<div class="state"><i class="bi bi-inbox"></i>No batches yet.</div>`;
    }

    const auditEl = body.querySelector("#audit");
    if (d.recent_audit.length) {
      auditEl.innerHTML = table([
        { label: "When", render: (r) => dateTime(r.action_timestamp) },
        { label: "Event", render: (r) => `<span class="mono">${esc(r.event || "-")}</span>` },
        { label: "Table", render: (r) => esc(r.table_name) },
        { label: "Action", render: (r) => esc(r.action_type) },
        { label: "Record", render: (r) => `#${esc(r.record_id)}` },
        { label: "By", render: (r) => esc(r.performed_by) },
      ], d.recent_audit).html;
    } else {
      auditEl.innerHTML = `<div class="state"><i class="bi bi-inbox"></i>No audit events yet.</div>`;
    }
  }

  view.querySelector("#refresh").addEventListener("click", load);
  await load();
  return () => charts.forEach((c) => c.destroy());
}

function kpi(icon, label, value, sub, small = false) {
  return `<div class="kpi"><div class="kpi-label"><i class="bi ${icon}"></i>${esc(label)}</div>
    <div class="kpi-value ${small ? "small" : ""}">${value}</div><div class="kpi-sub">${sub}</div></div>`;
}

function share(part, total) {
  return total ? `${Math.round((part / total) * 100)}% of all payments` : "no payments yet";
}
