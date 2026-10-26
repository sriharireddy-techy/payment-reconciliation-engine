// Chart.js helpers. Colours come from CSS tokens so light/dark both work.
// Status colours always appear with a labelled legend; single series use --series-1.

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export const statusColor = {
  SUCCESS: () => token("--good"),
  FAILED: () => token("--critical"),
  PENDING: () => token("--warning"),
  INITIATED: () => token("--warning"),
  PROCESSING: () => token("--warning"),
  REVERSED: () => token("--serious"),
  REPLAYED: () => token("--series-1"),
  ERROR: () => token("--serious"),
};

function base() {
  const ink2 = token("--ink-2");
  const grid = token("--hairline");
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 250 },
    plugins: {
      legend: { display: false },
      tooltip: { backgroundColor: token("--surface"), titleColor: token("--ink"), bodyColor: ink2, borderColor: grid, borderWidth: 1, padding: 10 },
    },
    scales: {
      x: { grid: { color: grid, drawTicks: false }, border: { color: token("--axis") }, ticks: { color: ink2, font: { size: 11 } } },
      y: { grid: { color: grid, drawTicks: false }, border: { color: token("--axis") }, ticks: { color: ink2, font: { size: 11 } } },
    },
  };
}

export function legendHtml(items) {
  return `<div class="legend-x">${items.map((i) => `<span><i class="swatch" style="background:${i.color}"></i>${i.label}</span>`).join("")}</div>`;
}

// Horizontal bars, one colour per category (e.g. status) or one series colour.
export function horizontalBar(canvas, labels, values, colors, label = "Count") {
  const opts = base();
  opts.indexAxis = "y";
  opts.scales.x.beginAtZero = true;
  opts.scales.x.ticks.precision = 0;
  opts.scales.y.grid.display = false;
  return new Chart(canvas, {
    type: "bar",
    data: { labels, datasets: [{ label, data: values, backgroundColor: colors, borderRadius: 4, borderSkipped: "start", maxBarThickness: 22 }] },
    options: opts,
  });
}

// Stacked daily columns (e.g. successful vs failed payments per day).
export function stackedColumns(canvas, labels, series) {
  const opts = base();
  opts.scales.x.stacked = true;
  opts.scales.x.grid.display = false;
  opts.scales.y.stacked = true;
  opts.scales.y.beginAtZero = true;
  opts.scales.y.ticks.precision = 0;
  opts.plugins.tooltip.mode = "index";
  return new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: series.map((s) => ({
        label: s.label, data: s.values, backgroundColor: s.color, borderRadius: 4, borderSkipped: "start",
        borderColor: token("--surface"), borderWidth: { top: 2 }, maxBarThickness: 28,
      })),
    },
    options: opts,
  });
}

// Concurrency timeline: one floating bar per request, from start to finish (ms).
export function timeline(canvas, requests) {
  const opts = base();
  opts.indexAxis = "y";
  opts.scales.x.beginAtZero = true;
  opts.scales.x.title = { display: true, text: "milliseconds after the barrier released all requests", color: token("--ink-2"), font: { size: 11 } };
  opts.scales.y.grid.display = false;
  opts.plugins.tooltip.callbacks = {
    label: (ctx) => {
      const r = requests[ctx.dataIndex];
      return `${r.outcome}${r.reason ? ` (${r.reason})` : ""}: ${r.started_ms} → ${r.finished_ms} ms`;
    },
  };
  return new Chart(canvas, {
    type: "bar",
    data: {
      labels: requests.map((r) => `#${r.request}`),
      datasets: [{
        label: "Request",
        data: requests.map((r) => [r.started_ms, Math.max(r.finished_ms, r.started_ms + 1)]),
        backgroundColor: requests.map((r) => (statusColor[r.outcome] || statusColor.ERROR)()),
        borderRadius: 4, borderSkipped: false, maxBarThickness: 16,
      }],
    },
    options: opts,
  });
}
