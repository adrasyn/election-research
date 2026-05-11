/* ─────────────────────────────────────────────────────────────
   nationalbeeswarm.js — beeswarm strip generator with party lanes.

   Renders one horizontal strip with N dots (one per electorate),
   positioned by their value on the chosen axis. Dots can be
   partitioned into horizontal "lanes" (e.g. one per major-party
   bloc) so the vertical space gets used to group political
   identity rather than just resolve overlap.

   Encoded conventions:
     • x = value on the axis; positions are pixel-exact.
     • y = lane centre + small jitter resolving in-lane overlaps.
     • Dots are coloured by winning party using the css-variable
       palette so they always agree with the map + legend.
     • Quartile reference lines (Q1, median, Q3) span all lanes.

   Public API:
     renderBeeswarmStrip(container, opts) → controller

   Options:
     {
       data: [{ id, name, state, partyKey, partyAb, lane, value, ... }],
       lanes: [{ id, label }],                       // optional; default
                                                     // = single lane
       axisLabel: 'Median household income',
       axisHint:  '$ per week · ABS 2021',
       format:    (v) => '$' + Math.round(v).toLocaleString(),
       ticks:     [1000, 1500, 2000, 2500, 3000],   // optional
       domain:    [min, max],                        // optional
       quartiles: true,                              // default true
       radius:    3.5,                               // optional
       laneHeight: 36,                               // optional
       onHover:   (datum, x, y) => {},
       onLeave:   () => {},
       onClick:   (datum, evt) => {},
     }

   Controller: { resize(), destroy(), highlight(seatId | null) }
   ───────────────────────────────────────────────────────────── */
