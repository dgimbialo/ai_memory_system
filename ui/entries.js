/**
 * entries.js — Filterable, paginated entries table with detail panel.
 */
import { API, State, toast, showDialog, t } from './app.js';

const PAGE_SIZE = 50;
let allEntries = [];
let filtered   = [];
let currentPage = 1;

// ── Rendering helpers ─────────────────────────────────────────────────────────
function typePill(type) {
  return `<span class="pill pill-${type}">${(type || 'note').replace('_', ' ')}</span>`;
}

function statusPill(status) {
  return `<span class="pill pill-${status}">${status || 'active'}</span>`;
}

function confBar(conf) {
  const pct   = Math.round((conf || 0.5) * 100);
  const color = pct >= 80 ? '#3fb950' : pct >= 50 ? '#58a6ff' : pct >= 30 ? '#d29922' : '#f85149';
  return `<span class="conf-bar" style="width:${pct * 0.5}px;background:${color};height:6px;"></span>${pct}%`;
}

function shortFile(f) {
  const parts = (f || '').replace(/\\/g, '/').split('/');
  return parts.slice(-2).join('/');
}

function fmtDate(ts) {
  if (!ts) return '—';
  return ts.slice(0, 16).replace('T', ' ');
}

// ── Build table rows ──────────────────────────────────────────────────────────
function renderTable() {
  const start = (currentPage - 1) * PAGE_SIZE;
  const page  = filtered.slice(start, start + PAGE_SIZE);
  const tbody = document.getElementById('entriesBody');
  if (!page.length) {
    tbody.innerHTML = `<tr><td colspan="6"><div class="empty-state">${t('entries.noEntriesFound')}</div></td></tr>`;
    return;
  }
  tbody.innerHTML = page.map(e => `
    <tr data-id="${e.id}" title="${(e.description || '').replace(/"/g, '&quot;')}">
      <td>${typePill(e.type)}</td>
      <td>${statusPill(e.status)}</td>
      <td>${confBar(e.confidence)}</td>
      <td class="desc-cell">${(e.description || '—').slice(0, 90)}${(e.description || '').length > 90 ? '…' : ''}</td>
      <td>${(e.files || []).slice(0,2).map(shortFile).join('<br>')}</td>
      <td style="white-space:nowrap">${fmtDate(e.timestamp)}</td>
    </tr>
  `).join('');

  // Row click → detail panel
  tbody.querySelectorAll('tr[data-id]').forEach(row => {
    row.addEventListener('click', () => {
      const entry = filtered.find(e => e.id === row.dataset.id);
      if (entry) showDetail(entry);
    });
  });
}

// ── Pagination ────────────────────────────────────────────────────────────────
function renderPagination() {
  const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
  const container  = document.getElementById('entriesPagination');
  if (totalPages <= 1) { container.innerHTML = ''; return; }

  const pages = [];
  for (let i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || Math.abs(i - currentPage) <= 2) {
      pages.push(i);
    } else if (pages[pages.length - 1] !== '…') {
      pages.push('…');
    }
  }
  container.innerHTML = pages.map(p =>
    typeof p === 'number'
      ? `<button class="page-btn ${p === currentPage ? 'active' : ''}" data-page="${p}">${p}</button>`
      : `<span style="color:var(--muted);padding:4px">…</span>`
  ).join('');

  container.querySelectorAll('.page-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      currentPage = parseInt(btn.dataset.page, 10);
      renderTable();
      renderPagination();
    });
  });
}

// ── Detail panel ──────────────────────────────────────────────────────────────
function showDetail(entry) {
  const panel   = document.getElementById('entryDetail');
  const content = document.getElementById('detailContent');

  // Highlight selected row
  document.querySelectorAll('#entriesBody tr').forEach(r => {
    r.classList.toggle('selected', r.dataset.id === entry.id);
  });

  const tags = (entry.tags || []).map(t =>
    `<span class="tag-chip">${t}</span>`).join(' ') || '<span style="color:var(--muted)">—</span>';

  const deps = (entry.depends_on || []).map(id =>
    `<code title="${id}">${id.slice(0, 10)}…</code>`).join(', ') || '—';

  const reqBy = (entry.required_by || []).map(id =>
    `<code title="${id}">${id.slice(0, 10)}…</code>`).join(', ') || '—';

  const testIds = (entry.test_ids || []).join(', ') || '—';
  const files = (entry.files || []).map(f => `<code>${f}</code>`).join('<br>') || '—';
  const decisions = (entry.decisions || []).map(d => `<li>${d}</li>`).join('') || '';

  content.innerHTML = `
    <h4 style="margin-bottom:12px;word-break:break-word">${entry.description || '(no description)'}</h4>
    <div class="detail-field">
      <label>Type / Status</label>
      <p>${typePill(entry.type)} ${statusPill(entry.status)}</p>
    </div>
    <div class="detail-field">
      <label>Confidence</label>
      <p>${confBar(entry.confidence)} (${entry.confidence ?? 0.5})</p>
    </div>
    <div class="detail-field">
      <label>ID</label>
      <p><code>${entry.id || '—'}</code></p>
    </div>
    <div class="detail-field">
      <label>Cause</label>
      <p>${entry.cause || '—'}</p>
    </div>
    <div class="detail-field">
      <label>Fix</label>
      <p>${entry.fix || '—'}</p>
    </div>
    ${decisions ? `<div class="detail-field"><label>Decisions</label><ul style="padding-left:18px;font-size:12px">${decisions}</ul></div>` : ''}
    <div class="detail-field"><label>Files</label><div>${files}</div></div>
    <div class="detail-field"><label>Tags</label><div>${tags}</div></div>
    <div class="detail-field"><label>Depends on</label><p>${deps}</p></div>
    <div class="detail-field"><label>Required by</label><p>${reqBy}</p></div>
    <div class="detail-field"><label>Linked tests</label><p>${testIds}</p></div>
    <div class="detail-field"><label>Date</label><p>${fmtDate(entry.timestamp)}</p></div>

    <hr style="border-color:var(--border);margin:12px 0">

    <!-- Inline actions -->
    <div style="display:flex;flex-direction:column;gap:10px">
      <div>
        <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">CHANGE STATUS</label>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          ${['active','resolved','superseded'].map(s =>
            `<button class="btn btn-ghost btn-sm" data-action="status" data-value="${s}"
              style="font-size:11px;padding:3px 8px">${s}</button>`
          ).join('')}
        </div>
      </div>
      <div>
        <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">CONFIDENCE</label>
        <div style="display:flex;gap:6px;align-items:center">
          <input type="range" id="confSlider" min="0" max="1" step="0.05"
            value="${entry.confidence ?? 0.5}" style="flex:1;accent-color:var(--accent)">
          <span id="confSliderVal" style="font-size:12px;width:36px">${Math.round((entry.confidence??0.5)*100)}%</span>
          <button class="btn btn-ghost" data-action="confidence" style="font-size:11px;padding:3px 8px">Apply</button>
        </div>
      </div>
      <div>
        <label style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px">ADD TAG</label>
        <div style="display:flex;gap:6px">
          <input type="text" id="newTagInput" class="search-box" placeholder="tag name"
            style="flex:1;font-size:12px">
          <button class="btn btn-ghost" data-action="addtag" style="font-size:11px;padding:3px 8px">+ Add</button>
        </div>
      </div>
    </div>
  `;

  panel.classList.remove('hidden');

  // Confidence slider live update
  const slider  = content.querySelector('#confSlider');
  const sliderLbl = content.querySelector('#confSliderVal');
  slider.addEventListener('input', () => {
    sliderLbl.textContent = `${Math.round(slider.value * 100)}%`;
  });

  // Action buttons
  content.querySelectorAll('[data-action]').forEach(btn => {
    btn.addEventListener('click', () => handleDetailAction(btn, entry));
  });
}

