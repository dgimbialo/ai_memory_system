/**
 * conflicts.js — Conflict cards with side-by-side entries and action buttons.
 */
import { API, State, toast, showDialog, t } from './app.js';

let currentConflicts = [];
let selectedConflictForResolve = null;

// ── Modal for full conflict view ───────────────────────────────────────────────
function renderFullEntry(entry) {
  if (!entry) return `<div style="color:var(--muted)">Not found</div>`;
  return `
    <div style="padding:20px;border-left:3px solid var(--accent);background:rgba(88,166,255,0.05);border-radius:var(--radius)">
      <div style="display:flex;gap:10px;margin-bottom:12px">
        <span class="pill pill-${entry.type}">${(entry.type || 'note').replace('_', ' ')}</span>
        <span class="pill pill-${entry.status}">${entry.status || 'active'}</span>
        <span style="color:var(--muted);font-size:12px">Conf: ${Math.round((entry.confidence||0.5)*100)}%</span>
      </div>
      <h4 style="margin-bottom:10px;font-size:16px;color:var(--text)">${entry.description || '—'}</h4>
      ${entry.cause ? `<div style="margin-bottom:10px"><strong style="color:var(--muted)">Cause:</strong><p style="color:var(--text);margin-top:4px">${entry.cause}</p></div>` : ''}
      ${entry.fix ? `<div style="margin-bottom:10px"><strong style="color:var(--muted)">Fix:</strong><p style="color:var(--text);margin-top:4px">${entry.fix}</p></div>` : ''}
      ${entry.files?.length ? `<div style="margin-bottom:10px"><strong style="color:var(--muted)">Files:</strong><ul style="margin-top:4px;margin-left:20px;color:var(--text)">${entry.files.map(f => `<li>${f}</li>`).join('')}</ul></div>` : ''}
      <div style="margin-top:15px;padding-top:10px;border-top:1px solid var(--border);color:var(--muted);font-size:11px">
        ID: <code>${entry.id}</code> | ${entry.timestamp ? entry.timestamp.slice(0, 16).replace('T', ' ') : ''}
      </div>
    </div>
  `;
}

function showFullConflict(conflictId) {
  const conflict = currentConflicts.find(c => c.id === conflictId);
  if (!conflict) return;

  const overlay = document.getElementById('conflictCompareOverlay');
  const contentA = document.getElementById('conflictCompareA');
  const contentB = document.getElementById('conflictCompareB');

  contentA.innerHTML = `<h3 style="margin-bottom:15px;color:var(--accent)">Entry A</h3>${renderFullEntry(conflict.entry_a)}`;
  contentB.innerHTML = `<h3 style="margin-bottom:15px;color:var(--accent)">Entry B</h3>${renderFullEntry(conflict.entry_b)}`;

  overlay.classList.remove('hidden');

  // Close on background click
  overlay.addEventListener('click', e => {
    if (e.target === overlay) overlay.classList.add('hidden');
  });
}

// ── Rendering ─────────────────────────────────────────────────────────────────
function typePill(type) {
  return `<span class="pill pill-${type}">${(type || 'note').replace('_', ' ')}</span>`;
}

function statusPill(status) {
  return `<span class="pill pill-${status}">${status || 'active'}</span>`;
}

function renderEntrySnippet(entry, label, conflictId) {
  if (!entry) return `<div class="conflict-entry"><h5>${label}</h5><p style="color:var(--muted)">Not found</p></div>`;
  return `
    <div class="conflict-entry" data-conflict-id="${conflictId}" style="cursor:pointer;transition:background 0.15s">
      <h5>${label}</h5>
      <p style="margin-bottom:6px">${typePill(entry.type)} ${statusPill(entry.status)}</p>
      <p style="font-weight:600;margin-bottom:4px">${(entry.description || '—').slice(0, 100)}</p>
      ${entry.cause  ? `<p style="color:var(--muted);font-size:11px">${t('detail.cause')}: ${entry.cause.slice(0,80)}</p>` : ''}
      ${entry.fix    ? `<p style="color:var(--muted);font-size:11px">${t('detail.fix')}: ${entry.fix.slice(0,80)}</p>` : ''}
      <p style="color:var(--muted);font-size:11px;margin-top:4px">
        ${t('entries.confidence')}: ${Math.round((entry.confidence||0.5)*100)}% &nbsp;|&nbsp;
        ID: <code>${(entry.id||'').slice(0,10)}…</code>
      </p>
    </div>
  `;
}