(function (global) {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';

  function svgEl(tag, attrs) {
    const el = document.createElementNS(NS, tag);
    if (attrs) for (const k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }

  function quantile(sorted, q) {
    if (!sorted.length) return null;
    const pos = (sorted.length - 1) * q;
    const lo = Math.floor(pos);
    const hi = Math.ceil(pos);
    if (lo === hi) return sorted[lo];
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  }

  // Deterministic anti-overlap packer within a single lane.
  // Walks dots in x-sorted order and tries y=center, then ±step
  // alternating, capped at ±halfH. If the cap is hit it accepts
  // the overlap — better than dropping a seat.
  function packLane(points, radius, halfH) {
    const step = Math.max(1.2, radius * 1.4);
    const padSq = (radius * 2) * (radius * 2);
    const placed = [];
    const sorted = points
      .map((p, idx) => ({ idx, x: p.x }))
      .sort((a, b) => a.x - b.x);
    const yOf = new Array(points.length).fill(0);
    const window = Math.max(1, Math.floor(halfH / step));
    for (const p of sorted) {
      let y = 0;
      let ok = false;
      for (let k = 0; k <= window; k++) {
        const tries = k === 0 ? [0] : [k * step, -k * step];
        for (const cy of tries) {
          let clear = true;
          for (let j = placed.length - 1; j >= 0; j--) {
            const q = placed[j];
            if (p.x - q.x > radius * 2.2) break;
            const dx = p.x - q.x;
            const dy = cy - q.y;
            if (dx * dx + dy * dy < padSq) { clear = false; break; }
          }
          if (clear) { y = cy; ok = true; break; }
        }
        if (ok) break;
      }
      placed.push({ x: p.x, y });
      yOf[p.idx] = y;
    }
    return yOf;
  }

  function renderBeeswarmStrip(container, opts) {
    opts = opts || {};
    const data = (opts.data || []).filter((d) => Number.isFinite(d.value));
    if (!container) return null;

    const radius = opts.radius || 3.5;
    const lanes = (opts.lanes && opts.lanes.length)
      ? opts.lanes
      : [{ id: '_all', label: '' }];
    const laneHeight = opts.laneHeight || 36;
    const padL = lanes[0].id === '_all' ? 22 : 92; // room for lane label
    const padR = 22;
    const padTop = 28;     // axis label + hint
    const padBot = 24;     // x-axis ticks
    const stripBody = lanes.length * laneHeight;
    const height = padTop + stripBody + padBot;

    container.innerHTML = '';
    container.classList.add('beeswarm-strip');

    // Header (axis label + hint).
    const head = document.createElement('div');
    head.className = 'bw-head';
    const label = document.createElement('div');
    label.className = 'bw-label';
    label.textContent = (opts.axisLabel || '').toUpperCase();
    const hint = document.createElement('div');
    hint.className = 'bw-hint';
    hint.textContent = opts.axisHint || '';
    head.appendChild(label);
    head.appendChild(hint);
    container.appendChild(head);

    const svg = svgEl('svg', { class: 'bw-svg', xmlns: NS });
    svg.style.display = 'block';
    svg.style.width = '100%';
    svg.style.height = height + 'px';
    container.appendChild(svg);

    let width = container.clientWidth || 800;

    function buildScale() {
      const values = data.map((d) => d.value).sort((a, b) => a - b);
      const dMin = opts.domain ? opts.domain[0] : values[0];
      const dMax = opts.domain ? opts.domain[1] : values[values.length - 1];
      const range = dMax - dMin || 1;
      const x0 = padL;
      const x1 = width - padR;
      return {
        min: dMin,
        max: dMax,
        q1: quantile(values, 0.25),
        median: quantile(values, 0.5),
        q3: quantile(values, 0.75),
        scale: (v) => x0 + ((v - dMin) / range) * (x1 - x0),
        x0, x1,
      };
    }

    function paint() {
      width = container.clientWidth || width;
      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      svg.innerHTML = '';

      const sc = buildScale();
      const laneCenter = (i) => padTop + i * laneHeight + laneHeight / 2;
      const halfLaneH = (laneHeight - radius * 2) / 2;

      // ── Reference lines (Q1/median/Q3 or caller-supplied) ──
      const refY0 = padTop - 2;
      const refY1 = padTop + stripBody + 2;
      if (opts.quartiles !== false) {
        for (const { v, kind } of [
          { v: sc.q1,     kind: 'q' },
          { v: sc.median, kind: 'med' },
          { v: sc.q3,     kind: 'q' },
        ]) {
          if (v == null) continue;
          const x = sc.scale(v);
          svg.appendChild(svgEl('line', {
            x1: x, x2: x, y1: refY0, y2: refY1,
            class: `bw-qline bw-qline-${kind}`,
          }));
        }
        if (sc.median != null) {
          const medX = sc.scale(sc.median);
          const medLbl = svgEl('text', {
            x: medX, y: padTop - 6,
            'text-anchor': 'middle',
            class: 'bw-medlbl',
          });
          medLbl.textContent = 'MEDIAN';
          svg.appendChild(medLbl);
        }
      }
      for (const ref of (opts.referenceLines || [])) {
        if (ref?.value == null) continue;
        if (ref.value < sc.min - 1e-6 || ref.value > sc.max + 1e-6) continue;
        const x = sc.scale(ref.value);
        svg.appendChild(svgEl('line', {
          x1: x, x2: x, y1: refY0, y2: refY1,
          class: 'bw-qline bw-qline-ref',
        }));
        if (ref.label) {
          const lbl = svgEl('text', {
            x: x, y: padTop - 6,
            'text-anchor': 'middle',
            class: 'bw-medlbl',
          });
          lbl.textContent = ref.label;
          svg.appendChild(lbl);
        }
      }

      // ── Per-lane scaffolding + labels + baselines ──
      lanes.forEach((lane, i) => {
        const cy = laneCenter(i);
        // baseline
        svg.appendChild(svgEl('line', {
          x1: sc.x0 - 4, x2: sc.x1 + 4, y1: cy, y2: cy,
          class: 'bw-baseline',
        }));
        // lane label
        if (lane.label) {
          const t = svgEl('text', {
            x: padL - 12, y: cy + 3.5,
            'text-anchor': 'end',
            class: 'bw-lane-label',
          });
          t.textContent = lane.label.toUpperCase();
          svg.appendChild(t);
        }
      });

      // ── Pack + paint dots, per lane ──
      const dotsG = svgEl('g', { class: 'bw-dots' });
      svg.appendChild(dotsG);

      const byLane = new Map();
      lanes.forEach((l) => byLane.set(l.id, []));
      data.forEach((d, idx) => {
        const laneId = d.lane && byLane.has(d.lane) ? d.lane : lanes[0].id;
        byLane.get(laneId).push({ idx, x: sc.scale(d.value), d });
      });

      lanes.forEach((lane, laneIdx) => {
        const points = byLane.get(lane.id) || [];
        const ys = packLane(points, radius, halfLaneH);
        const cy = laneCenter(laneIdx);
        points.forEach((p, i) => {
          const partyKey = (p.d.partyKey || 'oth').toLowerCase();
          const c = svgEl('circle', {
            cx: p.x, cy: cy + ys[i], r: radius,
            class: `bw-dot bw-dot-${partyKey} is-entering`,
            'data-id': p.d.id || '',
            'data-name': p.d.name || '',
            'data-party': p.d.partyAb || '',
            'data-value': p.d.value,
          });
          c.style.animationDelay = (Math.random() * 280) + 'ms';
          dotsG.appendChild(c);
          c.addEventListener('mouseenter', (e) => {
            c.classList.add('is-hover');
            if (opts.onHover) opts.onHover(p.d, e.clientX, e.clientY);
          });
          c.addEventListener('mouseleave', () => {
            c.classList.remove('is-hover');
            if (opts.onLeave) opts.onLeave();
          });
          c.addEventListener('click', (e) => {
            if (opts.onClick) opts.onClick(p.d, e);
          });
        });
      });

      // ── Tick row at the bottom ──
      const ticks = opts.ticks
        ? opts.ticks.filter((t) => t >= sc.min - 1e-6 && t <= sc.max + 1e-6)
        : [sc.min, sc.median, sc.max];
      const tickY = padTop + stripBody + 16;
      for (const t of ticks) {
        if (t == null || !Number.isFinite(t)) continue;
        const x = sc.scale(t);
        const tk = svgEl('text', {
          x: x, y: tickY,
          'text-anchor': 'middle',
          class: 'bw-tick',
        });
        tk.textContent = opts.format ? opts.format(t) : String(t);
        svg.appendChild(tk);
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
      container.__bwRO = ro;
    } else {
      window.addEventListener('resize', onResize);
    }

    return {
      resize: paint,
      destroy() {
        if (container.__bwRO) { container.__bwRO.disconnect(); delete container.__bwRO; }
        container.innerHTML = '';
        container.classList.remove('beeswarm-strip');
      },
      highlight(seatId) {
        const dots = svg.querySelectorAll('circle.bw-dot');
        dots.forEach((d) => {
          if (seatId && d.getAttribute('data-id') === seatId) d.classList.add('is-active');
          else d.classList.remove('is-active');
        });
      },
    };
  }

  global.renderBeeswarmStrip = renderBeeswarmStrip;
})(window);
