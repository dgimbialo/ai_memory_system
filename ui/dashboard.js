/**
 * dashboard.js — KPI cards + Chart.js charts.
 */
import { API, State, toast } from './app.js';

// Keep chart instances so we can destroy & recreate on project switch
const charts = {};

const CHART_DEFAULTS = {
  color: '#e6edf3',
  gridColor: 'rgba(48,54,61,0.8)',
};

// ── KPI cards ─────────────────────────────────────────────────────────────────
function renderKPIs(stats) {
  const set = (id, val) => {
    const el = document.querySelector(`#${id} .kpi-value`);
    if (el) el.textContent = val;
  };
  set('kpi-total',      stats.total ?? '—');
  set('kpi-bugs',       stats.by_type?.bug_fix  ?? 0);
  set('kpi-features',   stats.by_type?.feature   ?? 0);
  set('kpi-decisions',  stats.by_type?.decision  ?? 0);
  set('kpi-confidence', stats.avg_confidence != null
      ? `${Math.round(stats.avg_confidence * 100)}%` : '—');
  set('kpi-conflicts',  stats.open_conflicts ?? 0);
  set('kpi-linked',     stats.linked_entries ?? 0);
  set('kpi-active',     stats.by_status?.active ?? 0);

  // Conflict banner
  const n = stats.open_conflicts ?? 0;
  const banner = document.getElementById('conflictBanner');
  if (n > 0) {
    document.getElementById('bannerConflictCount').textContent = n;
    banner.style.display = 'block';
  } else {
    banner.style.display = 'none';
  }
}

// ── Chart helpers ─────────────────────────────────────────────────────────────
function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

function baseFont() {
  return { color: CHART_DEFAULTS.color, family: "'Segoe UI', sans-serif", size: 11 };
}

// Doughnut: entry types
function renderTypesChart(byType) {
  destroyChart('types');
  const ctx = document.getElementById('chartTypes').getContext('2d');
  const labels = Object.keys(byType);
  const data   = Object.values(byType);
  const colors = {
    bug_fix:  '#f85149', feature: '#3fb950',
    decision: '#58a6ff', note:    '#8b949e',
  };
  charts.types = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{ data, backgroundColor: labels.map(l => colors[l] || '#8b949e'), borderWidth: 0 }],
    },
    options: {
      plugins: {
        legend: { labels: { color: CHART_DEFAULTS.color, font: baseFont(), padding: 12 } },
      },
      cutout: '60%',
      maintainAspectRatio: false,
    },
  });
}

// Line: entries per day
function renderTimelineChart(perDay) {
  destroyChart('timeline');
  const ctx = document.getElementById('chartTimeline').getContext('2d');
  charts.timeline = new Chart(ctx, {
    type: 'line',
    data: {
      labels: perDay.map(d => d.date.slice(5)),  // MM-DD
      datasets: [{
        label: 'Entries',
        data:  perDay.map(d => d.count),
        borderColor:     '#58a6ff',
        backgroundColor: 'rgba(88,166,255,0.12)',
        fill: true,
        tension: 0.3,
        pointRadius: 2,
        borderWidth: 1.5,
      }],
    },
    options: {
      scales: {
        x: { ticks: { color: CHART_DEFAULTS.color, font: baseFont(), maxTicksLimit: 10 },
             grid: { color: CHART_DEFAULTS.gridColor } },
        y: { ticks: { color: CHART_DEFAULTS.color, font: baseFont(), stepSize: 1 },
             grid: { color: CHART_DEFAULTS.gridColor }, beginAtZero: true },
      },
      plugins: { legend: { display: false } },
      maintainAspectRatio: false,
    },
  });
}

// Horizontal bar: top files
function renderFilesChart(topFiles) {
  destroyChart('files');
  const ctx = document.getElementById('chartFiles').getContext('2d');
  const names = topFiles.map(f => {
    const parts = f.file.replace(/\\/g, '/').split('/');
    return parts.slice(-2).join('/');
  });
  charts.files = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: names,
      datasets: [{
        label: 'Entries',
        data: topFiles.map(f => f.count),
        backgroundColor: 'rgba(88,166,255,0.6)',
        borderColor: '#58a6ff',
        borderWidth: 1,
      }],
    },
    options: {
      indexAxis: 'y',
      scales: {
        x: { ticks: { color: CHART_DEFAULTS.color, font: baseFont() },
             grid: { color: CHART_DEFAULTS.gridColor }, beginAtZero: true },
        y: { ticks: { color: CHART_DEFAULTS.color, font: { ...baseFont(), size: 10 } },
             grid: { display: false } },
      },
      plugins: { legend: { display: false } },
      maintainAspectRatio: false,
    },
  });
}

