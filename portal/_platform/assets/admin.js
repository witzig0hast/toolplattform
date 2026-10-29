'use strict';
const $ = id => document.getElementById(id);
const dlg = $('dlg'), dlgDel = $('dlg-del'), form = $('form');
let editing = null; // Pfad beim Bearbeiten, sonst null
let toolsCache = [];
let toolsDomain = '';

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
    const tools = await api('GET', '/_platform/api/admin/tools');
    toolsCache = tools;
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
      if (t.type === 'proxy') name.append(el('span', 'chip-proxy', 'Proxy'));
      if (t.enabled === false) name.append(el('span', 'chip-maint', 'Wartung'));
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
    category: $('f-cat').value, enabled: !$('f-maint').checked, type, access,
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
    const body = { label: $('s-label').value, max_uses: maxUses, ...expiry };
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

async function loadDomain() {
  try {
    toolsDomain = (await api('GET', '/_platform/api/me')).toolsDomain || '';
  } catch (e) { toolsDomain = ''; }
  const radio = document.querySelector('input[name=access][value=subdomain]');
  radio.disabled = !toolsDomain;
}

loadDomain();
load();
loadShares();
loadAudit();
