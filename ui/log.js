/**
 * Real-time log tab — SSE connection to /api/log/stream
 *
 * Lifecycle: init() is called by app.js when the user switches to the log tab
 * (or on first load if it's the active tab). The SSE connection stays open
 * across tab switches so events are never missed.
 */
(function () {
  "use strict";

  // ── state ────────────────────────────────────────────────────────────────────
  let _es = null;          // EventSource
  let _rows = [];          // all received events (raw objects)
  let _filtered = [];      // rows after filter
  let _paused = false;
  let _autoScroll = true;
  let _selectedIdx = null; // index into _filtered
  let _eventCount = 0;
  let _retryTimer = null;
  let _project = "";       // current project (for URL)
  let _initialized = false;

  // ── DOM refs (resolved lazily) ────────────────────────────────────────────
  const $ = id => document.getElementById(id);

  // ── helpers ──────────────────────────────────────────────────────────────
  function _fmtTime(ts) {
    if (!ts) return "";
    const d = new Date(ts);
    if (isNaN(d)) return ts.slice(11, 19) || ts;
    return d.toTimeString().slice(0, 8);
  }

  function _sourceBadge(source) {
    const s = (source || "http").toLowerCase();
    const cls = s === "mcp" ? "log-badge-mcp" : s === "cli" ? "log-badge-cli" : "log-badge-http";
    return `<span class="log-badge ${cls}">${s.toUpperCase()}</span>`;
  }

  function _statusBadge(status) {
    const s = String(status || "");
    let cls = "log-badge-200";
    if (s.startsWith("4")) cls = "log-badge-400";
    else if (s.startsWith("5")) cls = "log-badge-500";
    return `<span class="log-badge ${cls}">${s}</span>`;
  }

  function _methodSpan(method) {
    const m = (method || "").toUpperCase();
    const cls = `log-badge-method-${m}`;
    return `<span class="${cls}">${m}</span>`;
  }

  function _durSpan(ms) {
    if (ms == null || ms === "") return "";
    const v = Number(ms).toFixed(0);
    const cls = Number(ms) > 500 ? "log-dur-slow" : "log-dur-fast";
    return `<span class="${cls}">${v}</span>`;
  }

  function _truncate(str, max) {
    if (!str) return "";
    const s = typeof str === "string" ? str : JSON.stringify(str);
    return s.length > max ? s.slice(0, max) + "…" : s;
  }

  // ── filter ───────────────────────────────────────────────────────────────
  function _applyFilter() {
    const text   = ($("logSearch")   || {}).value || "";
    const source = ($("logSourceFilter") || {}).value || "";
    const method = ($("logMethodFilter") || {}).value || "";
    const q = text.toLowerCase();

    _filtered = _rows.filter(ev => {
      if (source && (ev.source || "http").toLowerCase() !== source.toLowerCase()) return false;
      if (method && (ev.method || "").toUpperCase() !== method.toUpperCase()) return false;
      if (q) {
        const hay = [ev.path, ev.source, ev.project, ev.method, String(ev.status || ""),
                     JSON.stringify(ev.body || "")].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    _renderTable();
  }

  // ── render ───────────────────────────────────────────────────────────────
  function _renderTable() {
    const tbody = $("logBody");
    const empty = $("logEmpty");
    if (!tbody) return;

    if (_filtered.length === 0) {
      tbody.innerHTML = "";
      if (empty) empty.style.display = "";
      return;
    }
    if (empty) empty.style.display = "none";

    const frag = document.createDocumentFragment();
    _filtered.forEach((ev, idx) => {
      const tr = document.createElement("tr");
      if (idx === _selectedIdx) tr.classList.add("log-row-selected");

      const bodyPreview = _truncate(
        typeof ev.body === "object" ? JSON.stringify(ev.body) : (ev.body || ""), 80
      );

      tr.innerHTML = `
        <td class="log-col-time">${_fmtTime(ev.ts)}</td>
        <td class="log-col-source">${_sourceBadge(ev.source)}</td>
        <td class="log-col-method">${_methodSpan(ev.method)}</td>
        <td class="log-col-path" title="${(ev.path || "").replace(/"/g, "&quot;")}">${_truncate(ev.path, 50)}</td>
        <td class="log-col-project">${_truncate(ev.project, 20)}</td>
        <td class="log-col-status">${_statusBadge(ev.status)}</td>
        <td class="log-col-dur">${_durSpan(ev.duration_ms)}</td>
        <td class="log-col-body">${bodyPreview}</td>
      `;
      tr.addEventListener("click", () => _selectRow(idx));
      frag.appendChild(tr);
    });

    tbody.innerHTML = "";
    tbody.appendChild(frag);

    _updateCounter();
    if (_autoScroll) {
      const wrap = $("logTableWrap");
      if (wrap) wrap.scrollTop = wrap.scrollHeight;
    }
  }

  function _selectRow(idx) {
    _selectedIdx = idx;
    _renderTable();
    _openDrawer(_filtered[idx]);
  }

  function _openDrawer(ev) {
    const drawer = $("logDetailDrawer");
    const title  = $("logDetailTitle");
    const pre    = $("logDetailBody");
    if (!drawer || !pre) return;
    drawer.classList.remove("hidden");
    if (title) title.textContent = `${_fmtTime(ev.ts)}  ${ev.source || "http"} › ${ev.path || ""}`;
    pre.textContent = JSON.stringify(ev, null, 2);
  }

  function _updateCounter() {
    const el = $("logCounter");
    if (el) el.textContent = `${_eventCount} event${_eventCount !== 1 ? "s" : ""}  (${_filtered.length} shown)`;
  }

  // ── SSE connection ───────────────────────────────────────────────────────
  function _setStatus(state) {
    const dot  = $("logDot");
    const text = $("logStatusText");
    if (!dot) return;
    dot.className = "log-dot " + state;
    const labels = { connected: "Connected", disconnected: "Disconnected", connecting: "Connecting…" };
    if (text) text.textContent = labels[state] || state;
  }

  function _streamUrl() {
    const project = _project || (window.currentProject) || "";
    const base = window.location.origin || "http://127.0.0.1:5001";
    const url = new URL("/api/log/stream", base);
    if (project) url.searchParams.set("project", project);
    return url.toString();
  }

  function _historyUrl() {
    const project = _project || (window.currentProject) || "";
    const base = window.location.origin || "http://127.0.0.1:5001";
    const url = new URL("/api/log/entries", base);
    if (project) url.searchParams.set("project", project);
    return url.toString();
  }

  function _loadHistory() {
    fetch(_historyUrl())
      .then(r => r.ok ? r.json() : [])
      .then(events => {
        if (!Array.isArray(events)) return;
        events.forEach(ev => {
          _rows.push(ev);
          _eventCount++;
        });
        _applyFilter();
      })
      .catch(() => {});
  }

  function _connect() {
    if (_es) { _es.close(); _es = null; }
    clearTimeout(_retryTimer);
    _setStatus("connecting");

    _loadHistory();

    const es = new EventSource(_streamUrl());
    _es = es;

    es.onopen = () => {
      _setStatus("connected");
    };

    es.addEventListener("log", e => {
      if (_paused) return;
      try {
        const ev = JSON.parse(e.data);
        _rows.push(ev);
        _eventCount++;
        _applyFilter();
      } catch (_) {}
    });

    es.addEventListener("ping", () => {}); // keepalive, ignore

    es.onerror = () => {
      _setStatus("disconnected");
      es.close();
      _es = null;
      _retryTimer = setTimeout(_connect, 4000);
    };
  }

  // ── init ─────────────────────────────────────────────────────────────────
  function init(project) {
    if (project !== undefined) _project = project;

    if (!_initialized) {
      _initialized = true;
      _bindEvents();
    }

    // (Re)connect if project changed or connection dropped
    const needReconnect = !_es || _es.readyState === EventSource.CLOSED;
    if (needReconnect) _connect();
  }

  function _bindEvents() {
    // Filter inputs
    const search = $("logSearch");
    if (search) search.addEventListener("input", _applyFilter);

    const srcFilter = $("logSourceFilter");
    if (srcFilter) srcFilter.addEventListener("change", _applyFilter);

    const methodFilter = $("logMethodFilter");
    if (methodFilter) methodFilter.addEventListener("change", _applyFilter);

    // Pause toggle
    const pause = $("logPause");
    if (pause) pause.addEventListener("change", () => { _paused = pause.checked; });

    // Auto-scroll toggle
    const as = $("logAutoScroll");
    if (as) as.addEventListener("change", () => { _autoScroll = as.checked; });

    // Clear
    const clear = $("logClear");
    if (clear) clear.addEventListener("click", () => {
      _rows = [];
      _filtered = [];
      _eventCount = 0;
      _selectedIdx = null;
      const tbody = $("logBody");
      if (tbody) tbody.innerHTML = "";
      const empty = $("logEmpty");
      if (empty) empty.style.display = "";
      const drawer = $("logDetailDrawer");
      if (drawer) drawer.classList.add("hidden");
      _updateCounter();
    });

    // Export
    const exp = $("logExport");
    if (exp) exp.addEventListener("click", () => {
      const data = JSON.stringify(_rows, null, 2);
      const blob = new Blob([data], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `memory-log-${new Date().toISOString().slice(0,19).replace(/:/g,"-")}.json`;
      a.click();
      URL.revokeObjectURL(url);
    });

    // Drawer close
    const drawerClose = $("logDetailClose");
    if (drawerClose) drawerClose.addEventListener("click", () => {
      const drawer = $("logDetailDrawer");
      if (drawer) drawer.classList.add("hidden");
      _selectedIdx = null;
      _renderTable();
    });

    // Keyboard: Escape closes drawer
    document.addEventListener("keydown", e => {
      if (e.key === "Escape") {
        const drawer = $("logDetailDrawer");
        if (drawer && !drawer.classList.contains("hidden")) {
          drawer.classList.add("hidden");
          _selectedIdx = null;
          _renderTable();
        }
      }
    });
  }

  // ── public API (called by app.js tab switching) ───────────────────────────
  window.LogTab = { init };

  // Auto-init if log tab is already active on load
  document.addEventListener("DOMContentLoaded", () => {
    const logPane = document.getElementById("tab-log");
    if (logPane && !logPane.classList.contains("hidden")) {
      init();
    }
  });
})();