// Bar: confidence histogram
function renderConfidenceChart(buckets) {
  destroyChart('confidence');
  const ctx = document.getElementById('chartConfidence').getContext('2d');
  const labels = ['0–10%','10–20%','20–30%','30–40%','40–50%',
                   '50–60%','60–70%','70–80%','80–90%','90–100%'];
  const bgColors = buckets.map((_, i) => {
    const pct = (i + 0.5) / 10;
    if (pct >= 0.8) return 'rgba(63,185,80,0.7)';
    if (pct >= 0.5) return 'rgba(88,166,255,0.7)';
    if (pct >= 0.3) return 'rgba(210,153,34,0.7)';
    return 'rgba(248,81,73,0.7)';
  });
  charts.confidence = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{ label: 'Entries', data: buckets, backgroundColor: bgColors, borderWidth: 0 }],
    },
    options: {
      scales: {
        x: { ticks: { color: CHART_DEFAULTS.color, font: baseFont(), maxRotation: 45 },
             grid: { display: false } },
        y: { ticks: { color: CHART_DEFAULTS.color, font: baseFont(), stepSize: 1 },
             grid: { color: CHART_DEFAULTS.gridColor }, beginAtZero: true },
      },
      plugins: { legend: { display: false } },
      maintainAspectRatio: false,
    },
  });
}

// Horizontal bar: top tags
function renderTagsChart(topTags) {
  destroyChart('tags');
  const ctx = document.getElementById('chartTags').getContext('2d');
  const colors = ['#58a6ff','#3fb950','#d29922','#f85149','#8b949e',
                   '#a371f7','#39d353','#ffa657','#79c0ff','#56d364'];
  charts.tags = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: topTags.map(t => t.tag),
      datasets: [{
        label: 'Count',
        data:  topTags.map(t => t.count),
        backgroundColor: topTags.map((_, i) => colors[i % colors.length]),
        borderWidth: 0,
      }],
    },
    options: {
      scales: {
        x: { ticks: { color: CHART_DEFAULTS.color, font: baseFont() },
             grid: { color: CHART_DEFAULTS.gridColor }, beginAtZero: true },
        y: { ticks: { color: CHART_DEFAULTS.color, font: baseFont() },
             grid: { display: false } },
      },
      plugins: { legend: { display: false } },
      maintainAspectRatio: false,
      indexAxis: 'y',
    },
  });
}

// Doughnut: status breakdown
function renderStatusChart(byStatus) {
  destroyChart('status');
  const ctx = document.getElementById('chartStatus').getContext('2d');
  const labels = Object.keys(byStatus);
  const data   = Object.values(byStatus);
  const colors = { active: '#3fb950', resolved: '#8b949e', superseded: '#d29922', conflict: '#f85149' };
  charts.status = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{ data, backgroundColor: labels.map(l => colors[l] || '#8b949e'), borderWidth: 0 }],
    },
    options: {
      plugins: { legend: { labels: { color: CHART_DEFAULTS.color, font: baseFont(), padding: 12 } } },
      cutout: '60%',
      maintainAspectRatio: false,
    },
  });
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function loadAndRender() {
  try {
    const stats = await API.get('stats', API.withProject());
    renderKPIs(stats);
    if (stats.by_type)               renderTypesChart(stats.by_type);
    if (stats.entries_per_day)        renderTimelineChart(stats.entries_per_day);
    if (stats.top_files?.length)      renderFilesChart(stats.top_files);
    if (stats.confidence_histogram)   renderConfidenceChart(stats.confidence_histogram);
    if (stats.top_tags?.length)       renderTagsChart(stats.top_tags);
    if (stats.by_status)              renderStatusChart(stats.by_status);
  } catch (err) {
    toast(`Dashboard error: ${err.message}`, 'error');
  }
}

export function initDashboard()    { loadAndRender(); }
export function refreshDashboard() { loadAndRender(); }
