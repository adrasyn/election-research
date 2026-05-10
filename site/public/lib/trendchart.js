/* ─────────────────────────────────────────────────────────────
   trendchart.js — historical TCP/2PP line chart generator.

   Single source of truth for the History pillar's line chart.
   Takes N party series (each with N points, one per election) and
   emits an SVG with auto-scaled y-axis, threshold reference line,
   and active-election highlight.

   Encoded conventions:
     • Y-axis is symmetric around the threshold when one is given
       (so 50% always sits on the centre gridline).
     • Y-range is snapped to even values for clean labels.
     • Active election (default = last) gets an accent dashed
       vertical and an accent-coloured x-axis label.
     • Each series is stroked + dotted in its party colour using
       existing .chart-line-{party} / .chart-pt.{party} classes.

   Public API:
     renderTrendChart(host, data, opts?) → { svg }

   Data shape:
     {
       title: 'Two-Candidate preferred · Melbourne',
       series: [
         { id, party, label, points: [44.0, 44.7, ...] },
       ],
       xLabels: ['2010', '2013', '2016', '2019', '2022', '2025'],
       activeIndex?: number,    // default: last
       threshold?: number,      // default: 50 (set to null to omit)
       legend?: boolean,        // default: true
     }
   ───────────────────────────────────────────────────────────── */
