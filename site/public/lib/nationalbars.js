/* ─────────────────────────────────────────────────────────────
   nationalbars.js — small horizontal-bar generator for national
   party tallies. Supports two layouts:
     • value bars (positive 0..max, e.g. primary share %)
     • signed bars (negative left, positive right, e.g. swing pp)

   Public API:
     renderBars(container, opts) → controller

   Options:
     {
       data: [{ key, label, value, color }],
       signed: false,     // true → centre at 0, bars extend both ways
       max:    null,      // optional override
       format: (v) => '12.3%',
       sortDescending: true,
     }

   Controller: { resize(), destroy() }
   ───────────────────────────────────────────────────────────── */
(function (global) {
  'use strict';

  function fmtDefault(v) {
    if (typeof v !== 'number') return String(v);
    return v.toFixed(1);
  }

  function renderBars(container, opts) {
    opts = opts || {};
    if (!container) return null;
    container.innerHTML = '';
    container.classList.add('nb-host');

    const data = (opts.data || []).slice();
    if (opts.sortDescending !== false && !opts.signed) {
      data.sort((a, b) => b.value - a.value);
    }
    const fmt = opts.format || fmtDefault;
    const signed = !!opts.signed;
    const maxAbs = opts.max ?? Math.max(0.0001, ...data.map((d) => Math.abs(d.value)));

    if (opts.title) {
      const head = document.createElement('div');
      head.className = 'nb-head';
      const t = document.createElement('div');
      t.className = 'nb-title';
      t.textContent = opts.title.toUpperCase();
      head.appendChild(t);
      if (opts.hint) {
        const h = document.createElement('div');
        h.className = 'nb-hint';
        h.textContent = opts.hint;
        head.appendChild(h);
      }
      container.appendChild(head);
    }

    const list = document.createElement('div');
    list.className = signed ? 'nb-list nb-signed' : 'nb-list';
    container.appendChild(list);

    for (const d of data) {
      const row = document.createElement('div');
      row.className = 'nb-row';
      const lbl = document.createElement('div');
      lbl.className = 'nb-lbl';
      lbl.textContent = d.label;
      row.appendChild(lbl);

      const track = document.createElement('div');
      track.className = 'nb-track';
      if (signed) {
        track.classList.add('nb-track-signed');
        const centre = document.createElement('div');
        centre.className = 'nb-centre';
        track.appendChild(centre);

        const bar = document.createElement('div');
        bar.className = 'nb-bar';
        const pct = Math.min(1, Math.abs(d.value) / maxAbs);
        if (d.value >= 0) bar.style.left = '50%';
        else              bar.style.right = '50%';
        bar.style.width = '0%';
        bar.style.background = d.color || 'var(--oth)';
        track.appendChild(bar);
        requestAnimationFrame(() => { bar.style.width = (pct * 50) + '%'; });
      } else {
        const bar = document.createElement('div');
        bar.className = 'nb-bar';
        bar.style.width = '0%';
        bar.style.background = d.color || 'var(--oth)';
        track.appendChild(bar);
        const pct = Math.min(1, d.value / maxAbs);
        requestAnimationFrame(() => { bar.style.width = (pct * 100) + '%'; });
      }
      row.appendChild(track);

      const val = document.createElement('div');
      val.className = 'nb-val';
      val.textContent = fmt(d.value);
      row.appendChild(val);

      list.appendChild(row);
    }

    return {
      resize() {},
      destroy() {
        container.innerHTML = '';
        container.classList.remove('nb-host');
      },
    };
  }

  global.renderBars = renderBars;
})(window);
