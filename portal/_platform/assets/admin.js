'use strict';
const $ = id => document.getElementById(id);
const dlg = $('dlg'), dlgDel = $('dlg-del'), form = $('form');
let editing = null; // Pfad beim Bearbeiten, sonst null

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
      info.append(name, el('span', '', t.description || 'Keine Beschreibung'));
      cell.append(tile(t), info);

      const path = el('div', 'pathcell');
      const link = el('a', 'mono', '/' + t.slug + '/');
      link.href = '/' + encodeURIComponent(t.slug) + '/';
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
      act.append(edit, del);

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

function confirmRemove(t) {
  $('del-text').textContent = `„${t.name}“ wird aus der Liste entfernt und ist danach für niemanden mehr erreichbar. Die Dateien in sites/${t.slug}/ bleiben auf dem Server erhalten.`;
  dlgDel.returnValue = '';
  dlgDel.onclose = async () => {
    if (dlgDel.returnValue !== 'ok') return;
    try { await api('DELETE', '/_platform/api/admin/tools/' + encodeURIComponent(t.slug)); toast('Tool entfernt'); load(); }
    catch (e) { showError(e.message); }
  };
  dlgDel.showModal();
}

function syncType() {
  const proxy = document.querySelector('input[name=type]:checked').value === 'proxy';
  $('proxy-fields').hidden = !proxy;
  $('f-upstream').required = proxy;
}
document.querySelectorAll('input[name=type]').forEach(r => r.addEventListener('change', syncType));

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
  const body = { name: $('f-name').value, description: $('f-desc').value, icon: $('f-icon').value, group: $('f-group').value, type };
  if (type === 'proxy') { body.upstream = $('f-upstream').value.trim(); body.strip_prefix = $('f-strip').checked; }
  try {
    if (editing) await api('PUT', '/_platform/api/admin/tools/' + encodeURIComponent(editing), body);
    else await api('POST', '/_platform/api/admin/tools', { slug: $('f-slug').value, ...body });
    dlg.close();
    toast(editing ? 'Änderungen gespeichert' : 'Tool angelegt');
    load();
  } catch (e) {
    $('dlg-error').textContent = e.message;
    $('dlg-error').hidden = false;
  } finally { save.disabled = false; }
});

load();
