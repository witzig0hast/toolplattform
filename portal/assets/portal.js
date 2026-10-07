'use strict';
(async () => {
  const $ = id => document.getElementById(id);
  const grid = $('grid'), empty = $('empty');
  let cards = [];

  function showEmpty(title, text) {
    empty.replaceChildren(el('strong', '', title), document.createTextNode(text));
    empty.hidden = false;
  }

  try {
    const get = u => fetch(u).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
    const [me, tools] = await Promise.all([get('/api/me'), get('/api/tools')]);

    $('who').textContent = me.name;
    $('avatar').textContent = initials(me.name);
    $('user').hidden = false;
    if (me.isAdmin) $('admin-link').hidden = false;
    $('hello').textContent = 'Hallo, ' + me.name.split(' ')[0];
    $('sub').textContent = tools.length === 1 ? '1 Tool für dich verfügbar.'
      : tools.length ? tools.length + ' Tools für dich verfügbar.' : '';

    if (!tools.length) {
      showEmpty('Noch keine Tools freigeschaltet', 'Sobald dir ein Administrator Zugriff gibt, erscheinen sie hier.');
      return;
    }

    for (const t of tools) {
      const a = el('a', 'card');
      a.href = '/' + encodeURIComponent(t.slug) + '/';
      const body = el('span', 'card-body');
      body.append(el('span', 'name', t.name));
      if (t.description) body.append(el('span', 'desc', t.description));
      body.append(el('span', 'path', '/' + t.slug));
      const arrow = svgIcon('M7 17 17 7M8 7h9v9', 18);
      arrow.classList.add('arrow');
      a.append(tile(t), body, arrow);
      a.dataset.q = (t.name + ' ' + t.description + ' ' + t.slug).toLowerCase();
      grid.append(a);
      cards.push(a);
    }
    if (tools.length > 6) $('search-box').hidden = false;
    $('search').addEventListener('input', ev => {
      const q = ev.target.value.trim().toLowerCase();
      let shown = 0;
      for (const c of cards) { const ok = !q || c.dataset.q.includes(q); c.hidden = !ok; if (ok) shown++; }
      if (shown) empty.hidden = true; else showEmpty('Keine Treffer', 'Zu „' + ev.target.value.trim() + '“ gibt es kein Tool.');
    });
  } catch (e) {
    $('hello').textContent = 'Willkommen';
    showEmpty('Tools konnten nicht geladen werden', 'Bitte lade die Seite neu.');
  }
})();