function renderConflicts(conflicts) {
  const grid = document.getElementById('conflictsGrid');
  const info = document.getElementById('conflictsInfo');

  currentConflicts = conflicts;  // Save for modal
  const open = conflicts.filter(c => c.status !== 'resolved');
  info.innerHTML = `${open.length} <span data-i18n="conflicts.open">${t('conflicts.open')}</span> / ${conflicts.length} <span data-i18n="conflicts.total">${t('conflicts.total')}</span>`;

  if (!conflicts.length) {
    grid.innerHTML = `<div class="empty-state">✅ ${t('conflicts.noConflicts')}</div>`;
    return;
  }

  grid.innerHTML = conflicts.map(c => {
    const ea = c.entry_a;
    const eb = c.entry_b;
    const resolved = c.status === 'resolved';

    return `
      <div class="conflict-card ${resolved ? 'conflict-resolved' : ''}" data-id="${c.id}">
        <div class="conflict-header">
          <div>
            <strong>${t('conflicts.cardConflict')}</strong>
            <span class="conflict-meta">
              &nbsp;${t('conflicts.cardSimilarity')}: ${c.similarity ? Math.round(c.similarity*100)+'%' : '—'}
              &nbsp;|&nbsp; ${c.timestamp ? c.timestamp.slice(0,16).replace('T',' ') : ''}
            </span>
          </div>
          ${resolved
            ? `<span class="pill" style="background:rgba(63,185,80,0.2);color:var(--success)">${t('conflicts.cardResolved')}</span>`
            : ''}
        </div>

        ${c.reason ? `<p style="font-size:12px;color:var(--muted);margin-bottom:10px">⚠️ ${c.reason}</p>` : ''}

        <div class="conflict-entries">
          ${renderEntrySnippet(ea, 'Entry A', c.id)}
          ${renderEntrySnippet(eb, 'Entry B', c.id)}
        </div>

        ${resolved ? '' : `
          <div class="conflict-actions">
            <button class="btn btn-primary" data-cid="${c.id}" onclick="window.openConflictResolveModal('${c.id}')"
              title="Open conflict comparison and resolution options">${t('conflicts.resolve')}</button>
          </div>
        `}
      </div>
    `;
  }).join('');

  // Wire up resolve buttons
  grid.querySelectorAll('.resolve-btn').forEach(btn => {
    btn.addEventListener('click', () => handleResolve(btn.dataset.cid, btn.dataset.action, conflicts));
  });

  // Wire up entry snippet clicks for full conflict view
  grid.querySelectorAll('.conflict-entry').forEach(entry => {
    entry.addEventListener('click', e => {
      e.stopPropagation();
      const conflictId = entry.dataset.conflictId;
      showFullConflict(conflictId);
    });
    // Hover effect
    entry.addEventListener('mouseenter', () => {
      entry.style.background = 'rgba(88,166,255,0.08)';
    });
    entry.addEventListener('mouseleave', () => {
      entry.style.background = '';
    });
  });
}

// ── Open conflict resolve modal ────────────────────────────────────────────────
export function openConflictResolveModal(conflictId) {
  const conflict = currentConflicts.find(c => c.id === conflictId);
  if (!conflict) return;

  selectedConflictForResolve = conflict;
  
  // Show the full conflict comparison
  showFullConflictWithActions(conflict);
}

// ── Modal interaction state ───────────────────────────────────────────────────
let _modalInteracting = false;  // True during drag/resize — prevents overlay close

