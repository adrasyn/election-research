/* ─────────────────────────────────────────────────────────────
   waterfall.js — preference-distribution Sankey generator.

   Single source of truth for the prefs pillar. Takes per-round
   candidate vote totals (the shape AEC distribution-of-preferences
   data falls out as) and emits a correct SVG + overlay HTML.

   Encoded conventions:
     • Round 1..N-1 candidate order = primary descending (stable
       across mid-rounds, so eliminated candidate naturally drifts
       rightward).
     • Round N (TCP) candidate order = winner first.
     • Exhausted votes always rightmost in any round.
     • Ribbons partition the eliminated candidate's slot left-to-right
       in destination order — never cross.
     • Ribbon source width === destination gain width (Sankey conservation).
     • Excluded candidate gets a diagonal-stripe overlay on its slot.

   Public API:
     renderWaterfall(container, data, opts?) → { svg, overlay, totalHeight }

   Data shape:
     {
       totalFormal: 102840,
       candidates: [
         { id, party, name, displayShort, surname }
       ],
       rounds: [
         { [candidateId]: votes, ..., exhausted: votes }
       ],
       roundLabels?: ['01 · PRIMARY', '02 · AFTER ANJ', ...]   // optional override
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

  function renderWaterfall(container, data, opts) {
    opts = opts || {};
    const W           = opts.width        || 1000;
    const ROUND_H     = opts.roundHeight  || 40;
    const FINAL_H     = opts.finalHeight  || 54;
    const GAP         = opts.gap          || 48;
    const TOP_PAD     = opts.topPad       || 22;
    const BOTTOM_PAD  = opts.bottomPad    || 8;

    const total = data.totalFormal;
    const px    = (v) => (v * W) / total;
    const N     = data.rounds.length;

    // ── candidate index ──
    const candById = Object.create(null);
    for (const c of data.candidates) candById[c.id] = c;

    // ── determine TCP winner & runner-up from final round ──
    const final = data.rounds[N - 1];
    const finalIds = Object.keys(final).filter((k) => k !== 'exhausted');
    const sortedFinal = [...finalIds].sort((a, b) => final[b] - final[a]);
    const winner   = sortedFinal[0];
    const runnerUp = sortedFinal[1];

    // ── elimination order ──
    const eliminated = []; // eliminated[k] = candidate excluded from round k → k+1
    for (let k = 0; k < N - 1; k++) {
      const from = new Set(Object.keys(data.rounds[k]).filter((x) => x !== 'exhausted'));
      const to   = new Set(Object.keys(data.rounds[k + 1]).filter((x) => x !== 'exhausted'));
      eliminated.push([...from].find((c) => !to.has(c)));
    }

    // ── stable ordering for rounds 1..N-1: primary descending ──
    const r1 = data.rounds[0];
    const r1Order = Object.keys(r1)
      .filter((k) => k !== 'exhausted')
      .sort((a, b) => r1[b] - r1[a]);

    function orderForRound(k) {
      if (k === N - 1) return [winner, runnerUp];
      const survivors = new Set(Object.keys(data.rounds[k]).filter((x) => x !== 'exhausted'));
      return r1Order.filter((c) => survivors.has(c));
    }

    // ── per-round layouts: order + slot positions ──
    const layouts = data.rounds.map((round, k) => {
      const order = orderForRound(k);
      const slots = Object.create(null);
      let x = 0;
      for (const id of order) {
        const w = px(round[id] || 0);
        slots[id] = { left: x, right: x + w, width: w };
        x += w;
      }
      if (round.exhausted && round.exhausted > 0) {
        const w = px(round.exhausted);
        slots._exhausted = { left: x, right: x + w, width: w };
        x += w;
      }
      return { order, slots };
    });

    // ── y-coord helper ──
    function yForRound(k) {
      const isFinal = k === N - 1;
      const top    = TOP_PAD + k * (ROUND_H + GAP);
      const height = isFinal ? FINAL_H : ROUND_H;
      return { top, bottom: top + height, height, isFinal };
    }
    const totalHeight = TOP_PAD + (N - 1) * (ROUND_H + GAP) + FINAL_H + BOTTOM_PAD;

    // ── prepare/clear SVG ──
    let svg;
    if (container.tagName && container.tagName.toLowerCase() === 'svg') {
      svg = container;
      while (svg.firstChild) svg.removeChild(svg.firstChild);
    } else {
      svg = svgEl('svg', { class: 'flow-svg' });
      // remove any prior svg in the container, but keep overlay if caller manages it
      const prior = container.querySelector('svg.flow-svg');
      if (prior) prior.remove();
      container.insertBefore(svg, container.firstChild);
    }
    svg.setAttribute('viewBox', `0 0 ${W} ${totalHeight}`);
    svg.setAttribute('preserveAspectRatio', 'none');
    // Match container height to viewBox so % overlay positions line up exactly with bars.
    if (!(container.tagName && container.tagName.toLowerCase() === 'svg')) {
      container.style.height = totalHeight + 'px';
    }

    // ── defs (excluded stripe pattern) ──
    const defs = svgEl('defs');
    defs.innerHTML =
      '<pattern id="wf-excluded-pattern" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">' +
      '<line x1="0" y1="0" x2="0" y2="6" stroke="rgba(0,0,0,0.45)" stroke-width="2"/></pattern>';
    svg.appendChild(defs);

    // ── render bars ──
    for (let k = 0; k < N; k++) {
      const y      = yForRound(k);
      const layout = layouts[k];
      const g      = svgEl('g', { transform: `translate(0, ${y.top})` });

      for (const id of layout.order) {
        const slot = layout.slots[id];
        const cand = candById[id];
        const partyVar = cand && cand.party ? `var(--${cand.party})` : 'var(--oth)';
        g.appendChild(svgEl('rect', {
          x: slot.left.toFixed(3),
          y: 0,
          width: slot.width.toFixed(3),
          height: y.height,
          fill: partyVar,
        }));

        // diagonal-stripe overlay on the slot of the candidate excluded next
        if (k < N - 1 && eliminated[k] === id) {
          g.appendChild(svgEl('rect', {
            x: slot.left.toFixed(3),
            y: 0,
            width: slot.width.toFixed(3),
            height: y.height,
            fill: 'url(#wf-excluded-pattern)',
          }));
        }
      }
      // exhaust block (rightmost)
      if (layout.slots._exhausted) {
        const slot = layout.slots._exhausted;
        g.appendChild(svgEl('rect', {
          x: slot.left.toFixed(3),
          y: 0,
          width: slot.width.toFixed(3),
          height: y.height,
          fill: 'var(--t-5)',
        }));
      }
      svg.appendChild(g);
    }

    // ── render ribbons ──
    for (let k = 0; k < N - 1; k++) {
      const ex          = eliminated[k];
      const exCand      = candById[ex];
      const sourceSlot  = layouts[k].slots[ex];
      const sourceY     = yForRound(k).bottom;
      const destY       = yForRound(k + 1).top;
      const midY        = (sourceY + destY) / 2;
      const sourceRound = data.rounds[k];
      const destRound   = data.rounds[k + 1];

      // build receivers list (each surviving candidate + exhaust)
      const receivers = [];
      for (const id of layouts[k + 1].order) {
        const prev = sourceRound[id] || 0;
        const cur  = destRound[id]   || 0;
        const gain = cur - prev;
        if (gain > 0) {
          const slot = layouts[k + 1].slots[id];
          receivers.push({
            id,
            destLeft:  slot.right - px(gain),
            destWidth: px(gain),
          });
        }
      }
      const prevExhaust = sourceRound.exhausted || 0;
      const curExhaust  = destRound.exhausted   || 0;
      const exhaustGain = curExhaust - prevExhaust;
      if (exhaustGain > 0 && layouts[k + 1].slots._exhausted) {
        const slot = layouts[k + 1].slots._exhausted;
        receivers.push({
          id: '_exhausted',
          destLeft:  slot.right - px(exhaustGain),
          destWidth: px(exhaustGain),
        });
      }
      receivers.sort((a, b) => a.destLeft - b.destLeft);

      // partition source slot left-to-right in dest order — ribbons never cross
      let sx = sourceSlot.left;
      const ribbonG = svgEl('g', {
        fill: `var(--${exCand.party})`,
        'fill-opacity': '0.5',
        stroke: 'rgba(0,0,0,0.22)',
        'stroke-width': '0.5',
      });
      for (const r of receivers) {
        const sLeft  = sx;
        const sRight = sx + r.destWidth; // conservation: source-width === dest-gain
        const dLeft  = r.destLeft;
        const dRight = r.destLeft + r.destWidth;
        const d = [
          `M ${sLeft.toFixed(3)} ${sourceY}`,
          `C ${sLeft.toFixed(3)} ${midY} ${dLeft.toFixed(3)} ${midY} ${dLeft.toFixed(3)} ${destY}`,
          `L ${dRight.toFixed(3)} ${destY}`,
          `C ${dRight.toFixed(3)} ${midY} ${sRight.toFixed(3)} ${midY} ${sRight.toFixed(3)} ${sourceY}`,
          'Z',
        ].join(' ');
        ribbonG.appendChild(svgEl('path', { d }));
        sx += r.destWidth;
      }
      svg.appendChild(ribbonG);
    }

    // ── overlay (HTML, absolutely positioned over the SVG) ──
    // The container is expected to be position:relative; we render a .wf-overlay div
    // with .rh round-headers and .rb bar-overlays that line up with the SVG bars.
    let overlay = container.querySelector('.wf-overlay');
    if (overlay) overlay.innerHTML = '';
    else {
      overlay = document.createElement('div');
      overlay.className = 'wf-overlay';
      container.appendChild(overlay);
    }

    // round labels — from data, or auto-generated
    const labels = (data.roundLabels && data.roundLabels.length === N) ? data.roundLabels : (() => {
      const out = [];
      out.push(zeroPad(1) + ' · PRIMARY');
      for (let k = 1; k < N - 1; k++) {
        const exId = eliminated[k - 1];
        const tag  = (candById[exId] && (candById[exId].excludedTag || candById[exId].displayShort || candById[exId].party.toUpperCase())) || exId;
        out.push(zeroPad(k + 1) + ' · AFTER ' + tag);
      }
      out.push(zeroPad(N) + ' · TWO-CANDIDATE PREFERRED');
      return out;
    })();

    const finalSet = new Set([winner, runnerUp]);

    // The overlay uses a percentage-based coordinate system that lines up with the SVG viewBox,
    // since the SVG has preserveAspectRatio="none" the X% in HTML matches X/W in SVG.
    // For Y, we convert to absolute pixels via the same viewBox scale.
    // Easiest is to make the overlay match the SVG's natural box: the .waterfall container in
    // existing CSS does this (overlay positioned via top in px relative to SVG). We use percentage
    // top values relative to totalHeight so layout scales with SVG.

    // For compat with existing CSS, position .rh and .rb in absolute pixels matching the
    // SVG's intrinsic coord system. The container CSS sets position:relative and the SVG fills it,
    // so px values here correspond to viewBox units when the SVG's intrinsic size matches.
    // We emit `top` in px relative to the SVG box; the host CSS scales accordingly.
    const yScale = 100 / totalHeight; // percent per viewBox-unit

    for (let k = 0; k < N; k++) {
      const y      = yForRound(k);
      const layout = layouts[k];
      const round  = data.rounds[k];

      // header
      const rh = document.createElement('div');
      rh.className = 'rh';
      rh.style.top = (y.top - 14) * yScale + '%';
      rh.innerHTML = `<span class="num">${escapeHTML(labels[k])}</span>`;
      overlay.appendChild(rh);

      // bar overlay
      const rb = document.createElement('div');
      rb.className = 'rb';
      rb.style.top    = y.top * yScale + '%';
      rb.style.height = y.height * yScale + '%';

      for (const id of layout.order) {
        const cand = candById[id];
        const slot = layout.slots[id];
        const pct  = (round[id] / total) * 100;
        const widthPct = (slot.width / W) * 100;
        const inFinal  = finalSet.has(id);
        const dim      = !inFinal && !y.isFinal; // mid-round non-finalists are dim
        const showLabel = inFinal || pct >= 5;

        if (y.isFinal) {
          // richer two-row overlay for TCP
          const isWinner = id === winner;
          const votes    = round[id];
          const seg = document.createElement('span');
          seg.className = 's large';
          seg.style.width = widthPct + '%';
          const titleLabel = (cand && cand.displayLong) || (cand && cand.displayShort) || id;
          seg.innerHTML =
            `<div class="row-a"><span>${escapeHTML(titleLabel)}</span><span>${formatPct(pct, 2)}</span></div>` +
            `<div class="row-b"><span>${isWinner ? '▲ Winner · ' : ''}${formatNum(votes)} votes</span></div>`;
          rb.appendChild(seg);
        } else {
          const seg = document.createElement('span');
          seg.className = 's' + (dim ? ' dim' : '');
          seg.style.width = widthPct + '%';
          if (showLabel) {
            const short = (cand && cand.displayShort) || id;
            seg.innerHTML = `<span>${escapeHTML(short)}</span><span>${formatPct(pct, 1)}</span>`;
          }
          rb.appendChild(seg);
        }
      }
      overlay.appendChild(rb);
    }

    return { svg, overlay, totalHeight };
  }

  // ── helpers ──
  function zeroPad(n)        { return n < 10 ? '0' + n : '' + n; }
  function formatPct(n, dp)  { return n.toFixed(dp); }
  function formatNum(n)      { return Math.round(n).toLocaleString('en-AU'); }
  function escapeHTML(s)     { return String(s).replace(/[&<>"]/g, (c) => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c])); }

  global.renderWaterfall = renderWaterfall;
})(typeof window !== 'undefined' ? window : globalThis);