async function handleDetailAction(btn, entry) {
  const action = btn.dataset.action;
  if (action === 'status') {
    const newStatus = btn.dataset.value;
    try {
      await API.patch(`entry/${entry.id}`, API.withProject({ status: newStatus }));
      entry.status = newStatus;
      toast(`Status → ${newStatus}`, 'success');
      renderTable();
      showDetail(entry);
    } catch (e) { toast(e.message, 'error'); }
  } else if (action === 'confidence') {
    const newConf = parseFloat(document.getElementById('confSlider').value);
    try {
      await API.patch(`entry/${entry.id}`, API.withProject({ confidence: newConf }));
      entry.confidence = newConf;
      toast(`Confidence → ${Math.round(newConf*100)}%`, 'success');
      renderTable();
      showDetail(entry);
    } catch (e) { toast(e.message, 'error'); }
  } else if (action === 'addtag') {
    const newTag = document.getElementById('newTagInput').value.trim();
    if (!newTag) return;
    const tags = [...new Set([...(entry.tags || []), newTag])];
    try {
      await API.patch(`entry/${entry.id}`, API.withProject({ tags }));
      entry.tags = tags;
      toast(`Tag "${newTag}" added`, 'success');
      showDetail(entry);
    } catch (e) { toast(e.message, 'error'); }
  }
}

// ── Filters ───────────────────────────────────────────────────────────────────
function applyFilters() {
  const q      = document.getElementById('entrySearch').value.trim().toLowerCase();
  const type   = document.getElementById('filterType').value;
  const status = document.getElementById('filterStatus').value;
  const file   = document.getElementById('filterFile').value.trim().toLowerCase();
  const tag    = document.getElementById('filterTag').value.trim().toLowerCase();

  filtered = allEntries.filter(e => {
    if (type   && e.type   !== type)   return false;
    if (status && e.status !== status) return false;
    if (file   && !(e.files || []).some(f => f.toLowerCase().includes(file))) return false;
    if (tag    && !(e.tags  || []).some(t => t.toLowerCase().includes(tag)))  return false;
    if (q) {
      const text = [e.description, e.cause, e.fix, ...(e.decisions||[])].join(' ').toLowerCase();
      if (!text.includes(q)) return false;
    }
    return true;
  });
  currentPage = 1;
  renderTable();
  renderPagination();
}

function clearFilters() {
  document.getElementById('entrySearch').value  = '';
  document.getElementById('filterType').value   = '';
  document.getElementById('filterStatus').value = '';
  document.getElementById('filterFile').value   = '';
  document.getElementById('filterTag').value    = '';
  filtered = [...allEntries];
  currentPage = 1;
  renderTable();
  renderPagination();
}

// ── Init ──────────────────────────────────────────────────────────────────────
export async function initEntries() {
  try {
    allEntries = await API.get('entries', API.withProject());
    filtered   = [...allEntries];
    currentPage = 1;
    renderTable();
    renderPagination();
  } catch (err) {
    toast(`Entries error: ${err.message}`, 'error');
  }

  document.getElementById('applyFilters').onclick  = applyFilters;
  document.getElementById('clearFilters').onclick  = clearFilters;
  document.getElementById('detailClose').onclick   = () => {
    document.getElementById('entryDetail').classList.add('hidden');
    document.querySelectorAll('#entriesBody tr').forEach(r => r.classList.remove('selected'));
  };

  // Trigger on Enter in search box
  document.getElementById('entrySearch').addEventListener('keydown', e => {
    if (e.key === 'Enter') applyFilters();
  });
}
