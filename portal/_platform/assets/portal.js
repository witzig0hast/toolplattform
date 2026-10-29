'use strict';
(async () => {
  const $ = id => document.getElementById(id);
  const empty = $('empty');
  let cards = [];

  function showEmpty(title, text) {
    empty.replaceChildren(el('strong', '', title), document.createTextNode(text));
    empty.hidden = false;
  }

  try {
    const get = u => fetch(u).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
    const [me, tools] = await Promise.all([get('/_platform/api/me'), get('/_platform/api/tools')]);

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

    // Nach Kategorie gruppieren (ohne Kategorie zuletzt unter "Weitere")
    const byCat = new Map();
    for (const t of tools) {
      const c = t.category || '';
      if (!byCat.has(c)) byCat.set(c, []);
      byCat.get(c).push(t);
    }
    const cats = [...byCat.keys()].sort((a, b) => (a === '') - (b === '') || a.localeCompare(b, 'de'));
    const showHeads = cats.length > 1 || cats[0] !== '';
    for (const c of cats) {
      const section = el('section', 'group');
      if (showHeads) section.append(el('h2', 'cat-head', c || 'Weitere'));
      const grid = el('div', 'grid');
      for (const t of byCat.get(c)) {
        const a = el('a', 'card' + (t.enabled === false ? ' off' : ''));
        a.href = t.url;
        const body = el('span', 'card-body');
        const name = el('span', 'name', t.name);
        if (t.enabled === false) name.append(el('span', 'chip-maint', 'Wartung'));
        body.append(name);
        if (t.description) body.append(el('span', 'desc', t.description));
        body.append(el('span', 'path', t.url.startsWith('/') ? t.url : t.url.replace(/^https:\/\//, '').replace(/\/$/, '')));
        const arrow = svgIcon('M7 17 17 7M8 7h9v9', 18);
        arrow.classList.add('arrow');
        a.append(tile(t), body, arrow);
        a.dataset.q = (t.name + ' ' + t.description + ' ' + t.slug + ' ' + c).toLowerCase();
        grid.append(a);
        cards.push(a);
      }
      section.append(grid);
      $('groups').append(section);
    }
    if (tools.length > 6) $('search-box').hidden = false;
    $('search').addEventListener('input', ev => {
      const q = ev.target.value.trim().toLowerCase();
      let shown = 0;
      for (const c of cards) { const ok = !q || c.dataset.q.includes(q); c.hidden = !ok; if (ok) shown++; }
      // Gruppen ohne Treffer ausblenden
      for (const g of document.querySelectorAll('.group')) g.hidden = ![...g.querySelectorAll('.card')].some(c => !c.hidden);
      if (shown) empty.hidden = true; else showEmpty('Keine Treffer', 'Zu „' + ev.target.value.trim() + '“ gibt es kein Tool.');
    });
  } catch (e) {
    $('hello').textContent = 'Willkommen';
    showEmpty('Tools konnten nicht geladen werden', 'Bitte lade die Seite neu.');
  }
})();
