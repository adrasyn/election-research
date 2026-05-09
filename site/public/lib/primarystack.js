/* ─────────────────────────────────────────────────────────────
   primarystack.js — year-by-year primary-vote stacked bars.

   Renders the "Primary vote · stacked" sub-chart inside the
   History pillar. Each row is one election; each row's bar is a
   horizontal stack of party slices summing to 100%.

   Encoded conventions:
     • Party order within each row is stable across rows (so the
       eye can track each party's slice growing/shrinking).
     • Active year (default = last) gets `.yr.active` accent class.
     • Per-party CSS var lookups (var(--{party})) — no hardcoded
       colours, so any party in /parties.yaml works.

   Public API:
     renderPrimaryStack(host, data, opts?)

   Data shape:
     {
       title?: 'Primary vote · stacked',     // optional, default shown
       meta?:  '% OF VOTES',                 // optional right-side meta
       partyOrder: ['ALP', 'LIB', 'GRN', 'OTH'],   // left-to-right slice order
       partyOf:    { ALP:'alp', LIB:'lib', GRN:'grn', OTH:'oth' },
       rows: [
         { year: '2010', shares: { ALP: 33.6, LIB: 46.8, GRN: 11.4, OTH: 8.2 } },
         ...
       ],
       activeYear?: '2025',
     }
   ───────────────────────────────────────────────────────────── */
(function (global) {
  'use strict';

  function el(tag, cls, attrs) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  function escapeHTML(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c]));
  }

  function renderPrimaryStack(host, data) {
    host.innerHTML = '';

    const wrap = el('div');
    wrap.style.marginTop = '8px';

    // header row
    if (data.title || data.meta) {
      const head = el('div');
      head.style.cssText = 'display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px';
      if (data.title) {
        const t = el('span', 'chart-title');
        t.style.cssText = 'font-family:var(--mono);font-size:9.5px;letter-spacing:0.18em;color:var(--t-3);text-transform:uppercase';
        t.textContent = data.title;
        head.appendChild(t);
      }
      if (data.meta) {
        const m = el('span', 'pillar-meta');
        m.textContent = data.meta;
        head.appendChild(m);
      }
      wrap.appendChild(head);
    }

    const partyOf = data.partyOf || {};
    const order   = data.partyOrder || [];

    for (const row of data.rows) {
      const isActive = row.year === data.activeYear;
      const r = el('div', 'stack-row');

      const yr = el('span', 'yr' + (isActive ? ' active' : ''));
      yr.textContent = row.year;
      r.appendChild(yr);

      const bar = el('div', 'bar');
      let total = 0;
      for (const id of order) {
        const share = row.shares[id] || 0;
        if (share <= 0) continue;
        const seg = el('span');
        const partyVar = partyOf[id] || 'oth';
        seg.style.cssText = `background:var(--${partyVar});width:${share}%`;
        bar.appendChild(seg);
        total += share;
      }
      r.appendChild(bar);

      const tot = el('span', 'total');
      tot.textContent = total.toFixed(1);
      r.appendChild(tot);

      wrap.appendChild(r);
    }

    host.appendChild(wrap);
  }

  global.renderPrimaryStack = renderPrimaryStack;
})(typeof window !== 'undefined' ? window : globalThis);
