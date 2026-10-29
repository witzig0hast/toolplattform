'use strict';
const $ = id => document.getElementById(id);
const dlg = $('dlg'), dlgDel = $('dlg-del'), form = $('form');
let editing = null; // Pfad beim Bearbeiten, sonst null
let toolsCache = [];
let toolsDomain = '';
let healthCache = {};

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || 'Fehler ' + res.status);
  return data;
}

let toastTimer;
function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 2800);
}
function showError(msg) { $('error').textContent = msg; $('error').hidden = false; }

async function load() {
  const list = $('list');
  $('error').hidden = true;
  try {
    const [tools, health] = await Promise.all([
      api('GET', '/_platform/api/admin/tools'),
      api('GET', '/_platform/api/admin/health').catch(() => ({})),
    ]);
    toolsCache = tools;
    healthCache = health.tools || {};
    const cats = [...new Set(tools.map(t => t.category).filter(Boolean))].sort();
    $('cat-list').replaceChildren(...cats.map(c => { const o = el('option'); o.value = c; return o; }));
    list.replaceChildren();
    if (!tools.length) {
      list.append(el('div', 'table-empty', 'Noch keine Tools angelegt. Mit „Neues Tool“ geht es los.'));
      return;
    }
    for (const t of tools) {
      const row = el('div', 'trow');

      const cell = el('div', 'tcell-tool');
      const info = el('div', 't');
      const name = el('strong', '', t.name);
      const h = healthCache[t.slug];
      if (h) {
        const dot = el('span', 'dot ' + h.state);
        dot.title = HEALTH_LABEL[h.state] || h.state;
        name.prepend(dot);
      }
      if (t.type === 'proxy') name.append(el('span', 'chip-proxy', 'Proxy'));
      if (t.enabled === false) name.append(el('span', 'chip-maint', 'Wartung'));
      if (t.require_mfa) name.append(el('span', 'chip-proxy', '2FA'));
      info.append(name, el('span', '', [t.category, t.description || 'Keine Beschreibung'].filter(Boolean).join(' · ')));
      cell.append(tile(t), info);

      const path = el('div', 'pathcell');
      const link = el('a', 'mono', t.access === 'subdomain' ? t.url.replace(/^https:\/\//, '') : '/' + t.slug + '/');
      link.href = t.url;
      link.title = t.url;
      path.append(link);
      if (t.type === 'proxy') {
        const up = el('span', 'upstream', '→ ' + t.upstream);
        up.title = t.upstream;
        path.append(up);
      }
      const g = el('div', 'gcell');
      g.append(el('span', 'pill', t.group));

      const act = el('div', 'tactions');
      const edit = el('button', 'btn sm', 'Bearbeiten');
      edit.type = 'button';
      edit.onclick = () => openDialog(t);
      const del = el('button', 'btn sm danger', 'Entfernen');
      del.type = 'button';
      del.onclick = () => confirmRemove(t);
      const share = el('button', 'btn sm', 'Teilen');
      share.type = 'button';
      share.onclick = () => openShare(t.slug);
      if (t.type !== 'proxy') {
        const filesBtn = el('button', 'btn sm', 'Dateien');
        filesBtn.type = 'button';
        filesBtn.onclick = () => openFiles(t);
        act.append(filesBtn);
      }
      act.append(share, edit, del);

      row.append(cell, path, g, act);
      list.append(row);
    }
  } catch (e) { showError(e.message); }
}

function openDialog(t) {
  editing = t ? t.slug : null;
  $('dlg-title').textContent = t ? 'Tool bearbeiten' : 'Neues Tool';
  $('f-slug').value = t ? t.slug : '';
  $('f-slug').disabled = !!t;
  $('f-name').value = t ? t.name : '';
  $('f-desc').value = t ? t.description : '';
  $('f-icon').value = t ? t.icon : '';
  $('f-group').value = t ? t.group : '';
  $('f-cat').value = t ? (t.category || '') : '';
  $('f-maint').checked = !!t && t.enabled === false;
  $('f-mfa').checked = !!t && !!t.require_mfa;
  $('f-back').checked = !t || t.back_button !== false;
  const access = t && t.access === 'subdomain' ? 'subdomain' : 'path';
  document.querySelector(`input[name=access][value=${access}]`).checked = true;
  const kind = t && t.type === 'proxy' ? 'proxy' : 'static';
  document.querySelector(`input[name=type][value=${kind}]`).checked = true;
  $('f-upstream').value = t && t.upstream ? t.upstream : '';
  $('f-strip').checked = t && t.type === 'proxy' ? t.strip_prefix !== false : true;
  syncType();
  if (!t) delete $('f-group').dataset.touched;
  $('dlg-error').hidden = true;
  dlg.showModal();
  (t ? $('f-name') : $('f-slug')).focus();
}

function confirmDialog(title, text, okLabel, onOk) {
  $('del-title').textContent = title;
  $('del-text').textContent = text;
  $('del-ok').textContent = okLabel;
  dlgDel.returnValue = '';
  dlgDel.onclose = () => { if (dlgDel.returnValue === 'ok') onOk(); };
  dlgDel.showModal();
}

function confirmRemove(t) {
  confirmDialog('Tool entfernen?',
    `„${t.name}“ wird aus der Liste entfernt und ist danach für niemanden mehr erreichbar. Die Dateien in sites/${t.slug}/ bleiben auf dem Server erhalten. Freigabe-Links zu diesem Tool funktionieren dann auch nicht mehr.`,
    'Entfernen', async () => {
      try { await api('DELETE', '/_platform/api/admin/tools/' + encodeURIComponent(t.slug)); toast('Tool entfernt'); load(); loadShares(); }
      catch (e) { showError(e.message); }
    });
}

function syncType() {
  const proxy = document.querySelector('input[name=type]:checked').value === 'proxy';
  const sub = document.querySelector('input[name=access]:checked').value === 'subdomain';
  $('proxy-fields').hidden = !proxy;
  $('f-upstream').required = proxy;
  // "Pfad-Präfix entfernen" ist nur beim Aufruf über einen Pfad relevant
  $('strip-row').hidden = sub;
  const slug = $('f-slug').value || '<pfad>';
  $('acc-path-hint').textContent = (location.host || '') + '/' + slug + '/';
  $('acc-sub-hint').textContent = toolsDomain ? slug + '.' + toolsDomain : 'TOOLS_DOMAIN in .env setzen';
}
document.querySelectorAll('input[name=type], input[name=access]').forEach(r => r.addEventListener('change', syncType));
$('f-slug').addEventListener('input', syncType);

// Gruppenname beim Anlegen automatisch vorschlagen
$('f-slug').addEventListener('input', () => {
  if (!editing && !$('f-group').dataset.touched) $('f-group').value = $('f-slug').value ? 'tool-' + $('f-slug').value : '';
});
$('f-group').addEventListener('input', () => { $('f-group').dataset.touched = '1'; });

$('new').onclick = () => openDialog(null);
$('cancel').onclick = () => dlg.close();

form.addEventListener('submit', async ev => {
  ev.preventDefault();
  const save = $('save');
  save.disabled = true;
  const type = document.querySelector('input[name=type]:checked').value;
  const access = document.querySelector('input[name=access]:checked').value;
  const body = {
    name: $('f-name').value, description: $('f-desc').value, icon: $('f-icon').value, group: $('f-group').value,
    category: $('f-cat').value, enabled: !$('f-maint').checked, require_mfa: $('f-mfa').checked, back_button: $('f-back').checked, type, access,
  };
  if (type === 'proxy') { body.upstream = $('f-upstream').value.trim(); body.strip_prefix = $('f-strip').checked; }
  try {
    if (editing) await api('PUT', '/_platform/api/admin/tools/' + encodeURIComponent(editing), body);
    else await api('POST', '/_platform/api/admin/tools', { slug: $('f-slug').value, ...body });
    dlg.close();
    toast(editing ? 'Änderungen gespeichert' : 'Tool angelegt');
    load();
    loadAudit();
  } catch (e) {
    $('dlg-error').textContent = e.message;
    $('dlg-error').hidden = false;
  } finally { save.disabled = false; }
});

// ---------- Freigabe-Links ----------
const fmt = new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
const when = secs => fmt.format(new Date(secs * 1000));

function remaining(secs) {
  const d = secs - Date.now() / 1000;
  if (d <= 0) return 'abgelaufen';
  if (d < 3600) return 'noch ' + Math.ceil(d / 60) + ' Min.';
  if (d < 86400) return 'noch ' + Math.floor(d / 3600) + ' Std.';
  return 'noch ' + Math.floor(d / 86400) + ' Tage';
}

async function loadShares() {
  const list = $('share-list');
  $('share-error').hidden = true;
  try {
    const shares = await api('GET', '/_platform/api/admin/shares');
    list.replaceChildren();
    if (!shares.length) {
      list.append(el('div', 'table-empty', 'Keine Freigabe-Links. Mit „Teilen“ bei einem Tool oder „Freigabe-Link“ erstellst du einen.'));
      return;
    }
    for (const sh of shares) {
      const row = el('div', 'srow');
      const a = el('div');
      a.append(el('strong', '', sh.toolName), el('div', 'sub', '/' + sh.tool + '/ · von ' + (sh.createdBy || '?')));
      const badges = el('div', 'badges');
      if (sh.hasPin) badges.append(el('span', 'badge', 'PIN'));
      if (sh.ips && sh.ips.length) { const b = el('span', 'badge', 'IP'); b.title = sh.ips.join(', '); badges.append(b); }
      if (sh.schedule) { const b = el('span', 'badge', 'Zeitfenster'); b.title = scheduleText(sh.schedule); badges.append(b); }
      if (sh.locked) badges.append(el('span', 'badge warn', 'gesperrt (PIN)'));
      if (badges.childElementCount) a.append(badges);
      const b = el('div', '', sh.label || '–');
      const c = el('div');
      const used = sh.maxUses && sh.uses >= sh.maxUses;
      const state = sh.active ? remaining(sh.expires) : (used && sh.expires > Date.now() / 1000 ? 'aufgebraucht' : 'abgelaufen');
      c.append(el('span', 'status' + (sh.active ? '' : ' off'), state), el('div', 'sub', when(sh.expires)));
      const u = el('div');
      u.append(el('span', '', sh.uses + '× geöffnet' + (sh.maxUses ? ' (max. ' + sh.maxUses + ')' : '')),
               el('div', 'sub', sh.lastUsed ? 'zuletzt ' + when(sh.lastUsed) : 'noch nie geöffnet'));
      const d = el('div', 'tactions');
      const chg = el('button', 'btn sm', 'Ändern');
      chg.type = 'button';
      chg.onclick = () => openShare(sh.tool, sh);
      const rev = el('button', 'btn sm danger', 'Widerrufen');
      rev.type = 'button';
      rev.onclick = () => confirmDialog('Freigabe-Link widerrufen?',
        'Der Link funktioniert danach sofort nicht mehr.', 'Widerrufen', async () => {
          try { await api('DELETE', '/_platform/api/admin/shares/' + encodeURIComponent(sh.id)); toast('Link widerrufen'); loadShares(); loadAudit(); }
          catch (e) { $('share-error').textContent = e.message; $('share-error').hidden = false; }
        });
      if (sh.locked) {
        const un = el('button', 'btn sm', 'Entsperren');
        un.type = 'button';
        un.onclick = async () => {
          try { await api('PUT', '/_platform/api/admin/shares/' + encodeURIComponent(sh.id), { unlock: true }); toast('Link entsperrt'); loadShares(); }
          catch (e) { $('share-error').textContent = e.message; $('share-error').hidden = false; }
        };
        d.append(un);
      }
      d.append(chg, rev);
      row.append(a, b, c, u, d);
      list.append(row);
    }
  } catch (e) { $('share-error').textContent = e.message; $('share-error').hidden = false; }
}

// Gewählte Gültigkeit als Sekunden-Dauer oder Ablaufzeitpunkt ermitteln (null = unvollständig)
function shareExpiry() {
  const mode = $('s-dur').value;
  if (mode === 'until') {
    const v = $('s-date').value;
    if (!v) return null;
    return { expires_at: new Date(v).getTime() / 1000 };
  }
  if (mode === 'custom') {
    const n = parseInt($('s-num').value, 10);
    if (!n || n < 1) return null;
    return { expires_in: n * parseInt($('s-unit').value, 10) };
  }
  return { expires_in: parseInt(mode, 10) };
}

function syncShareDuration() {
  const mode = $('s-dur').value;
  $('s-custom').hidden = mode !== 'custom';
  $('s-until').hidden = mode !== 'until';
  const e = shareExpiry();
  const secs = e ? (e.expires_at ? e.expires_at : Date.now() / 1000 + e.expires_in) : null;
  $('s-preview').textContent = secs ? 'Läuft ab am ' + when(secs) + '.' : '';
}
for (const id of ['s-dur', 's-num', 's-unit', 's-date']) {
  $(id).addEventListener('input', syncShareDuration);
  $(id).addEventListener('change', syncShareDuration);
}

let editingShare = null; // beim Ändern: der bestehende Link

function openShare(slug, share) {
  editingShare = share || null;
  $('share-title').textContent = share ? 'Freigabe-Link ändern' : 'Freigabe-Link erstellen';
  $('share-save').textContent = share ? 'Speichern' : 'Link erstellen';
  $('s-tool-row').hidden = !!share;
  const sel = $('s-tool');
  sel.replaceChildren();
  for (const t of toolsCache) {
    const o = el('option', '', t.name + '  (/' + t.slug + '/)');
    o.value = t.slug;
    sel.append(o);
  }
  if (!toolsCache.length) { showError('Lege zuerst ein Tool an.'); return; }
  if (slug) sel.value = slug;
  $('s-label').value = share ? share.label : '';
  $('s-max').value = share && share.maxUses ? share.maxUses : '';
  $('s-pin').value = '';
  $('s-pin').placeholder = share && share.hasPin ? 'leer = PIN unverändert lassen' : 'leer = ohne PIN';
  $('s-pin-remove').checked = false;
  $('s-pin-remove-row').hidden = !(share && share.hasPin);
  $('s-ips').value = share && share.ips ? share.ips.join(', ') : '';
  const sched = share && share.schedule;
  $('s-sched-on').checked = !!sched;
  $('s-sched').hidden = !sched;
  for (const c of document.querySelectorAll('#s-sched .days input')) {
    c.checked = sched ? sched.days.includes(+c.value) : +c.value < 5;
  }
  $('s-from').value = sched ? sched.from : '08:00';
  $('s-to').value = sched ? sched.to : '18:00';
  $('s-dur').value = '86400';
  const soon = new Date(Date.now() + 86400000 - new Date().getTimezoneOffset() * 60000);
  $('s-date').value = soon.toISOString().slice(0, 16);
  $('share-dlg-error').hidden = true;
  syncShareDuration();
  $('dlg-share').showModal();
}

$('new-share').onclick = () => openShare(null);
$('share-cancel').onclick = () => $('dlg-share').close();

$('form-share').addEventListener('submit', async ev => {
  ev.preventDefault();
  const btn = $('share-save');
  const expiry = shareExpiry();
  if (!expiry) {
    $('share-dlg-error').textContent = 'Bitte eine gültige Dauer oder ein Datum angeben.';
    $('share-dlg-error').hidden = false;
    return;
  }
  const max = $('s-max').value.trim();
  const maxUses = max ? parseInt(max, 10) : null;
  if (max && !(maxUses >= 1)) {
    $('share-dlg-error').textContent = 'Maximale Öffnungen: leer lassen oder eine Zahl ab 1.';
    $('share-dlg-error').hidden = false;
    return;
  }
  btn.disabled = true;
  try {
    const body = { label: $('s-label').value, max_uses: maxUses, ips: $('s-ips').value, ...expiry };
    if ($('s-pin').value) body.pin = $('s-pin').value;
    else if (editingShare && $('s-pin-remove').checked) body.remove_pin = true;
    if ($('s-sched-on').checked) {
      body.schedule = {
        days: [...document.querySelectorAll('#s-sched .days input:checked')].map(c => +c.value),
        from: $('s-from').value, to: $('s-to').value,
      };
    } else body.schedule = null;
    if (editingShare) {
      await api('PUT', '/_platform/api/admin/shares/' + encodeURIComponent(editingShare.id), body);
      $('dlg-share').close();
      toast('Freigabe-Link geändert');
    } else {
      const res = await api('POST', '/_platform/api/admin/shares', { tool: $('s-tool').value, ...body });
      $('dlg-share').close();
      $('link-url').value = location.origin + res.path;
      $('link-expiry').textContent = 'Gültig bis ' + when(res.expires) + '.' + (maxUses ? ' Höchstens ' + maxUses + '× zu öffnen.' : '');
      $('link-session').hidden = !res.sessionMode;
      $('dlg-link').showModal();
      $('link-url').select();
    }
    loadShares();
    loadAudit();
  } catch (e) {
    $('share-dlg-error').textContent = e.message;
    $('share-dlg-error').hidden = false;
  } finally { btn.disabled = false; }
});

$('link-copy').onclick = async () => {
  const input = $('link-url');
  try { await navigator.clipboard.writeText(input.value); toast('Link kopiert'); }
  catch (e) { input.select(); document.execCommand('copy'); toast('Link kopiert'); }
};

// ---------- Protokoll ----------
const ACTIONS = {
  'tool.create': e => ['Tool angelegt', '/' + e.tool + '/ · Gruppe ' + e.group + (e.upstream ? ' · → ' + e.upstream : '')],
  'tool.update': e => ['Tool geändert', '/' + e.tool + '/ · ' + (e.changed || []).join(', ')],
  'tool.delete': e => ['Tool entfernt', '/' + e.tool + '/'],
  'share.create': e => ['Freigabe-Link erstellt', e.tool + ' · bis ' + when(e.expires) + (e.max_uses ? ' · max. ' + e.max_uses + '×' : '')],
  'share.update': e => ['Freigabe-Link geändert', 'Link ' + e.share + ' · bis ' + when(e.expires)],
  'share.revoke': e => ['Freigabe-Link widerrufen', e.tool + ' · Link ' + e.share],
  'share.open': e => ['Freigabe-Link geöffnet', e.tool + ' · ' + (e.ip || 'IP unbekannt') + ' · ' + e.uses + '. Öffnung'],
  'share.blocked': e => ['Freigabe-Link abgewiesen', e.tool + ' · ' + (e.ip || 'IP unbekannt') + ' · Limit erreicht'],
  'config.export': () => ['Konfiguration exportiert', ''],
  'config.import': e => ['Konfiguration importiert', (e.tools || []).length + ' Tool(s) · ' + e.added + ' neu'],
  'share.pin_failed': e => ['Falsche PIN eingegeben', e.tool + ' · ' + (e.ip || 'IP unbekannt')],
  'files.upload': e => [e.unzip ? 'ZIP entpackt' : 'Datei hochgeladen', '/' + e.tool + '/' + e.path + (e.unzip ? ' · ' + e.files + ' Datei(en)' : '')],
  'files.delete': e => ['Datei gelöscht', '/' + e.tool + '/' + e.path],
  'notify.test': () => ['Test-Benachrichtigung angestoßen', ''],
};

async function loadAudit() {
  const list = $('audit-list');
  $('audit-error').hidden = true;
  try {
    const entries = await api('GET', '/_platform/api/admin/audit?limit=150');
    list.replaceChildren();
    if (!entries.length) { list.append(el('div', 'table-empty', 'Noch keine Einträge.')); return; }
    for (const e of entries) {
      const [title, detail] = (ACTIONS[e.action] || (() => [e.action, '']))(e);
      const row = el('div', 'arow');
      const what = el('div', 'what');
      what.append(el('strong', '', title));
      if (detail) what.append(el('div', 'sub', detail));
      row.append(el('span', 'when', when(e.ts)), el('span', '', e.actor || 'Gast'), what);
      list.append(row);
    }
  } catch (e) { $('audit-error').textContent = e.message; $('audit-error').hidden = false; }
}
$('audit-reload').onclick = () => { loadAudit(); loadShares(); };

$('export').onclick = async () => {
  try {
    const data = await api('GET', '/_platform/api/admin/export');
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = el('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'tools-export-' + new Date().toISOString().slice(0, 10) + '.json';
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    loadAudit();
  } catch (e) { showError(e.message); }
};

$('s-sched-on').addEventListener('change', () => { $('s-sched').hidden = !$('s-sched-on').checked; });

const DAY_NAMES = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];
function scheduleText(s) {
  return s.days.map(d => DAY_NAMES[d]).join(', ') + ' · ' + s.from + '–' + s.to + ' Uhr';
}

// ---------- Nutzung ----------
async function loadUsage() {
  const list = $('usage-list');
  $('usage-error').hidden = true;
  try {
    const days = $('usage-days').value;
    const data = await api('GET', '/_platform/api/admin/stats?days=' + days);
    list.replaceChildren();
    const names = Object.fromEntries(toolsCache.map(t => [t.slug, t.name]));
    const rows = Object.entries(data.tools).sort((a, b) => b[1].total - a[1].total);
    if (!rows.length) { list.append(el('div', 'table-empty', 'Noch keine Aufrufe im gewählten Zeitraum.')); return; }
    for (const [slug, st] of rows) {
      const row = el('div', 'urow');
      const a = el('div');
      a.append(el('strong', '', names[slug] || slug), el('div', 'sub', '/' + slug + '/'));
      const spark = el('div', 'spark');
      spark.title = 'Aufrufe pro Tag';
      const max = Math.max(1, ...st.series);
      for (const v of st.series) {
        const bar = el('span', v ? '' : 'zero');
        bar.style.height = Math.max(8, Math.round(v / max * 100)) + '%';
        bar.title = v + ' Aufrufe';
        spark.append(bar);
      }
      const total = el('div');
      total.append(el('span', 'num', String(st.total)), el('div', 'sub', st.last7 + ' in 7 Tagen'));
      const users = el('div', 'num', String(st.uniqueUsers));
      const top = el('div', 'sub', st.topUsers.map(u => u.user + ' (' + u.count + ')').join(', '));
      row.append(a, spark, total, users, top);
      list.append(row);
    }
  } catch (e) { $('usage-error').textContent = e.message; $('usage-error').hidden = false; }
}
$('usage-days').addEventListener('change', loadUsage);

// ---------- Überwachung ----------
const HEALTH_LABEL = { up: 'erreichbar', down: 'nicht erreichbar', maintenance: 'Wartung', unknown: 'wird geprüft' };

function ago(secs) {
  const d = Date.now() / 1000 - secs;
  if (d < 90) return 'gerade eben';
  if (d < 3600) return 'seit ' + Math.round(d / 60) + ' Min.';
  if (d < 86400) return 'seit ' + Math.round(d / 3600) + ' Std.';
  return 'seit ' + Math.round(d / 86400) + ' Tagen';
}

async function loadMonitor() {
  const list = $('monitor-list');
  $('monitor-error').hidden = true;
  try {
    const data = await api('GET', '/_platform/api/admin/health');
    const tools = data.tools || {};
    list.replaceChildren();
    const names = Object.fromEntries(toolsCache.map(t => [t.slug, t]));
    const proxies = toolsCache.filter(t => t.type === 'proxy');
    if (!data.updated) {
      list.append(el('div', 'table-empty', 'Noch keine Daten. Läuft der Container tools-monitor? (docker compose ps)'));
    } else if (!proxies.length) {
      list.append(el('div', 'table-empty', 'Es gibt keine Weiterleitungs-Tools zum Überwachen.'));
    }
    for (const t of proxies) {
      const h = tools[t.slug];
      const row = el('div', 'mrow');
      const a = el('div');
      a.append(el('strong', '', t.name), el('div', 'sub', t.upstream));
      const st = el('div');
      st.append(el('span', 'dot ' + (h ? h.state : 'unknown')), document.createTextNode(HEALTH_LABEL[h ? h.state : 'unknown']));
      const since = el('div', 'sub', h ? ago(h.since) : '–');
      const resp = el('div', 'sub', h ? (h.detail || '') + (h.ms != null ? ' · ' + h.ms + ' ms' : '') : '–');
      row.append(a, st, since, resp);
      list.append(row);
    }
    const n = data.notify || {};
    const active = ['ntfy', 'telegram', 'webhook'].filter(k => n[k]);
    $('notify-targets').textContent = data.updated
      ? 'Zuletzt geprüft: ' + when(data.updated) + ' · alle ' + data.interval + ' s. Benachrichtigungen: '
        + (active.length ? active.join(', ') : 'keine eingerichtet (NOTIFY_… in der .env)') + '.'
      : '';
    return data;
  } catch (e) { $('monitor-error').textContent = e.message; $('monitor-error').hidden = false; }
}

$('notify-test').onclick = async () => {
  const btn = $('notify-test');
  btn.disabled = true;
  try {
    const before = ((await api('GET', '/_platform/api/admin/health')).test || {}).ts || 0;
    await api('POST', '/_platform/api/admin/notify-test', {});
    toast('Test wird gesendet …');
    for (let i = 0; i < 15; i++) {
      await new Promise(r => setTimeout(r, 1000));
      const t = (await api('GET', '/_platform/api/admin/health')).test;
      if (t && t.ts > before) {
        if (!t.results.length) toast('Keine Benachrichtigung eingerichtet (NOTIFY_… in der .env)');
        else toast(t.results.map(r => r.target + ': ' + (r.ok ? 'OK' : 'Fehler – ' + r.error)).join(' · '));
        return;
      }
    }
    toast('Keine Antwort vom Monitor. Läuft tools-monitor?');
  } catch (e) { $('monitor-error').textContent = e.message; $('monitor-error').hidden = false; }
  finally { btn.disabled = false; }
};

// ---------- Dateien ----------
let filesTool = null;
const fmtSize = n => n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB';

async function loadFiles() {
  const list = $('files-list');
  $('files-error').hidden = true;
  try {
    const data = await api('GET', '/_platform/api/admin/files/' + encodeURIComponent(filesTool.slug));
    list.replaceChildren();
    if (!data.files.length) { list.append(el('div', 'table-empty', 'Noch keine Dateien.')); return; }
    for (const f of data.files) {
      const row = el('div', 'frow');
      const link = el('a', 'mono', f.path);
      link.href = '/' + filesTool.slug + '/' + f.path.split('/').map(encodeURIComponent).join('/');
      link.target = '_blank';
      link.rel = 'noopener';
      const del = el('button', 'btn sm danger', 'Löschen');
      del.type = 'button';
      del.onclick = async () => {
        try {
          await api('DELETE', '/_platform/api/admin/files/' + encodeURIComponent(filesTool.slug) + '?path=' + encodeURIComponent(f.path));
          loadFiles();
        } catch (e) { $('files-error').textContent = e.message; $('files-error').hidden = false; }
      };
      row.append(link, el('span', 'size', fmtSize(f.size)), el('span', 'date', when(f.mtime)), del);
      list.append(row);
    }
    if (data.truncated) list.append(el('div', 'table-empty', 'Liste gekürzt (sehr viele Dateien).'));
  } catch (e) { $('files-error').textContent = e.message; $('files-error').hidden = false; }
}

function openFiles(t) {
  filesTool = t;
  $('files-title').textContent = 'Dateien von „' + t.name + '“';
  $('files-input').value = '';
  $('files-progress').textContent = '';
  $('dlg-files').showModal();
  loadFiles();
}

$('files-upload').onclick = async () => {
  const files = [...$('files-input').files];
  if (!files.length) { $('files-error').textContent = 'Bitte zuerst Dateien auswählen.'; $('files-error').hidden = false; return; }
  const dir = $('files-dir').value.trim().replace(/^\/+|\/+$/g, '');
  const btn = $('files-upload');
  btn.disabled = true;
  $('files-error').hidden = true;
  try {
    let done = 0;
    for (const f of files) {
      $('files-progress').textContent = 'Lade hoch: ' + f.name + ' (' + (++done) + ' von ' + files.length + ') …';
      const unzip = $('files-unzip').checked && /\.zip$/i.test(f.name);
      const path = (dir ? dir + '/' : '') + f.name;
      const res = await fetch('/_platform/api/admin/files/' + encodeURIComponent(filesTool.slug)
        + '?path=' + encodeURIComponent(path) + (unzip ? '&unzip=1' : ''), {
        method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: f,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(f.name + ': ' + (data.error || (res.status === 413 ? 'Datei zu groß' : 'Fehler ' + res.status)));
    }
    $('files-progress').textContent = files.length + ' Datei(en) hochgeladen.';
    $('files-input').value = '';
    loadFiles();
    loadAudit();
  } catch (e) {
    $('files-error').textContent = e.message;
    $('files-error').hidden = false;
    $('files-progress').textContent = '';
  } finally { btn.disabled = false; }
};

// ---------- Import ----------
$('import').onclick = () => $('import-file').click();
$('import-file').addEventListener('change', async () => {
  const file = $('import-file').files[0];
  $('import-file').value = '';
  if (!file) return;
  let data;
  try {
    data = JSON.parse(await file.text());
  } catch (e) { showError('Die Datei ist kein gültiges JSON.'); return; }
  const tools = data && data.tools;
  if (!tools || typeof tools !== 'object' || !Object.keys(tools).length) { showError('In der Datei sind keine Tools.'); return; }
  const existing = new Set(toolsCache.map(t => t.slug));
  const slugs = Object.keys(tools);
  const upd = slugs.filter(s => existing.has(s)).length;
  confirmDialog('Konfiguration importieren?',
    slugs.length + ' Tool(s) aus „' + file.name + '“: ' + (slugs.length - upd) + ' neu, ' + upd
      + ' vorhandene werden überschrieben. Tools, die nicht in der Datei stehen, bleiben erhalten.',
    'Importieren', async () => {
      try {
        const r = await api('POST', '/_platform/api/admin/import', { tools, mode: 'merge' });
        toast(r.imported + ' Tool(s) importiert');
        load(); loadAudit();
      } catch (e) { showError(e.message); }
    });
});

async function loadDomain() {
  try {
    toolsDomain = (await api('GET', '/_platform/api/me')).toolsDomain || '';
  } catch (e) { toolsDomain = ''; }
  const radio = document.querySelector('input[name=access][value=subdomain]');
  radio.disabled = !toolsDomain;
}

loadDomain();
load().then(() => { loadUsage(); loadMonitor(); });
loadShares();
loadAudit();
setInterval(() => { if (!document.hidden) loadMonitor(); }, 60000);
