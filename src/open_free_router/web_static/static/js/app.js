function $(id){ return document.getElementById(id); }

// Auth: the dashboard's write endpoints (POST /api/config, /api/providers,
// /api/refresh) require a local token — see `ui.token` next to config.yaml.
// We ask for it once per browser session and remember it in sessionStorage
// (cleared when the tab closes) so a reload doesn't re-prompt.
function getAuthToken() {
  let tok = sessionStorage.getItem('ofr_token');
  if (!tok) {
    tok = prompt(
      'Enter the dashboard auth token (see ui.token next to config.yaml, ' +
      'or the "Auth token stored at" line printed when the server started):'
    ) || '';
    sessionStorage.setItem('ofr_token', tok);
  }
  return tok;
}

// Wrapper around fetch() that attaches the auth header for POST requests
// and clears the cached token (so the next call re-prompts) on 401.
async function authFetch(path, options = {}) {
  const opts = { ...options, headers: { ...(options.headers || {}) } };
  if ((opts.method || 'GET').toUpperCase() === 'POST') {
    opts.headers['Authorization'] = 'Bearer ' + getAuthToken();
  }
  const r = await fetch(path, opts);
  if (r.status === 401) {
    sessionStorage.removeItem('ofr_token');
  }
  return r;
}

// Tabs
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');
    btn.classList.add('active');
    $('tab-' + btn.dataset.tab).style.display = 'block';
    if (btn.dataset.tab === 'config') loadConfig();
    if (btn.dataset.tab === 'models') loadModels();
    if (btn.dataset.tab === 'providers') loadProviders();
  });
});

// Dashboard
async function loadStatus() {
  const r = await fetch('/api/status');
  const data = await r.json();
  $('providers').innerHTML = data.providers.map(p => `
    <div class="provider-row">
      <div class="dot ${p.auto_refresh ? 'ok' : 'manual'}"></div>
      <div class="pname">${p.name} <span class="badge">${p.model_count} models</span></div>
      <div class="pmeta">${p.auto_refresh ? 'auto-refresh' : 'manual'}</div>
    </div>
  `).join('') || '<div class="status-line">No providers configured</div>';

  const sched = data.scheduler || {};
  const warn = $('scheduler-warning');
  if (sched.last_error) {
    $('scheduler-warning-text').textContent =
      'The last scheduled refresh raised an exception (see server logs for the ' +
      'full traceback). Auto-refresh will retry on the next interval, but ' +
      'model lists may be stale until then.';
    warn.style.display = 'block';
  } else {
    warn.style.display = 'none';
  }

  const tiers = data.tiers || {};
  const tierNames = Object.keys(tiers);
  $('tiers').innerHTML = tierNames.length ? tierNames.map(name => `
    <div class="tier-block">
      <h3>tier/${name}</h3>
      ${(tiers[name] || []).map(inst => `
        <div class="provider-row">
          <div class="dot ${inst.in_cooldown ? 'manual' : 'ok'}"></div>
          <div class="pname">${inst.instance}
            <span class="badge">${(inst.context_window / 1000).toFixed(0)}k ctx</span>
          </div>
          <div class="pmeta">
            ${inst.success} ok / ${inst.failure} failed
            ${inst.in_cooldown ? ` · cooling down ${inst.cooldown_seconds_remaining}s` : ''}
          </div>
        </div>
      `).join('') || '<div class="status-line">No instances in this tier</div>'}
    </div>
  `).join('') : '<div class="status-line">No tier data</div>';
}

// Actions
async function doAction(path, label) {
  const el = $('action-status');
  el.textContent = 'Running…';
  el.style.color = '#eab308';
  try {
    const r = await authFetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' } });
    const data = await r.json();
    if (r.ok && data.ok) {
      el.textContent = `${label}: ${JSON.stringify(data.results || data)}`;
      el.style.color = '#22c55e';
      loadStatus();
      loadProviders();
      loadModels();
    } else {
      throw new Error(data.error || 'failed');
    }
  } catch (e) {
    el.textContent = `${label} failed: ${e.message}`;
    el.style.color = '#ef4444';
  }
}

$('refresh-all').addEventListener('click', () => doAction('/api/refresh', 'Refresh'));

