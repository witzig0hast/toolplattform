'use strict';
// Gemeinsame Helfer für Portal und Verwaltung (kein innerHTML -> kein XSS)
const SVG_NS = 'http://www.w3.org/2000/svg';

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function svgIcon(d, size = 16) {
  const s = document.createElementNS(SVG_NS, 'svg');
  s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('width', size);
  s.setAttribute('height', size);
  s.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(SVG_NS, 'path');
  p.setAttribute('d', d);
  s.append(p);
  return s;
}

// Kachel mit Emoji oder Anfangsbuchstabe; Farbton stabil aus dem Pfad abgeleitet
function tile(tool) {
  const t = el('span', 'tile' + (tool.icon ? ' emoji' : ''), tool.icon || tool.name.trim().charAt(0).toUpperCase());
  let h = 0;
  for (const c of tool.slug) h = (h * 31 + c.charCodeAt(0)) % 360;
  t.style.setProperty('--h', h);
  t.setAttribute('aria-hidden', 'true');
  return t;
}

function initials(name) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return ((parts[0] || '?')[0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
}
