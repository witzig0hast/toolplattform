// Knopf "zurück zum Portal" - wird von der Tools-Plattform in die Seiten der Tools eingeblendet.
// Stört die App nicht: eigener Shadow-DOM, keine globalen Variablen, keine Abhängigkeiten.
(function () {
  'use strict';
  var me = document.currentScript;
  var portal = (me && me.getAttribute('data-portal')) || '/_platform/portal';
  if (window.top !== window || document.getElementById('tools-back-host')) return;

  function mount() {
    var host = document.createElement('div');
    host.id = 'tools-back-host';
    var root = host.attachShadow ? host.attachShadow({ mode: 'closed' }) : host;
    var a = document.createElement('a');
    a.href = portal;
    a.title = 'Zurück zum Portal';
    a.setAttribute('aria-label', 'Zurück zum Portal');
    var NS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '20');
    svg.setAttribute('height', '20');
    [[3, 3], [13, 3], [3, 13], [13, 13]].forEach(function (p, i) {
      var r = document.createElementNS(NS, 'rect');
      r.setAttribute('x', p[0]); r.setAttribute('y', p[1]);
      r.setAttribute('width', '8'); r.setAttribute('height', '8'); r.setAttribute('rx', '2');
      r.setAttribute('fill', 'currentColor');
      if (i === 1 || i === 2) r.setAttribute('opacity', '0.45');
      svg.appendChild(r);
    });
    a.appendChild(svg);
    // Stile per CSSOM (funktioniert auch, wenn die App Inline-Styles per CSP verbietet)
    var s = a.style;
    s.position = 'fixed'; s.left = '14px'; s.bottom = '14px'; s.zIndex = '2147483647';
    s.width = '42px'; s.height = '42px'; s.borderRadius = '12px';
    s.display = 'flex'; s.alignItems = 'center'; s.justifyContent = 'center';
    s.background = '#3b5bdb'; s.color = '#fff'; s.boxShadow = '0 4px 14px rgba(0,0,0,.25)';
    s.opacity = '0.55'; s.transition = 'opacity .15s'; s.textDecoration = 'none';
    a.addEventListener('mouseenter', function () { s.opacity = '1'; });
    a.addEventListener('mouseleave', function () { s.opacity = '0.55'; });
    a.addEventListener('focus', function () { s.opacity = '1'; });
    root.appendChild(a);
    document.body.appendChild(host);
  }
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);
})();