(function (global) {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  function svgEl(tag, attrs) {
    const el = document.createElementNS(NS, tag);
    if (attrs) for (const k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }
  function escapeHTML(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c]));
  }

  function renderTrendChart(host, data, opts) {
    opts = opts || {};
    const W           = opts.width   || 440;
    const H           = opts.height  || 140;
    const xPadLeft   = opts.xPadLeft   ?? 50;
    const xPadRight  = opts.xPadRight  ?? 38;
    const yPlotTop   = opts.yPlotTop   ?? 0;
    const yPlotBot   = opts.yPlotBot   ?? 120;
    const labelY     = opts.labelY     ?? 135;
    const threshold  = data.threshold === null ? null : (data.threshold ?? 50);
    const showLegend = data.legend !== false;

    // ── compute Y range (skip nulls — seats that didn't exist in that year) ──
    let dMin = Infinity, dMax = -Infinity;
    for (const s of data.series) {
      for (const p of s.points) {
        if (p == null) continue;
        if (p < dMin) dMin = p;
        if (p > dMax) dMax = p;
      }
    }
    if (!Number.isFinite(dMin) || !Number.isFinite(dMax)) {
      host.innerHTML = '';
      return { svg: null };
    }
    let yMin, yMax;
    if (threshold != null) {
      const half = Math.max(threshold - dMin, dMax - threshold) + 2;
      yMin = threshold - half;
      yMax = threshold + half;
    } else {
      yMin = dMin - 2;
      yMax = dMax + 2;
    }
    // snap to even values for clean labels
    yMin = Math.floor(yMin / 2) * 2;
    yMax = Math.ceil(yMax / 2) * 2;
    // guarantee a sensible minimum range so labels aren't crowded
    while (yMax - yMin < 8) { yMax += 2; yMin -= 2; }

    function yAt(val) {
      return yPlotTop + (yPlotBot - yPlotTop) * (yMax - val) / (yMax - yMin);
    }

    // ── X positions ──
    const N = data.xLabels.length;
    const xStep = (W - xPadLeft - xPadRight) / Math.max(1, N - 1);
    const xs = [];
    for (let i = 0; i < N; i++) xs.push(xPadLeft + i * xStep);

    const activeIdx = data.activeIndex ?? (N - 1);

    // ── build container scaffolding ──
    host.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.className = 'chart-wrap';

    if (data.title || showLegend) {
      const head = document.createElement('div');
      head.className = 'chart-head';
      if (data.title) {
        const t = document.createElement('span');
        t.className = 'chart-title';
        t.textContent = data.title;
        head.appendChild(t);
      }
      if (showLegend) {
        const legend = document.createElement('div');
        legend.className = 'chart-legend';
        for (const s of data.series) {
          const ll = document.createElement('div');
          ll.className = 'll';
          ll.innerHTML =
            `<span class="sw" style="background:var(--${s.party})"></span>` +
            `<span>${escapeHTML(s.label || s.id)}</span>`;
          legend.appendChild(ll);
        }
        if (threshold != null) {
          const ll = document.createElement('div');
          ll.className = 'll';
          ll.innerHTML =
            '<span class="sw" style="background:var(--t-5);height:1px;border-top:1px dashed var(--t-4)"></span>' +
            `<span>${threshold}%</span>`;
          legend.appendChild(ll);
        }
        head.appendChild(legend);
      }
      wrap.appendChild(head);
    }

    // ── SVG ──
    const svg = svgEl('svg', { class: 'chart-svg', viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none' });

    // gridlines: 5 evenly spaced (top, q1, mid, q3, bottom)
    const gridYs = [yPlotTop, yPlotTop + (yPlotBot - yPlotTop) * 0.25, (yPlotTop + yPlotBot) / 2, yPlotTop + (yPlotBot - yPlotTop) * 0.75, yPlotBot];
    for (const gy of gridYs) {
      svg.appendChild(svgEl('line', { class: 'chart-grid', x1: 0, y1: gy, x2: W, y2: gy }));
    }

    // axis labels (5 corresponding to gridlines, mapping y-position to data value)
    const labelValues = gridYs.map((gy) => yMax - ((gy - yPlotTop) / (yPlotBot - yPlotTop)) * (yMax - yMin));
    for (let i = 0; i < gridYs.length; i++) {
      const text = svgEl('text', { class: 'chart-axis-label', x: 2, y: gridYs[i] === yPlotTop ? gridYs[i] + 10 : (gridYs[i] === yPlotBot ? gridYs[i] + 4 : gridYs[i] + 10) });
      const v = Math.round(labelValues[i] * 10) / 10; // 1 decimal max, usually integer
      const display = (v % 1 === 0) ? v.toFixed(0) : v.toFixed(1);
      text.textContent = (i === 0 ? display + '%' : display);
      svg.appendChild(text);
    }

    // threshold line
    if (threshold != null) {
      const ty = yAt(threshold);
      svg.appendChild(svgEl('line', { class: 'chart-50line', x1: xPadLeft - 16, y1: ty, x2: W, y2: ty }));
    }

    // series paths — break the line at any null so seats that didn't
    // exist in some year don't generate a NaN segment.
    for (const s of data.series) {
      let d = '';
      let started = false;
      for (let i = 0; i < s.points.length; i++) {
        const p = s.points[i];
        if (p == null) {
          started = false;
          continue;
        }
        d += (started ? ' L ' : 'M ') + xs[i].toFixed(2) + ' ' + yAt(p).toFixed(2);
        started = true;
      }
      if (d) {
        svg.appendChild(svgEl('path', {
          class: `chart-line chart-line-${s.party}`,
          // Inline stroke so any party (grn, ind, ca, nat, on, …) renders
          // correctly without needing a per-party CSS rule.
          style: `stroke: var(--${s.party}); stroke-width: 1.6; fill: none;`,
          d,
        }));
      }
    }

    // active election dashed vertical
    if (activeIdx >= 0 && activeIdx < N) {
      const ax = xs[activeIdx];
      svg.appendChild(svgEl('line', {
        x1: ax, y1: yPlotTop, x2: ax, y2: H - 5,
        stroke: 'var(--accent)', 'stroke-width': '0.6',
        'stroke-dasharray': '2 3', opacity: '0.5',
      }));
    }

    // points (skip nulls)
    for (const s of data.series) {
      for (let i = 0; i < s.points.length; i++) {
        const p = s.points[i];
        if (p == null) continue;
        svg.appendChild(svgEl('circle', {
          class: `chart-pt ${s.party}`,
          // Inline stroke matches the line colour for any party.
          style: `stroke: var(--${s.party}); stroke-width: 1.4; fill: var(--bg-base);`,
          cx: xs[i].toFixed(2),
          cy: yAt(p).toFixed(2),
          r: '3.6',
        }));
      }
    }

    // x-axis labels
    for (let i = 0; i < N; i++) {
      const cls = 'chart-x-label' + (i === activeIdx ? ' active' : '');
      const t = svgEl('text', { class: cls, x: xs[i], y: labelY, 'text-anchor': 'middle' });
      t.textContent = data.xLabels[i];
      svg.appendChild(t);
    }

    wrap.appendChild(svg);
    host.appendChild(wrap);
    return { svg };
  }

  global.renderTrendChart = renderTrendChart;
})(typeof window !== 'undefined' ? window : globalThis);
