/**
 * graph.js — vis.js dependency graph visualisation.
 * Node color by type, size by confidence, click to see detail.
 * Filters: type checkboxes, hide-superseded toggle, min-confidence slider.
 */
import { API, State, toast } from './app.js';

let network   = null;
let rawNodes  = [];
let rawEdges  = [];
let physicsOn = true;

// ── Type → color mapping ──────────────────────────────────────────────────────
const TYPE_COLOR = {
  decision: { bg: '#1c3854', border: '#58a6ff', font: '#79c0ff' },
  bug_fix:  { bg: '#3b1111', border: '#f85149', font: '#ff9492' },
  feature:  { bg: '#0d2e1a', border: '#3fb950', font: '#56d364' },
  note:     { bg: '#1e2430', border: '#8b949e', font: '#adbac7' },
};

function nodeColor(type, status) {
  const c = TYPE_COLOR[type] || TYPE_COLOR.note;
  const opacity = status === 'superseded' ? 0.4 : 1;
  return {
    background:  c.bg,
    border:      c.border,
    highlight:   { background: c.border, border: '#fff' },
    hover:       { background: c.bg,     border: '#fff' },
  };
}

// ── Build vis datasets ────────────────────────────────────────────────────────
function buildDatasets(nodes, edges) {
  const typeFilters = [...document.querySelectorAll('.graph-type-filter')]
    .filter(cb => cb.checked).map(cb => cb.value);
  const hideSuperseded = document.getElementById('hideSuperseded').checked;
  const minConf = parseFloat(document.getElementById('graphMinConf').value || 0);

  const visibleIds = new Set();
  const visNodes = [];

  for (const n of nodes) {
    if (!typeFilters.includes(n.type))              continue;
    if (hideSuperseded && n.status === 'superseded') continue;
    if ((n.confidence || 0) < minConf)               continue;
    visibleIds.add(n.id);
    const size = 10 + Math.round((n.confidence || 0.5) * 20);
    const c    = nodeColor(n.type, n.status);
    const fontColor = (TYPE_COLOR[n.type] || TYPE_COLOR.note).font;
    visNodes.push({
      id:      n.id,
      label:   n.label,
      title:   buildTooltip(n),
      color:   c,
      size:    size,
      font:    { color: fontColor, size: 11, face: 'monospace' },
      borderWidth: 2,
      shape:   n.type === 'decision' ? 'diamond' : 'dot',
    });
  }

  const visEdges = edges
    .filter(e => visibleIds.has(e.from) && visibleIds.has(e.to))
    .map(e => ({
      from:   e.from,
      to:     e.to,
      arrows: { to: { enabled: true, scaleFactor: 0.7 } },
      color:  { color: 'rgba(88,166,255,0.5)', highlight: '#58a6ff', hover: '#58a6ff' },
      width:  1.5,
      smooth: { type: 'cubicBezier', forceDirection: 'vertical', roundness: 0.4 },
    }));

  return { visNodes, visEdges };
}

function buildTooltip(n) {
  const div = document.createElement('div');
  div.style.cssText = 'background:#1c2128;border:1px solid #30363d;padding:8px 12px;border-radius:6px;font-size:12px;max-width:280px;color:#e6edf3;';
  div.innerHTML = `
    <strong>${n.label}</strong><br>
    <span style="color:#8b949e">${n.type} · ${n.status}</span><br>
    <span>Confidence: ${Math.round((n.confidence||0.5)*100)}%</span>
    ${n.files?.length ? `<br><span style="color:#8b949e;font-size:11px">${n.files[0]}</span>` : ''}
  `;
  return div;
}

// ── Render network ────────────────────────────────────────────────────────────
function renderNetwork() {
  if (!rawNodes.length) {
    document.getElementById('graphContainer').innerHTML =
      `<div class="empty-state">No dependency links found.<br>
       Use <code>add_link</code> or <code>--depends-on</code> to create links.</div>`;
    return;
  }

  const { visNodes, visEdges } = buildDatasets(rawNodes, rawEdges);

  if (!visNodes.length) {
    document.getElementById('graphContainer').innerHTML =
      `<div class="empty-state">No nodes match current filters.</div>`;
    if (network) { network.destroy(); network = null; }
    return;
  }

  const container = document.getElementById('graphContainer');
  // Clear any empty-state message
  if (!network) container.innerHTML = '';

  const nodes = new vis.DataSet(visNodes);
  const edges = new vis.DataSet(visEdges);

  const options = {
    nodes: {
      shape: 'dot',
      size:  14,
      font:  { size: 11, face: 'monospace' },
      borderWidth: 2,
    },
    edges: {
      smooth: { type: 'dynamic' },
    },
    physics: {
      enabled: physicsOn,
      stabilization: { iterations: 150, updateInterval: 25 },
      barnesHut: {
        gravitationalConstant: -4000,
        centralGravity: 0.15,
        springLength: 130,
        springConstant: 0.05,
        damping: 0.3,
      },
    },
    interaction: {
      hover:        true,
      tooltipDelay: 150,
      navigationButtons: false,
      keyboard: true,
      zoomView: true,
    },
    layout: {
      improvedLayout: true,
    },
  };

  if (network) {
    network.setData({ nodes, edges });
    network.setOptions(options);
  } else {
    network = new vis.Network(container, { nodes, edges }, options);
  }

  // Click node → show detail
  network.on('click', params => {
    if (params.nodes.length > 0) {
      const nodeId = params.nodes[0];
      const n = rawNodes.find(n => n.id === nodeId);
      if (n) showGraphDetail(n);
    } else {
      hideGraphDetail();
    }
  });

  // Double-click → fit
  network.on('doubleClick', () => network.fit({ animation: true }));
}

