'use strict';
(async () => {
  const status = document.getElementById('status');
  const grid = document.getElementById('grid');
  try {
    const [me, tools] = await Promise.all([
      fetch('/api/me').then(r => { if (!r.ok) throw 0; return r.json(); }),
      fetch('/api/tools').then(r => { if (!r.ok) throw 0; return r.json(); }),
    ]);
    document.getElementById('who').textContent = me.name;
    if (me.isAdmin) document.getElementById('admin-link').hidden = false;
    if (!tools.length) {
      status.textContent = 'Für dich sind noch keine Tools freigeschaltet.';
      return;
    }
    status.hidden = true;
    for (const t of tools) {
      const a = document.createElement('a');
      a.className = 'card';
      a.href = '/' + encodeURIComponent(t.slug) + '/';
      const icon = document.createElement('span');
      icon.className = 'icon';
      icon.textContent = t.icon || '🔧';
      const name = document.createElement('span');
      name.className = 'name';
      name.textContent = t.name;
      a.append(icon, name);
      if (t.description) {
        const d = document.createElement('span');
        d.className = 'desc';
        d.textContent = t.description;
        a.append(d);
      }
      grid.append(a);
    }
  } catch (e) {
    status.textContent = 'Konnte die Tool-Liste nicht laden.';
  }
})();