// ── Show conflict with action buttons in modal ─────────────────────────────────
function showFullConflictWithActions(conflict) {
  const overlay = document.getElementById('conflictCompareOverlay');
  const contentA = document.getElementById('conflictCompareA');
  const contentB = document.getElementById('conflictCompareB');
  const actionsDiv = document.getElementById('conflictCompareActions');
  const modal = document.getElementById('conflictCompareModal');

  contentA.innerHTML = `<h3 style="margin-bottom:15px;color:var(--accent)">${t('conflicts.entryA')}</h3>${renderFullEntry(conflict.entry_a)}`;
  contentB.innerHTML = `<h3 style="margin-bottom:15px;color:var(--accent)">${t('conflicts.entryB')}</h3>${renderFullEntry(conflict.entry_b)}`;

  // Render compact button layout
  actionsDiv.innerHTML = `
    <button class="btn btn-secondary" onclick="window.handleConflictAction('${conflict.id}', 'supersede_a')" style="flex:0 1 auto">${t('conflicts.supersede_a')}</button>
    <button class="btn btn-secondary" onclick="window.handleConflictAction('${conflict.id}', 'supersede_b')" style="flex:0 1 auto">${t('conflicts.supersede_b')}</button>
    <button class="btn btn-secondary" onclick="window.handleConflictAction('${conflict.id}', 'merge')" style="flex:0 1 auto">${t('conflicts.merge')}</button>
    <button class="btn btn-danger" onclick="window.handleConflictAction('${conflict.id}', 'dismiss')" style="flex:0 1 auto">${t('conflicts.dismiss')}</button>
    <textarea id="conflictReason" class="dialog-reason" placeholder="${t('conflicts.enter_reason')}" rows="1" style="flex:1 1 100%;margin-top:0;min-width:200px"></textarea>
  `;

  overlay.classList.remove('hidden');

  // Setup drag, resize, context menu (safe to call multiple times — they guard internally)
  setupModalResize(modal);
  setupModalDrag(modal);
  setupContextMenu(contentA, contentB);
}

// ── Modal Resizing ────────────────────────────────────────────────────────────
let _resizeSetup = false;
function setupModalResize(modal) {
  if (_resizeSetup) return;
  _resizeSetup = true;

  const resizeHandle = document.getElementById('resizeHandle');
  if (!resizeHandle) return;

  let isResizing = false;
  let startX, startY, startWidth, startHeight;

  resizeHandle.addEventListener('mousedown', e => {
    isResizing = true;
    _modalInteracting = true;
    startX = e.clientX;
    startY = e.clientY;
    startWidth = modal.offsetWidth;
    startHeight = modal.offsetHeight;
    e.preventDefault();
    e.stopPropagation();
  });

  document.addEventListener('mousemove', e => {
    if (!isResizing) return;
    const dX = e.clientX - startX;
    const dY = e.clientY - startY;
    modal.style.width = Math.max(400, startWidth + dX) + 'px';
    modal.style.maxHeight = Math.max(300, startHeight + dY) + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (isResizing) {
      isResizing = false;
      // Keep flag set briefly so the overlay click handler won't fire
      setTimeout(() => { _modalInteracting = false; }, 100);
    }
  });
}

// ── Modal Dragging ───────────────────────────────────────────────────────────
let _dragSetup = false;
function setupModalDrag(modal) {
  if (_dragSetup) return;
  _dragSetup = true;

  const header = document.getElementById('conflictCompareHeader');
  if (!header) return;

  let isDragging = false;
  let offsetX, offsetY;

  header.addEventListener('mousedown', e => {
    if (e.target.tagName === 'BUTTON') return;
    isDragging = true;
    _modalInteracting = true;
    const rect = modal.getBoundingClientRect();
    offsetX = e.clientX - rect.left;
    offsetY = e.clientY - rect.top;
    e.preventDefault();
  });

  document.addEventListener('mousemove', e => {
    if (!isDragging) return;
    modal.style.position = 'absolute';
    modal.style.left = Math.max(0, e.clientX - offsetX) + 'px';
    modal.style.top = Math.max(0, e.clientY - offsetY) + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (isDragging) {
      isDragging = false;
      setTimeout(() => { _modalInteracting = false; }, 100);
    }
  });
}

// ── Context Menu for Inline Translation ──────────────────────────────────────
let _contextMenuSetup = false;
function setupContextMenu(contentA, contentB) {
  if (_contextMenuSetup) return;
  _contextMenuSetup = true;

  const handleContextMenu = (e) => {
    e.preventDefault();
    const selectedText = window.getSelection().toString().trim();
    if (selectedText) {
      showTranslateContextMenu(e, selectedText);
    }
  };

  contentA.addEventListener('contextmenu', handleContextMenu);
  contentB.addEventListener('contextmenu', handleContextMenu);
}

