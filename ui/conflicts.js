/**
 * conflicts.js — Conflict cards with side-by-side entries and action buttons.
 */
import { API, State, toast, showDialog } from './app.js';

// ── Rendering ─────────────────────────────────────────────────────────────────
function typePill(type) {
  return `<span class="pill pill-${type}">${(type || 'note').replace('_', ' ')}</span>`;
}

function statusPill(status) {
  return `<span class="pill pill-${status}">${status || 'active'}</span>`;
}

function renderEntrySnippet(entry, label) {
  if (!entry) return `<div class="conflict-entry"><h5>${label}</h5><p style="color:var(--muted)">Not found</p></div>`;
  return `
    <div class="conflict-entry">
      <h5>${label}</h5>
      <p style="margin-bottom:6px">${typePill(entry.type)} ${statusPill(entry.status)}</p>
      <p style="font-weight:600;margin-bottom:4px">${(entry.description || '—').slice(0, 100)}</p>
      ${entry.cause  ? `<p style="color:var(--muted);font-size:11px">Cause: ${entry.cause.slice(0,80)}</p>` : ''}
      ${entry.fix    ? `<p style="color:var(--muted);font-size:11px">Fix: ${entry.fix.slice(0,80)}</p>` : ''}
      <p style="color:var(--muted);font-size:11px;margin-top:4px">
        Conf: ${Math.round((entry.confidence||0.5)*100)}% &nbsp;|&nbsp;
        ID: <code>${(entry.id||'').slice(0,10)}…</code>
      </p>
    </div>
  `;
}

function renderConflicts(conflicts) {
  const grid = document.getElementById('conflictsGrid');
  const info = document.getElementById('conflictsInfo');

  const open = conflicts.filter(c => c.status !== 'resolved');
  info.textContent = `${open.length} open / ${conflicts.length} total`;

  if (!conflicts.length) {
    grid.innerHTML = `<div class="empty-state">✅ No conflicts found.</div>`;
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
            <strong>Conflict</strong>
            <span class="conflict-meta">
              &nbsp;Similarity: ${c.similarity ? Math.round(c.similarity*100)+'%' : '—'}
              &nbsp;|&nbsp; ${c.timestamp ? c.timestamp.slice(0,16).replace('T',' ') : ''}
            </span>
          </div>
          ${resolved
            ? `<span class="pill" style="background:rgba(63,185,80,0.2);color:var(--success)">resolved</span>`
            : ''}
        </div>

        ${c.reason ? `<p style="font-size:12px;color:var(--muted);margin-bottom:10px">⚠️ ${c.reason}</p>` : ''}

        <div class="conflict-entries">
          ${renderEntrySnippet(ea, 'Entry A')}
          ${renderEntrySnippet(eb, 'Entry B')}
        </div>

        ${resolved ? '' : `
          <div class="conflict-actions">
            <button class="btn btn-secondary resolve-btn" data-action="supersede_a" data-cid="${c.id}"
              title="Mark Entry A as superseded by B">Supersede A</button>
            <button class="btn btn-secondary resolve-btn" data-action="supersede_b" data-cid="${c.id}"
              title="Mark Entry B as superseded by A">Supersede B</button>
            <button class="btn btn-secondary resolve-btn" data-action="merge" data-cid="${c.id}"
              title="Merge entries (keeps A, supersedes B)">Merge</button>
            <button class="btn btn-ghost resolve-btn" data-action="dismiss" data-cid="${c.id}"
              title="Dismiss conflict without changing entries">Dismiss</button>
          </div>
        `}
      </div>
    `;
  }).join('');

  // Wire up resolve buttons
  grid.querySelectorAll('.resolve-btn').forEach(btn => {
    btn.addEventListener('click', () => handleResolve(btn.dataset.cid, btn.dataset.action, conflicts));
  });
}

async function handleResolve(conflictId, action, conflicts) {
  const conflict = conflicts.find(c => c.id === conflictId);
  if (!conflict) return;

  const labels = {
    supersede_a: 'Supersede Entry A',
    supersede_b: 'Supersede Entry B',
    merge:       'Merge entries',
    dismiss:     'Dismiss conflict',
  };

  showDialog(
    labels[action] || action,
    `Confirm: ${labels[action] || action}?`,
    async (reason) => {
      try {
        await API.post(`conflicts/${conflictId}/resolve`, API.withProject({ action, reason }));
        toast(`Conflict ${action.replace('_', ' ')} — done`, 'success');
        await loadAndRender();
        // Update badge
        updateBadge(conflicts.filter(c => c.status !== 'resolved').length - 1);
      } catch (e) {
        toast(`Error: ${e.message}`, 'error');
      }
    },
    true,  // show reason input
  );
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
}
