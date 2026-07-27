function $(id){ return document.getElementById(id); }

// Tabs
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');
    btn.classList.add('active');
    $('tab-' + btn.dataset.tab).style.display = 'block';
    if (btn.dataset.tab === 'config') loadConfig();
    if (btn.dataset.tab === 'models') loadModels();
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

  $('proxy').innerHTML = Object.entries(data.proxy).map(([k, v]) =>
    `<div class="status-line">${k}: http://${v}</div>`
  ).join('');
}

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
  const r = await fetch('/api/config', {
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
