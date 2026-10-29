'use strict';
(async () => {
  const $ = id => document.getElementById(id);
  const empty = $('empty');
  const STAR = 'M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z';
  let cards = [];
  let favorites = [];
  let tools = [];

  function showEmpty(title, text) {
    empty.replaceChildren(el('strong', '', title), document.createTextNode(text));
    empty.hidden = false;
  }

  const shortUrl = t => t.url.startsWith('/') ? t.url : t.url.replace(/^https:\/\//, '').replace(/\/$/, '');

  function card(t) {
    const wrap = el('div', 'card-wrap');
    const a = el('a', 'card' + (t.enabled === false ? ' off' : ''));
    a.href = t.url;
    const body = el('span', 'card-body');
    const name = el('span', 'name', t.name);
    if (t.enabled === false) name.append(el('span', 'chip-maint', 'Wartung'));
    body.append(name);
    if (t.description) body.append(el('span', 'desc', t.description));
    body.append(el('span', 'path', shortUrl(t)));
    if (t.health === 'down') {
      const note = el('span', 'health-note');
      note.append(el('span', 'dot down'), document.createTextNode('Gerade nicht erreichbar'));
      body.append(note);
    }
    const arrow = svgIcon('M7 17 17 7M8 7h9v9', 18);
    arrow.classList.add('arrow');
    a.append(tile(t), body, arrow);

    const star = el('button', 'star');
    star.type = 'button';
    const fav = favorites.includes(t.slug);
    star.setAttribute('aria-pressed', String(fav));
    star.setAttribute('aria-label', fav ? t.name + ' aus Favoriten entfernen' : t.name + ' zu Favoriten');
    star.title = fav ? 'Aus Favoriten entfernen' : 'Zu Favoriten';
    star.append(svgIcon(STAR, 18));
    star.onclick = () => toggleFavorite(t.slug);

    wrap.append(a, star);
    wrap.dataset.q = (t.name + ' ' + t.description + ' ' + t.slug + ' ' + (t.category || '')).toLowerCase();
    cards.push(wrap);
    return wrap;
  }

  function section(title, list) {
    const sec = el('section', 'group');
    if (title) sec.append(el('h2', 'cat-head', title));
    const grid = el('div', 'grid');
    for (const t of list) grid.append(card(t));
    sec.append(grid);
    return sec;
  }

  function render(recent) {
    const groups = $('groups');
    groups.replaceChildren();
    cards = [];
    const bySlug = Object.fromEntries(tools.map(t => [t.slug, t]));

    // Zuletzt benutzt (nur wenn es mehr als ein paar Tools gibt)
    const rec = recent.map(s => bySlug[s]).filter(Boolean).slice(0, 5);
    if (rec.length && tools.length > 3) {
      const box = el('section', 'group');
      box.append(el('h2', 'cat-head', 'Zuletzt benutzt'));
      const row = el('div', 'recent');
      for (const t of rec) {
        const a = el('a');
        a.href = t.url;
        a.append(tile(t), document.createTextNode(t.name));
        row.append(a);
      }
      box.append(row);
      groups.append(box);
    }

    const favs = tools.filter(t => favorites.includes(t.slug));
    if (favs.length) groups.append(section('Favoriten', favs));

    // Nach Kategorie gruppieren (ohne Kategorie zuletzt unter "Weitere")
    const byCat = new Map();
    for (const t of tools) {
      const c = t.category || '';
      if (!byCat.has(c)) byCat.set(c, []);
      byCat.get(c).push(t);
    }
    const cats = [...byCat.keys()].sort((a, b) => (a === '') - (b === '') || a.localeCompare(b, 'de'));
    const showHeads = favs.length || cats.length > 1 || cats[0] !== '';
    for (const c of cats) groups.append(section(showHeads ? (c || (cats.length > 1 ? 'Weitere' : 'Alle Tools')) : '', byCat.get(c)));
    applySearch();
  }

  async function toggleFavorite(slug) {
    const next = favorites.includes(slug) ? favorites.filter(s => s !== slug) : [...favorites, slug];
    try {
      const res = await fetch('/_platform/api/me/favorites', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ favorites: next }),
      });
      if (!res.ok) throw new Error(res.status);
      const me = await res.json();
      favorites = me.favorites;
      render(me.recent);
    } catch (e) { /* Favoriten sind ein Komfort - bei Fehler bleibt alles wie es ist */ }
  }

  function applySearch() {
    const q = $('search').value.trim().toLowerCase();
    let shown = 0;
    for (const c of cards) { const ok = !q || c.dataset.q.includes(q); c.hidden = !ok; if (ok) shown++; }
    // Gruppen ohne Treffer ausblenden ("Zuletzt benutzt" nur ohne Suche)
    for (const g of document.querySelectorAll('.group')) {
      const own = [...g.querySelectorAll('.card-wrap')];
      g.hidden = own.length ? !own.some(c => !c.hidden) : !!q;
    }
    if (shown || !q) empty.hidden = !!tools.length; else showEmpty('Keine Treffer', 'Zu „' + $('search').value.trim() + '“ gibt es kein Tool.');
  }

  try {
    const get = u => fetch(u).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
    const [me, list] = await Promise.all([get('/_platform/api/me'), get('/_platform/api/tools')]);
    tools = list;
    favorites = me.favorites || [];

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
    if (tools.length > 6) $('search-box').hidden = false;
    $('search').addEventListener('input', applySearch);
    render(me.recent || []);
  } catch (e) {
    $('hello').textContent = 'Willkommen';
    showEmpty('Tools konnten nicht geladen werden', 'Bitte lade die Seite neu.');
  }
})();