// Providers
async function loadProviders() {
  const r = await fetch('/api/providers');
  const data = await r.json();
  const el = $('provider-list');
  if (!data.providers.length) {
    el.innerHTML = '<div class="status-line">No providers configured</div>';
    return;
  }
  el.innerHTML = data.providers.map(p => `
    <div class="card provider-card">
      <div class="provider-header">
        <div>
          <div class="pname">${p.name}</div>
          <div class="pmeta">${p.base_url || p.upstream_url}</div>
        </div>
        <div class="badge ${p.auto_refresh ? 'ok' : 'manual'}">${p.auto_refresh ? 'auto' : 'manual'}</div>
      </div>
      <div class="provider-meta">API key: ${p.api_key || 'empty'}</div>
      <div class="provider-meta">${p.model_count} models</div>
      <details>
        <summary>Models</summary>
        <div class="model-grid">
          ${p.models.map(m => `<div class="model-card"><div class="mid">${m.id}</div></div>`).join('')}
        </div>
      </details>
    </div>
  `).join('');
}

$('provider-add').addEventListener('click', async () => {
  const name = prompt('Provider name:');
  if (!name) return;
  const base_url = prompt('Base URL:');
  if (!base_url) return;
  const api_key = prompt('API key (leave empty if none):') || '';
  const models_raw = prompt('Comma-separated model IDs (leave empty to fetch later):') || '';
  const models = models_raw.split(',').map(s => s.trim()).filter(Boolean);
  const body = { name, base_url, api_key, models, auto_refresh: !!api_key };
  const r = await authFetch('/api/providers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (r.ok) {
    alert(`Added provider ${name} with ${data.models} models`);
    loadProviders();
  } else {
    alert('Failed: ' + (data.error || 'unknown'));
  }
});

// Models
async function loadModels() {
  const r = await fetch('/api/models');
  const data = await r.json();
  const el = $('models');
  const cards = [];
  for (const [provider, models] of Object.entries(data)) {
    cards.push(`<h3 style="color:#38bdf8;margin:1rem 0 .5rem">${provider}</h3>`);
    cards.push('<div class="model-grid">');
    for (const m of models) {
      cards.push(`
        <div class="model-card">
          <div class="mid">${m.id}</div>
          <div class="mctx">
            ctx=${m.context_window?.toLocaleString?.() ?? '?'}
            ${m.reasoning ? '<span class="badge reasoning">reasoning</span>' : ''}
          </div>
        </div>
      `);
    }
    cards.push('</div>');
  }
  el.innerHTML = cards.join('') || '<div class="status-line">No models</div>';
}

// Config
async function loadConfig() {
  const r = await fetch('/api/config');
  const data = await r.json();
  $('config-editor').value = data.yaml;
  maskApiKeys();
  renderEffectiveConfig(data.effective);
}

// Show the effective config (values including defaults, e.g.
// refresh_interval_hours=12 when the raw file doesn't set it) as a
// read-only summary above the editor.
function renderEffectiveConfig(eff) {
  const el = $('effective-config');
  if (!eff || Object.keys(eff).length === 0) {
    el.innerHTML = '';
    return;
  }
  const rows = [
    ['refresh_interval_hours', eff.refresh_interval_hours + ' h'],
    ['upstream_timeout', eff.upstream_timeout + ' s'],
    ['tier_cascade', eff.tier_cascade ? 'on' : 'off'],
    ['proxy', eff.proxy_host + ':' + eff.proxy_port],
    ['ui', eff.ui_host + ':' + eff.ui_port],
    ['registry', eff.registry_path],
    ['registry_git_history', eff.registry_git_history ? 'on' : 'off'],
  ];
  el.innerHTML =
    '<div class="config-note"><strong>Effective config (incl. defaults):</strong></div>' +
    rows.map(([k, v]) =>
      '<div class="effective-row"><span class="eff-key">' + k + '</span>' +
      '<span class="eff-val">' + v + '</span></div>'
    ).join('');
}

function maskApiKeys() {
  const el = $('config-editor');
  el.value = el.value.replace(/(api_key:\s*)([^\n]+)/g, (_, p1, p2) => {
    const v = p2.trim().replace(/['"]/g, '');
    if (!v || v.length <= 8) return _;
    return p1 + '****' + v.slice(-4);
  });
}

$('config-save').addEventListener('click', async () => {
  const yaml = $('config-editor').value;
  const r = await authFetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ yaml }),
  });
  const data = await r.json();
  const el = $('config-status');
  if (r.ok) {
    el.textContent = '✔ Saved at ' + new Date().toLocaleTimeString();
    el.style.color = '#22c55e';
  } else {
    el.textContent = '✘ ' + (data.error || 'Failed');
    el.style.color = '#ef4444';
  }
});

$('config-reload').addEventListener('click', () => {
  loadConfig();
  $('config-status').textContent = '';
});

// Init
loadStatus();
setInterval(loadStatus, 30000);
