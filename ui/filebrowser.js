/**
 * filebrowser.js — File list with entry-count badges and per-file detail panel.
 */
import { API, State, toast } from './app.js';

let allFiles = [];  // [{file, count, entries}]

// ── Build file list from entry data ──────────────────────────────────────────
function buildFileList(entries) {
  const map = new Map();
  for (const e of entries) {
    for (const f of (e.files || [])) {
      if (!map.has(f)) map.set(f, { file: f, count: 0, entries: [] });
      const rec = map.get(f);
      rec.count++;
      rec.entries.push(e);
    }
  }
  return [...map.values()];
}

// ── Render file list ──────────────────────────────────────────────────────────
function renderFileList(files) {
  const container = document.getElementById('fileList');
  if (!files.length) {
    container.innerHTML = `<div class="empty-state">No files found.</div>`;
    return;
  }
  container.innerHTML = files.map(f => `
    <div class="file-item" data-file="${escAttr(f.file)}">
      <span class="file-name" title="${escAttr(f.file)}">${f.file}</span>
      <span class="file-count">${f.count}</span>
    </div>
  `).join('');

  container.querySelectorAll('.file-item').forEach(item => {
    item.addEventListener('click', () => {
      container.querySelectorAll('.file-item').forEach(i => i.classList.remove('selected'));
      item.classList.add('selected');
      showFileDetail(item.dataset.file);
    });
  });
}

// ── Show file detail panel ────────────────────────────────────────────────────
async function showFileDetail(filePath) {
  const detail  = document.getElementById('fileDetail');
  const content = document.getElementById('fileDetailContent');
  detail.classList.remove('hidden');
  content.innerHTML = '<p style="color:var(--muted)">Loading…</p>';

  try {
    const data = await API.get('file_summary', API.withProject({ file: filePath }));
    const summary  = data.summary  || '';
    const entries  = data.entries  || [];
    const summaryHtml = summary
      ? `<div style="font-size:13px;color:var(--muted);background:var(--surface2);
                     border:1px solid var(--border);border-radius:6px;padding:12px;
                     margin-bottom:14px;white-space:pre-wrap">${summary}</div>`
      : '';

    const typePill = (t) => `<span class="pill pill-${t}" style="font-size:10px">${(t||'').replace('_',' ')}</span>`;
    const confPct  = (c) => `${Math.round((c??0.5)*100)}%`;

    const rows = entries.map(e => `
      <tr>
        <td>${typePill(e.type)}</td>
        <td style="font-size:12px">${(e.description||'—').slice(0,80)}</td>
        <td style="font-size:11px;color:var(--muted)">${confPct(e.confidence)}</td>
        <td style="font-size:11px;color:var(--muted)">${(e.timestamp||'').slice(0,10)}</td>
      </tr>
    `).join('');

    content.innerHTML = `
      <h4 style="margin-bottom:8px;word-break:break-all;font-size:13px">${filePath}</h4>
      <p style="font-size:12px;color:var(--muted);margin-bottom:12px">${entries.length} entries</p>
      ${summaryHtml}
      <table class="data-table" style="font-size:12px">
        <thead>
          <tr><th>Type</th><th>Description</th><th>Conf</th><th>Date</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  } catch (err) {
    content.innerHTML = `<p style="color:var(--error)">Error: ${err.message}</p>`;
    toast(err.message, 'error');
  }
}

// ── Filter and sort ───────────────────────────────────────────────────────────
function applyFilter() {
  const q    = document.getElementById('fileSearch').value.trim().toLowerCase();
  const sort = document.getElementById('fileSortBy').value;

  let result = q ? allFiles.filter(f => f.file.toLowerCase().includes(q)) : [...allFiles];
  result.sort((a, b) => sort === 'name'
    ? a.file.localeCompare(b.file)
    : b.count - a.count);

  renderFileList(result);
}

// ── Escape attr helper ────────────────────────────────────────────────────────
function escAttr(s) {
  return (s || '').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

// ── Init ──────────────────────────────────────────────────────────────────────
export async function initFileBrowser() {
  try {
    const entries = await API.get('entries', API.withProject());
    allFiles = buildFileList(entries).sort((a, b) => b.count - a.count);
    renderFileList(allFiles);
  } catch (err) {
    toast(`File browser error: ${err.message}`, 'error');
  }

  document.getElementById('fileSearch').addEventListener('input', applyFilter);
  document.getElementById('fileSortBy').addEventListener('change', applyFilter);
  document.getElementById('fileDetailClose').addEventListener('click', () => {
    document.getElementById('fileDetail').classList.add('hidden');
    document.querySelectorAll('.file-item').forEach(i => i.classList.remove('selected'));
  });
}
