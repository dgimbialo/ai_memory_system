/**
 * app.js — AI Memory System dashboard entry point.
 * Manages: global state, API wrapper, tab routing, project selector, toasts.
 */

import { initDashboard, refreshDashboard } from './dashboard.js';
import { initEntries }    from './entries.js';
import { initConflicts }  from './conflicts.js';
import { initSettings }   from './settings.js';
import { initFileBrowser }from './filebrowser.js';
import { initGraph }      from './graph.js';

// ── Global state ──────────────────────────────────────────────────────────────
export const State = {
  project:    null,   // currently selected project name
  projects:   [],     // [{name, entry_count}]
  activeTab:  'dashboard',
};

// ── API helper ────────────────────────────────────────────────────────────────
export const API = {
  _base: '/api',

  async get(endpoint, params = {}) {
    const url = new URL(`${this._base}/${endpoint}`, location.origin);
    for (const [k, v] of Object.entries(params)) {
      if (v !== null && v !== undefined && v !== '') {
        url.searchParams.set(k, String(v));
      }
    }
    const res = await fetch(url.toString());
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || res.statusText);
    }
    return res.json();
  },

  async post(endpoint, body = {}) {
    const res = await fetch(`${this._base}/${endpoint}`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || res.statusText);
    }
    return res.json();
  },

  async patch(endpoint, body = {}) {
    const res = await fetch(`${this._base}/${endpoint}`, {
      method:  'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || res.statusText);
    }
    return res.json();
  },

  withProject(params = {}) {
    if (State.project) params.project = State.project;
    return params;
  },
};

// ── Toast notifications ───────────────────────────────────────────────────────
export function toast(message, type = 'info', duration = 3500) {
  const container = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity 0.3s';
    setTimeout(() => el.remove(), 350);
  }, duration);
}

// ── Confirm dialog ────────────────────────────────────────────────────────────
export function showDialog(title, message, onConfirm, showReason = false) {
  const overlay  = document.getElementById('dialogOverlay');
  const titleEl  = document.getElementById('dialogTitle');
  const msgEl    = document.getElementById('dialogMessage');
  const reasonEl = document.getElementById('dialogReason');
  const confirmBtn = document.getElementById('dialogConfirm');
  const cancelBtn  = document.getElementById('dialogCancel');

  titleEl.textContent  = title;
  msgEl.textContent    = message;
  reasonEl.style.display = showReason ? 'block' : 'none';
  reasonEl.value       = '';
  overlay.classList.remove('hidden');

  const cleanup = () => {
    overlay.classList.add('hidden');
    confirmBtn.replaceWith(confirmBtn.cloneNode(true));
    cancelBtn.replaceWith(cancelBtn.cloneNode(true));
  };

  document.getElementById('dialogConfirm').addEventListener('click', () => {
    cleanup();
    onConfirm(reasonEl.value.trim());
  }, { once: true });

  document.getElementById('dialogCancel').addEventListener('click', () => {
    cleanup();
  }, { once: true });
}

// ── Tab switching ─────────────────────────────────────────────────────────────
const tabInitialised = new Set();

function activateTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  document.querySelectorAll('.tab-pane').forEach(p => {
    p.classList.toggle('hidden', p.id !== `tab-${name}`);
    p.classList.toggle('active', p.id === `tab-${name}`);
  });

  State.activeTab = name;

  if (!tabInitialised.has(name)) {
    tabInitialised.add(name);
    if (name === 'dashboard')  initDashboard();
    if (name === 'entries')    initEntries();
    if (name === 'conflicts')  initConflicts();
    if (name === 'settings')   initSettings();
    if (name === 'files')      initFileBrowser();
    if (name === 'graph')      initGraph();
  } else {
    // Refresh on revisit
    if (name === 'dashboard')  refreshDashboard();
    if (name === 'conflicts')  initConflicts();
    if (name === 'graph')      { /* graph refreshes on user demand */ }
  }
}

// ── Project selector ──────────────────────────────────────────────────────────
async function loadProjects() {
  try {
    State.projects = await API.get('projects');
    const sel = document.getElementById('projectSelect');
    sel.innerHTML = '';
    if (State.projects.length === 0) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = '(no projects)';
      sel.appendChild(opt);
    } else {
      State.projects.forEach(p => {
        const opt = document.createElement('option');
        opt.value       = p.name;
        opt.textContent = `${p.name} (${p.entry_count})`;
        sel.appendChild(opt);
      });
      // Pre-select: prefer project from URL hash, else first
      const hash = (location.hash || '').replace('#', '').split('?')[0];
      const initial = State.projects.find(p => p.name === hash) || State.projects[0];
      sel.value    = initial.name;
      State.project = initial.name;
    }
  } catch (err) {
    toast(`Failed to load projects: ${err.message}`, 'error');
  }
}

async function updateConflictBadge() {
  try {
    const data = await API.get('stats', API.withProject());
    const n = data.open_conflicts || 0;
    const badge = document.getElementById('conflictBadge');
    badge.textContent = n;
    badge.style.display = n > 0 ? 'inline' : 'none';
  } catch (_) { /* silent */ }
}

// ── Wiring ────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await loadProjects();

  // Tab buttons
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => activateTab(btn.dataset.tab));
  });

  // Dashboard "conflict banner" links
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-goto]');
    if (el) {
      e.preventDefault();
      activateTab(el.dataset.goto);
    }
  });

  // Project selector change
  document.getElementById('projectSelect').addEventListener('change', e => {
    State.project = e.target.value;
    tabInitialised.clear();
    activateTab(State.activeTab);
    updateConflictBadge();
  });

  // Refresh button
  document.getElementById('refreshBtn').addEventListener('click', () => {
    tabInitialised.delete(State.activeTab);
    activateTab(State.activeTab);
    updateConflictBadge();
  });

  // Initial tab
  activateTab('dashboard');
  updateConflictBadge();
  // Refresh badge every 60 s
  setInterval(updateConflictBadge, 60_000);
});