// ── Node detail panel ─────────────────────────────────────────────────────────
function showGraphDetail(n) {
  const panel   = document.getElementById('graphDetail');
  const content = document.getElementById('graphDetailContent');
  panel.classList.remove('hidden');

  const files = (n.files || []).map(f => `<code style="font-size:11px">${f}</code>`).join('<br>');
  const tags  = (n.tags  || []).map(t => `<span class="tag-chip">${t}</span>`).join(' ');

  content.innerHTML = `
    <h4 style="margin-bottom:10px;word-break:break-word;font-size:13px">${n.label}</h4>
    <div class="detail-field">
      <label>Type / Status</label>
      <p>
        <span class="pill pill-${n.type}">${(n.type||'note').replace('_',' ')}</span>
        <span class="pill pill-${n.status}">${n.status||'active'}</span>
      </p>
    </div>
    <div class="detail-field">
      <label>Confidence</label>
      <p>${Math.round((n.confidence||0.5)*100)}%</p>
    </div>
    <div class="detail-field">
      <label>ID</label>
      <p><code style="font-size:11px">${n.id}</code></p>
    </div>
    ${files ? `<div class="detail-field"><label>Files</label><div>${files}</div></div>` : ''}
    ${tags  ? `<div class="detail-field"><label>Tags</label><div>${tags}</div></div>` : ''}
    <button class="btn btn-ghost" style="margin-top:10px;font-size:12px"
      id="graphSuggestLinks">🔗 Suggest links</button>
    <div id="suggestResult" style="margin-top:8px;font-size:12px;color:var(--muted)"></div>
  `;

  document.getElementById('graphSuggestLinks').addEventListener('click', async () => {
    const res = document.getElementById('suggestResult');
    res.textContent = 'Searching…';
    try {
      const suggestions = await API.get(`suggest_links/${n.id}`, API.withProject());
      if (!suggestions.length) {
        res.textContent = 'No suggestions found.';
        return;
      }
      res.innerHTML = suggestions.map(s =>
        `<div style="margin:4px 0">→ <code style="font-size:11px">${s.id?.slice(0,10)}…</code>
          <span style="color:var(--text)">${(s.description||'').slice(0,60)}</span>
          <span style="color:var(--muted)">(${Math.round((s.similarity||0)*100)}%)</span>
        </div>`
      ).join('');
    } catch (e) {
      res.textContent = `Error: ${e.message}`;
    }
  });
}

function hideGraphDetail() {
  document.getElementById('graphDetail').classList.add('hidden');
}

// ── Controls ──────────────────────────────────────────────────────────────────
function wireControls() {
  // Type filter checkboxes
  document.querySelectorAll('.graph-type-filter').forEach(cb => {
    cb.addEventListener('change', renderNetwork);
  });
  document.getElementById('hideSuperseded').addEventListener('change', renderNetwork);

  // Confidence slider
  const slider    = document.getElementById('graphMinConf');
  const sliderLbl = document.getElementById('graphMinConfVal');
  slider.addEventListener('input', () => {
    sliderLbl.textContent = parseFloat(slider.value).toFixed(1);
    renderNetwork();
  });

  // Physics toggle
  document.getElementById('graphPhysicsToggle').addEventListener('click', btn => {
    physicsOn = !physicsOn;
    btn.target.textContent = physicsOn ? '⏸ Pause physics' : '▶ Resume physics';
    if (network) network.setOptions({ physics: { enabled: physicsOn } });
  });

  // Fit button
  document.getElementById('graphFit').addEventListener('click', () => {
    if (network) network.fit({ animation: { duration: 500 } });
  });

  // Reload
  document.getElementById('graphReload').addEventListener('click', loadAndRender);

  // Close detail
  document.getElementById('graphDetailClose').addEventListener('click', hideGraphDetail);
}

// ── Load and render ───────────────────────────────────────────────────────────
async function loadAndRender() {
  try {
    const data = await API.get('graph', API.withProject());
    rawNodes   = data.nodes || [];
    rawEdges   = data.edges || [];
    renderNetwork();
  } catch (err) {
    toast(`Graph error: ${err.message}`, 'error');
    document.getElementById('graphContainer').innerHTML =
      `<div class="empty-state">⚠️ Failed to load graph: ${err.message}</div>`;
  }
}

export async function initGraph() {
  wireControls();
  await loadAndRender();
}
