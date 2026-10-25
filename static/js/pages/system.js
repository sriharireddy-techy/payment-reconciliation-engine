// Database health: connection test, schema verification and setup guidance.
import { api } from "../api.js";
import { refreshDbPill } from "../app.js";
import { badge, dateTime, esc, loading } from "../ui.js";

export async function render(view) {
  view.innerHTML = `
    <div class="page-head"><div><h1>Database health</h1>
      <p>Connects to Oracle with the settings in <span class="mono">.env</span>, runs a read-only query and compares the data
      dictionary (<span class="mono">USER_TAB_COLUMNS</span>, <span class="mono">USER_CONSTRAINTS</span>) with the columns the application uses.</p></div>
      <button class="btn btn-outline-secondary btn-sm" id="recheck"><i class="bi bi-arrow-clockwise"></i> Test again</button></div>
    <div id="body"></div>`;
  const body = view.querySelector("#body");

  async function load() {
    loading(body, "Testing the Oracle connection&hellip;");
    let h;
    try {
      h = await api.get("/api/health/database");
    } catch (err) {
      h = err.body || { status: "unavailable", error: { message: err.message, code: err.code } };
    }
    refreshDbPill();
    const flags = `<dl class="kv"><dt>Connection method</dt><dd>${esc(h.connection_method || "-")}</dd>
      <dt>Connection pool size</dt><dd>${esc(h.db_pool_max ?? "-")}</dd>
      <dt>Failure simulation</dt><dd>${h.failure_simulation_enabled ? badge("ACTIVE") + " enabled for the demo" : badge("INACTIVE") + " disabled (safe default)"}</dd></dl>`;

    if (h.status === "not_configured") {
      body.innerHTML = `<div class="card-x"><h2><i class="bi bi-plug"></i>Not configured yet</h2>
        <p>Missing settings: ${h.missing.map((m) => `<span class="mono">${esc(m)}</span>`).join(", ")}.</p>
        ${setupSteps()}</div><div class="card-x"><h2>Settings</h2>${flags}</div>`;
      return;
    }
    if (!h.connected) {
      body.innerHTML = `<div class="card-x"><h2><i class="bi bi-x-octagon"></i>Cannot reach Oracle</h2>
        <p>${esc(h.error ? h.error.message : "Connection failed.")} ${h.error && h.error.details && h.error.details.oracle_code ? `<span class="mono">(${esc(h.error.details.oracle_code)})</span>` : ""}</p>
        <ul class="small"><li>ORA-01017: wrong username or password &mdash; regenerate the password in FreeSQL and update <span class="mono">.env</span>.</li>
        <li>ORA-12514 / timeouts: check host, port and service name exactly as FreeSQL shows them; check VPN or firewall.</li></ul>
        ${setupSteps()}</div><div class="card-x"><h2>Settings</h2>${flags}</div>`;
      return;
    }
    const s = h.schema;
    body.innerHTML = `
      <div class="kpi-grid">
        <div class="kpi"><div class="kpi-label">Connection</div><div class="kpi-value small">${badge("SUCCESS")}</div><div class="kpi-sub">as ${esc(h.database_user)}</div></div>
        <div class="kpi"><div class="kpi-label">Schema check</div><div class="kpi-value small">${badge(s.ok ? "SUCCESS" : "FAILED")}</div><div class="kpi-sub">10 project tables</div></div>
        <div class="kpi"><div class="kpi-label">Server time</div><div class="kpi-value small">${dateTime(h.server_time)}</div><div class="kpi-sub">UTC${esc(h.server_timezone)} &middot; timestamps are shown in this time</div></div>
        <div class="kpi"><div class="kpi-label">Oracle version</div><div class="kpi-value small">${esc(h.server_version)}</div></div>
      </div>
      <div class="row g-3">
        <div class="col-lg-6"><div class="card-x"><h2><i class="bi bi-table"></i>Tables</h2>
          <table class="table-x"><thead><tr><th>Table</th><th>Status</th><th>Notes</th></tr></thead><tbody>
          ${s.tables.map((t) => `<tr><td class="mono">${esc(t.table)}</td><td>${badge(t.ok ? "SUCCESS" : "FAILED")}</td>
            <td class="small">${!t.exists ? "table not found" : t.missing_columns.length ? `missing: ${t.missing_columns.map(esc).join(", ")}` : ""}
            ${t.unexpected_columns.length ? `extra: ${t.unexpected_columns.map(esc).join(", ")}` : ""}</td></tr>`).join("")}
          </tbody></table></div></div>
        <div class="col-lg-6"><div class="card-x"><h2><i class="bi bi-shield-lock"></i>Constraints the logic relies on</h2>
          <table class="table-x"><tbody>
          ${s.constraints.map((c) => `<tr><td class="mono">${esc(c.name)}</td><td class="small">${esc(c.purpose)}</td><td>${badge(c.present ? "SUCCESS" : "FAILED")}</td></tr>`).join("")}
          </tbody></table>
          <p class="small mt-2 mb-0">Optional migration 001 (DUPLICATE_EXTERNAL exception type):
            ${s.duplicate_exception_type_enabled ? badge("ACTIVE") : `<span class="status status-neutral">not applied</span>`}</p></div>
          <div class="card-x"><h2>Settings</h2>${flags}<p class="mt-2 mb-0"><a href="/docs" target="_blank" rel="noopener">Open the Swagger API documentation</a></p></div></div>
      </div>`;
  }

  view.querySelector("#recheck").addEventListener("click", load);
  await load();
}

function setupSteps() {
  return `<ol class="small">
    <li>Sign in at <span class="mono">freesql.com</span> and open <strong>Schema Connection Details</strong> (the account that owns the 10 tables).</li>
    <li>Copy the hostname, port, service name and username; generate a connection password there.</li>
    <li>In the project folder: <span class="mono">Copy-Item .env.example .env</span>, then fill in <span class="mono">ORACLE_USER</span>,
      <span class="mono">ORACLE_PASSWORD</span>, <span class="mono">ORACLE_HOST</span>, <span class="mono">ORACLE_PORT</span> and <span class="mono">ORACLE_SERVICE_NAME</span>.</li>
    <li>Restart the server (<span class="mono">uvicorn app:app --reload</span>) and press <em>Test again</em>.</li></ol>`;
}
