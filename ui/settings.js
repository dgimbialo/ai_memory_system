/**
 * settings.js — Settings form + operations panel.
 */
import { API, State, toast } from './app.js';

let currentSettings = null;
const defaultTags   = [];

// ── Load settings from API ────────────────────────────────────────────────────
async function loadSettings() {
  try {
    currentSettings = await API.get('settings', API.withProject());
    populateForm(currentSettings);
  } catch (err) {
    toast(`Settings load error: ${err.message}`, 'error');
  }
}

// ── Populate the form with settings object ────────────────────────────────────
function populateForm(s) {
  const d = s.decay           || {};
  const dd = s.deduplication || {};
  const rv = s.revert_detection || {};
  const sc = s.stale_check   || {};
  const wi = s.wiki           || {};
  const q  = s.query          || {};

  setCheck('decayEnabled',   d.enabled  !== false);
  setNum(  'decayHalfLife',  d.half_life_days ?? 60);
  setNum(  'decayMinConf',   d.min_confidence ?? 0.40);

  setCheck('dedupEnabled',   dd.enabled !== false);
  setNum(  'dedupThreshold', dd.threshold ?? 0.88);

  setCheck('revertEnabled',  rv.enabled !== false);
  setNum(  'revertThreshold',rv.threshold ?? 2);

  setNum('staleMinAge',    sc.min_age_days ?? 7);
  setCheck('wikiAutoRender', wi.auto_render !== false);
  setNum('queryTopK',       q.top_k    ?? 10);
  setNum('queryDecayBlend', q.decay_blend ?? 0.10);

  renderTagList(s.default_tags || []);
}

function setCheck(id, val) {
  const el = document.getElementById(id);
  if (el) el.checked = !!val;
}
function setNum(id, val) {
  const el = document.getElementById(id);
  if (el) el.value = val;
}

// ── Tag editor ────────────────────────────────────────────────────────────────
let _tags = [];

function renderTagList(tags) {
  _tags = [...tags];
  const list = document.getElementById('defaultTagsList');
  list.innerHTML = _tags.map((t, i) => `
    <span class="tag-chip">
      ${t}
      <button type="button" data-idx="${i}" title="Remove tag">✕</button>
    </span>
  `).join('');
  list.querySelectorAll('button[data-idx]').forEach(btn => {
    btn.addEventListener('click', () => {
      _tags.splice(parseInt(btn.dataset.idx, 10), 1);
      renderTagList(_tags);
    });
  });
}

// ── Gather form → settings object ────────────────────────────────────────────
function gatherSettings() {
  return {
    decay: {
      enabled:         getCheck('decayEnabled'),
      half_life_days:  getNum('decayHalfLife'),
      min_confidence:  getNum('decayMinConf'),
    },
    deduplication: {
      enabled:   getCheck('dedupEnabled'),
      threshold: getNum('dedupThreshold'),
    },
    revert_detection: {
      enabled:   getCheck('revertEnabled'),
      threshold: getNum('revertThreshold'),
    },
    stale_check: {
      min_age_days: getNum('staleMinAge'),
    },
    wiki: {
      auto_render: getCheck('wikiAutoRender'),
    },
    query: {
      top_k:        getNum('queryTopK'),
      decay_blend:  getNum('queryDecayBlend'),
    },
    default_tags: [..._tags],
  };
}

function getCheck(id) { return !!(document.getElementById(id)?.checked); }
function getNum(id)   { return parseFloat(document.getElementById(id)?.value || 0); }

// ── Save settings ─────────────────────────────────────────────────────────────
async function saveSettings(evt) {
  evt.preventDefault();
  const body = API.withProject(gatherSettings());
  // Move project param to query string, body is settings only
  const project = body.project;
  delete body.project;
  try {
    const result = await API.post(`settings${project ? '?project=' + encodeURIComponent(project) : ''}`, body);
    toast('Settings saved ✓', 'success');
    currentSettings = result.settings || body;
  } catch (err) {
    toast(`Save error: ${err.message}`, 'error');
  }
}

// ── Operations ────────────────────────────────────────────────────────────────
async function runOp(op) {
  const resultEl = document.getElementById('opResult');
  const resultPre= document.getElementById('opResultPre');
  resultEl.classList.remove('hidden');
  resultPre.textContent = 'Running…';

  const project = State.project;
  const params  = project ? `?project=${encodeURIComponent(project)}` : '';
  let body      = {};

  if (op === 'decay') {
    const s = (currentSettings?.decay) || {};
    body = {
      dry_run:       document.getElementById('opDecayDryRun')?.checked ?? true,
      half_life_days: s.half_life_days ?? 60,
      min_confidence: s.min_confidence ?? 0.40,
    };
  } else if (op === 'deduplicate') {
    const s = (currentSettings?.deduplication) || {};
    body = {
      dry_run:   document.getElementById('opDedupDryRun')?.checked ?? true,
      threshold: s.threshold ?? 0.88,
    };
  }

  try {
    const result = await API.post(`ops/${op}${params}`, body);
    resultPre.textContent = JSON.stringify(result, null, 2);
    toast(`Operation "${op}" complete`, 'success');
  } catch (err) {
    resultPre.textContent = `Error: ${err.message}`;
    toast(`Operation failed: ${err.message}`, 'error');
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
export async function initSettings() {
  await loadSettings();

  document.getElementById('settingsForm').addEventListener('submit', saveSettings);

  document.getElementById('resetSettings').addEventListener('click', () => {
    if (currentSettings) populateForm(currentSettings);
    else loadSettings();
    toast('Reset to saved settings', 'info');
  });

  document.getElementById('addTagBtn').addEventListener('click', () => {
    const input = document.getElementById('tagInput');
    const tag   = input.value.trim();
    if (tag && !_tags.includes(tag)) {
      _tags.push(tag);
      renderTagList(_tags);
    }
    input.value = '';
  });

  document.getElementById('tagInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      document.getElementById('addTagBtn').click();
    }
  });

  // Operations buttons
  document.querySelectorAll('[data-op]').forEach(btn => {
    btn.addEventListener('click', () => runOp(btn.dataset.op));
  });
}
