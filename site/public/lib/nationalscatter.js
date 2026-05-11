/* ─────────────────────────────────────────────────────────────
   nationalscatter.js — generic 2D scatter with categorical or
   continuous axes per dimension.

   One dot per electorate, coloured by winner. The X and Y axes
   are independently any combination of:
     • continuous (e.g. income, margin, primary vote pct)
     • categorical (e.g. state — dots are jittered within an
       ordinal band).

   Public API:
     renderScatter(container, opts) → controller

   Options:
     {
       data: [{ id, name, state, partyKey, partyAb, x, y, surname, ... }],
       xAxis: { label, hint, kind: 'continuous' | 'categorical',
                domain?: [lo, hi],          // continuous only
                ticks?: [...],              // continuous only
                categories?: [...],         // categorical only
                format?: (v) => string },
       yAxis: { same shape as xAxis },
       onHover: (datum, x, y) => {},
       onLeave: () => {},
       onClick: (datum, evt) => {},
     }

   Controller: { resize(), destroy(), highlight(id|null) }
   ───────────────────────────────────────────────────────────── */
(function (global) {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';

  function svgEl(tag, attrs) {
    const el = document.createElementNS(NS, tag);
    if (attrs) for (const k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }

  function defaultFmt(v) {
    if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(1);
    return String(v);
  }

  // PRNG: deterministic per-id jitter so dots don't dance on resize.
  function hashInt(str) {
    let h = 2166136261;
    for (let i = 0; i < (str || '').length; i++) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return (h >>> 0) / 4294967295;
  }

  function renderScatter(container, opts) {
    opts = opts || {};
    if (!container) return null;

    const radius = opts.radius || 3.5;
    const padL = 88;
    const padR = 28;
    const padTop = 28;
    const padBot = 64;
    let width = container.clientWidth || 800;
    let height = container.clientHeight || Math.max(420, container.clientWidth * 0.5) || 480;

    container.innerHTML = '';
    container.classList.add('scatter-host');

    const svg = svgEl('svg', { class: 'sc-svg', xmlns: NS });
    svg.style.display = 'block';
    svg.style.width = '100%';
    svg.style.height = '100%';
    container.appendChild(svg);

    function makeAxis(axis, dataAccessor) {
      const vals = (opts.data || [])
        .map(dataAccessor)
        .filter((v) => v != null && (axis.kind === 'categorical' || Number.isFinite(v)));
      if (axis.kind === 'categorical') {
        const cats = axis.categories || [...new Set(vals)];
        return {
          kind: 'cat',
          categories: cats,
          posOf(v) {
            const i = cats.indexOf(v);
            return i < 0 ? 0 : (i + 0.5) / cats.length;  // 0..1
          },
          ticks: cats.map((c, i) => ({ value: c, pos: (i + 0.5) / cats.length })),
          format: axis.format || defaultFmt,
        };
      }
      const sorted = vals.slice().sort((a, b) => a - b);
      const dMin = axis.domain ? axis.domain[0] : sorted[0];
      const dMax = axis.domain ? axis.domain[1] : sorted[sorted.length - 1];
      const range = (dMax - dMin) || 1;
      const ticks = axis.ticks
        ? axis.ticks.filter((t) => t >= dMin - 1e-6 && t <= dMax + 1e-6).map((t) => ({ value: t, pos: (t - dMin) / range }))
        : niceLinearTicks(dMin, dMax).map((t) => ({ value: t, pos: (t - dMin) / range }));
      return {
        kind: 'num',
        min: dMin,
        max: dMax,
        posOf(v) { return (v - dMin) / range; },
        ticks,
        format: axis.format || defaultFmt,
      };
    }

    function niceLinearTicks(lo, hi) {
      // ~6 nicely-rounded ticks.
      const span = hi - lo;
      if (span <= 0) return [lo];
      const step = Math.pow(10, Math.floor(Math.log10(span / 5)));
      const candidates = [step, step * 2, step * 5, step * 10];
      let chosen = candidates[0];
      for (const c of candidates) if (span / c >= 4 && span / c <= 8) chosen = c;
      const start = Math.ceil(lo / chosen) * chosen;
      const out = [];
      for (let v = start; v <= hi + 1e-9; v += chosen) out.push(Number(v.toFixed(8)));
      return out;
    }

    let xScale, yScale;
    let scaffoldG = null;
    let dotsG = null;
    const dotById = new Map(); // data-id → circle element

    function paint() {
      width = container.clientWidth || width;
      // Use whatever vertical space the container has — caller is
      // responsible for sizing the container via flex / grid. Floor at
      // 320 so the chart stays usable on tiny screens.
      height = Math.max(320, container.clientHeight || width * 0.55);
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

      // Wipe scaffolding (frame, grid, ticks, axis labels) but reuse
      // the dots group so existing circles can tween to new positions.
      if (scaffoldG) scaffoldG.remove();
      scaffoldG = svgEl('g', { class: 'sc-scaffold' });
      // scaffold below dots
      svg.insertBefore(scaffoldG, svg.firstChild);
      if (!dotsG) {
        dotsG = svgEl('g', { class: 'sc-dots' });
        svg.appendChild(dotsG);
      }

      xScale = makeAxis(opts.xAxis || {}, (d) => d.x);
      yScale = makeAxis(opts.yAxis || {}, (d) => d.y);

      const plotW = width - padL - padR;
      const plotH = height - padTop - padBot;

      const xToPx = (v) => padL + xScale.posOf(v) * plotW;
      const yToPx = (v) => padTop + (1 - yScale.posOf(v)) * plotH;

      // ── Frame ──
      scaffoldG.appendChild(svgEl('rect', {
        x: padL, y: padTop, width: plotW, height: plotH,
        class: 'sc-frame',
      }));

      // ── Gridlines + tick labels ──
      for (const t of xScale.ticks) {
        const x = padL + t.pos * plotW;
        scaffoldG.appendChild(svgEl('line', {
          x1: x, x2: x, y1: padTop, y2: padTop + plotH,
          class: 'sc-grid',
        }));
        const txt = svgEl('text', {
          x: x, y: padTop + plotH + 18,
          'text-anchor': 'middle',
          class: 'sc-tick',
        });
        txt.textContent = xScale.format(t.value);
        scaffoldG.appendChild(txt);
      }
      for (const t of yScale.ticks) {
        const y = padTop + (1 - t.pos) * plotH;
        scaffoldG.appendChild(svgEl('line', {
          x1: padL, x2: padL + plotW, y1: y, y2: y,
          class: 'sc-grid',
        }));
        const txt = svgEl('text', {
          x: padL - 8, y: y + 3,
          'text-anchor': 'end',
          class: 'sc-tick',
        });
        txt.textContent = yScale.format(t.value);
        scaffoldG.appendChild(txt);
      }

      // ── Axis labels ──
      const xLbl = svgEl('text', {
        x: padL + plotW / 2, y: height - 14,
        'text-anchor': 'middle',
        class: 'sc-axis-label',
      });
      xLbl.textContent = ((opts.xAxis && opts.xAxis.label) || '').toUpperCase();
      scaffoldG.appendChild(xLbl);

      const yLbl = svgEl('text', {
        x: 16, y: padTop + plotH / 2,
        'text-anchor': 'middle',
        class: 'sc-axis-label',
        transform: `rotate(-90, 16, ${padTop + plotH / 2})`,
      });
      yLbl.textContent = ((opts.yAxis && opts.yAxis.label) || '').toUpperCase();
      scaffoldG.appendChild(yLbl);

      // ── Dots — reconcile against existing ones so cx/cy tween ──
      const data = opts.data || [];
      const xCatStep = xScale.kind === 'cat' ? plotW / xScale.categories.length : 0;
      const yCatStep = yScale.kind === 'cat' ? plotH / yScale.categories.length : 0;

      const seen = new Set();
      data.forEach((d, i) => {
        if (d.x == null || d.y == null) return;
        let px = xToPx(d.x);
        let py = yToPx(d.y);
        if (xScale.kind === 'cat') px += (hashInt(d.id + 'x') - 0.5) * xCatStep * 0.6;
        if (yScale.kind === 'cat') py += (hashInt(d.id + 'y') - 0.5) * yCatStep * 0.6;
        const partyKey = (d.partyKey || 'oth').toLowerCase();
        seen.add(d.id);

        let c = dotById.get(d.id);
        if (!c) {
          c = svgEl('circle', { r: radius, cx: px, cy: py });
          c.setAttribute('class', `bw-dot bw-dot-${partyKey} sc-dot is-entering`);
          c.setAttribute('data-id', d.id || '');
          c.style.animationDelay = (Math.random() * 280) + 'ms';
          dotsG.appendChild(c);
          dotById.set(d.id, c);
          c.addEventListener('mouseenter', (e) => {
            c.classList.add('is-hover');
            if (opts.onHover) opts.onHover(c.__datum, e.clientX, e.clientY);
          });
          c.addEventListener('mouseleave', () => {
            c.classList.remove('is-hover');
            if (opts.onLeave) opts.onLeave();
          });
          c.addEventListener('click', (e) => {
            if (opts.onClick) opts.onClick(c.__datum, e);
          });
        } else {
          // Existing dot — animate to new coords via cx/cy transition.
          c.setAttribute('class', `bw-dot bw-dot-${partyKey} sc-dot`);
          c.setAttribute('cx', px);
          c.setAttribute('cy', py);
        }
        c.__datum = d;
      });

      // Remove dots that aren't in the new dataset.
      for (const [id, el] of dotById) {
        if (!seen.has(id)) { el.remove(); dotById.delete(id); }
      }
    }

    paint();

    let resizeRaf = 0;
    function onResize() {
      if (resizeRaf) cancelAnimationFrame(resizeRaf);
      resizeRaf = requestAnimationFrame(paint);
    }
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(onResize);
      ro.observe(container);
      container.__scRO = ro;
    } else {
      window.addEventListener('resize', onResize);
    }

    return {
      resize: paint,
      // Update options in place and re-paint. Existing dots stay in
      // the DOM, so cx/cy transitions fire as their values change.
      update(newOpts) {
        if (!newOpts) return;
        if (newOpts.data)  opts.data  = newOpts.data;
        if (newOpts.xAxis) opts.xAxis = newOpts.xAxis;
        if (newOpts.yAxis) opts.yAxis = newOpts.yAxis;
        if (newOpts.onHover) opts.onHover = newOpts.onHover;
        if (newOpts.onLeave) opts.onLeave = newOpts.onLeave;
        if (newOpts.onClick) opts.onClick = newOpts.onClick;
        paint();
      },
      destroy() {
        if (container.__scRO) { container.__scRO.disconnect(); delete container.__scRO; }
        container.innerHTML = '';
        container.classList.remove('scatter-host');
      },
      highlight(id) {
        svg.querySelectorAll('circle.sc-dot').forEach((c) => {
          c.classList.toggle('is-active', !!id && c.getAttribute('data-id') === id);
        });
      },
    };
  }

  global.renderScatter = renderScatter;
})(window);