// ── Inline context menu ───────────────────────────────────────────────────────
function showTranslateContextMenu(e, selectedText) {
  const existing = document.getElementById('translateContextMenuPopup');
  if (existing) existing.remove();

  const menu = document.createElement('div');
  menu.id = 'translateContextMenuPopup';
  menu.style.cssText = `
    position: fixed;
    left: ${e.clientX}px;
    top: ${e.clientY}px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    z-index: 2000;
    padding: 8px 0;
    min-width: 200px;
  `;

  menu.innerHTML = `
    <button style="display:block;width:100%;text-align:left;padding:8px 16px;border:none;background:none;color:var(--text);cursor:pointer;font-size:14px"
      onmouseover="this.style.background='var(--accent)';this.style.color='var(--bg)'"
      onmouseout="this.style.background='none';this.style.color='var(--text)'"
      id="translateInlineBtn">
      🌐 ${t('ui.translate')}
    </button>
  `;

  document.body.appendChild(menu);

  menu.querySelector('#translateInlineBtn').addEventListener('click', async () => {
    menu.remove();
    await translateSelectionInline(selectedText);
  });

  setTimeout(() => {
    document.addEventListener('click', () => { if (menu.parentElement) menu.remove(); }, { once: true });
  }, 0);
}

// ── Translate selected text inline via Google Translate free API ──────────────
async function translateSelectionInline(selectedText) {
  try {
    const url = `https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=uk&dt=t&q=${encodeURIComponent(selectedText)}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error('Translation request failed');
    const data = await resp.json();

    // data[0] is array of [translated, original, ...] chunks
    const translated = data[0].map(chunk => chunk[0]).join('');

    if (!translated) return;

    // Replace the selected text in the DOM
    const selection = window.getSelection();
    if (selection.rangeCount > 0) {
      const range = selection.getRangeAt(0);
      range.deleteContents();
      const span = document.createElement('span');
      span.style.cssText = 'background:rgba(88,166,255,0.15);border-radius:2px';
      span.title = `Original: ${selectedText}`;
      span.textContent = translated;
      range.insertNode(span);
      selection.removeAllRanges();
    }
  } catch (err) {
    toast(`Translation error: ${err.message}`, 'error');
  }
}

// ── Handle conflict action selection ───────────────────────────────────────────
export async function handleConflictAction(conflictId, action) {
  const conflict = currentConflicts.find(c => c.id === conflictId);
  if (!conflict) return;

  const reason = document.getElementById('conflictReason')?.value.trim() || '';

  try {
    await API.post(`conflicts/${conflictId}/resolve`, API.withProject({ action, reason }));
    toast(`${t('conflicts.resolve')} — ${action.replace('_', ' ')}`, 'success');
    document.getElementById('conflictCompareOverlay').classList.add('hidden');
    await loadAndRender();
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  }
}

function updateBadge(count) {
  const badge = document.getElementById('conflictBadge');
  badge.textContent    = count;
  badge.style.display  = count > 0 ? 'inline' : 'none';
}

// ── Load and render ───────────────────────────────────────────────────────────
async function loadAndRender() {
  try {
    const conflicts = await API.get('conflicts', API.withProject());
    renderConflicts(conflicts);
    const openCount = conflicts.filter(c => c.status !== 'resolved').length;
    updateBadge(openCount);
  } catch (err) {
    document.getElementById('conflictsGrid').innerHTML =
      `<div class="empty-state">⚠️ Failed to load conflicts: ${err.message}</div>`;
    toast(`Conflicts error: ${err.message}`, 'error');
  }
}

export async function initConflicts() {
  await loadAndRender();
  document.getElementById('reloadConflicts').onclick = loadAndRender;

  // Make functions globally accessible for inline onclick handlers
  window.openConflictResolveModal = openConflictResolveModal;
  window.handleConflictAction = handleConflictAction;

  // Setup overlay close on background click — once, with drag/resize guard
  const overlay = document.getElementById('conflictCompareOverlay');
  overlay.addEventListener('click', e => {
    if (e.target === overlay && !_modalInteracting) {
      overlay.classList.add('hidden');
    }
  });
}
