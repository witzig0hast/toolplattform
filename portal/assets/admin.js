'use strict';
const $ = id => document.getElementById(id);
const dlg = $('dlg'), form = $('form');
let editing = null; // Slug beim Bearbeiten, sonst null

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

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

async function load() {
  const list = $('list');
  $('error').hidden = true;
  try {
    const tools = await api('GET', '/api/admin/tools');
    list.replaceChildren();
    if (!tools.length) list.append(el('p', 'muted', 'Noch keine Tools angelegt.'));
    for (const t of tools) {
      const item = el('div', 'item');
      item.append(el('span', 'icon', t.icon || '🔧'));
      const info = el('div', 'info');
      const title = el('div', 'title');
      const link = el('a', '', t.name);
      link.href = '/' + encodeURIComponent(t.slug) + '/';
      title.append(link, el('span', 'chip', '/' + t.slug));
      const meta = el('div', 'meta', 'Gruppe: ' + t.group + (t.description ? ' · ' + t.description : ''));
      info.append(title, meta);
      const btns = el('div', 'btns');
      const edit = el('button', 'btn small', 'Bearbeiten');
      edit.type = 'button';
      edit.onclick = () => openDialog(t);
      const del = el('button', 'btn small danger', 'Löschen');
      del.type = 'button';
      del.onclick = () => remove(t);
      btns.append(edit, del);
      item.append(info, btns);
      list.append(item);
    }
  } catch (e) {
    $('error').textContent = e.message;
    $('error').hidden = false;
  }
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
  $('dlg-error').hidden = true;
  dlg.showModal();
}

async function remove(t) {
  if (!confirm(`„${t.name}“ aus der Liste entfernen?\nDie Dateien in sites/${t.slug}/ bleiben auf dem Server liegen, sind aber nicht mehr erreichbar.`)) return;
  try { await api('DELETE', '/api/admin/tools/' + encodeURIComponent(t.slug)); load(); }
  catch (e) { $('error').textContent = e.message; $('error').hidden = false; }
}

// Gruppenname beim Anlegen vorschlagen
$('f-slug').addEventListener('input', () => {
  if (!editing && !$('f-group').dataset.touched) $('f-group').value = $('f-slug').value ? 'tool-' + $('f-slug').value : '';
});
$('f-group').addEventListener('input', () => { $('f-group').dataset.touched = '1'; });

$('new').onclick = () => { delete $('f-group').dataset.touched; openDialog(null); };
$('cancel').onclick = () => dlg.close();

form.addEventListener('submit', async ev => {
  ev.preventDefault();
  const body = {
    name: $('f-name').value, description: $('f-desc').value,
    icon: $('f-icon').value, group: $('f-group').value,
  };
  try {
    if (editing) await api('PUT', '/api/admin/tools/' + encodeURIComponent(editing), body);
    else await api('POST', '/api/admin/tools', { slug: $('f-slug').value, ...body });
    dlg.close();
    load();
  } catch (e) {
    $('dlg-error').textContent = e.message;
    $('dlg-error').hidden = false;
  }
});

load();
