/* boothinset.js — per-seat polling-place map.
 *
 * A small MapLibre instance scoped to one electorate, layered as:
 *   1. Dark raster basemap (CartoDB Dark Matter, with labels)
 *   2. Electorate boundary outline (line + soft fill)
 *   3. Booth dots, coloured by winning party
 * plus an inline legend for the parties present in this seat.
 *
 * The map instance is created once and reused on every seat change —
 * we just swap the data on each layer and refit the bounds. This
 * avoids per-click MapLibre teardown/rebuild overhead.
 *
 * The caller passes maplibregl in via opts because the bundle is
 * dynamic-imported once at the page level and shared.
 */
(function () {
  const PARTY_FILL = {
    alp:  '#d4253a',
    lib:  '#2167cb',
    lnp:  '#2167cb',
    clp:  '#2980d4',
    nat:  '#1d7846',
    grn:  '#2da050',
    ind:  '#8e94a0',
    on:   '#ed6f1a',
    kap:  '#9e1a2f',
    ca:   '#d96820',
    ffp:  '#4a4992',
    sff:  '#b3253a',
    fus:  '#c926f2',
    oth:  '#6c6878',
  };
  const PARTY_LABEL = {
    alp: 'Labor',
    lib: 'Liberal',
    lnp: 'LNP',
    clp: 'CLP',
    nat: 'Nationals',
    grn: 'Greens',
    ind: 'Independent',
    on:  'One Nation',
    kap: "Katter's",
    ca:  'Centre Alliance',
    ffp: 'Family First',
    sff: 'Shooters',
    fus: 'Fusion',
    oth: 'Other',
  };

  // Build a MapLibre `match` colour expression keyed off `winnerParty`.
  function fillExpr() {
    const expr = ['match', ['get', 'winnerParty']];
    for (const [k, v] of Object.entries(PARTY_FILL)) expr.push(k, v);
    expr.push('#6c6878');
    return expr;
  }

  function* outerRings(geometry) {
    if (!geometry) return;
    if (geometry.type === 'Polygon') {
      yield geometry.coordinates[0];
    } else if (geometry.type === 'MultiPolygon') {
      for (const poly of geometry.coordinates) yield poly[0];
    }
  }

  function bbox(geometry, booths) {
    let minLng = Infinity, minLat = Infinity, maxLng = -Infinity, maxLat = -Infinity;
    const grow = (lng, lat) => {
      if (lng < minLng) minLng = lng;
      if (lat < minLat) minLat = lat;
      if (lng > maxLng) maxLng = lng;
      if (lat > maxLat) maxLat = lat;
    };
    // Prefer the electorate boundary so postal-vote / interstate / hospital
    // booths well outside the seat don't blow the framing out. Booths fall
    // back as the basis only when no geometry is available.
    if (geometry) {
      for (const ring of outerRings(geometry)) {
        for (const [lng, lat] of ring) grow(lng, lat);
      }
    } else {
      for (const b of booths) {
        if (b.lat != null && b.lng != null) grow(b.lng, b.lat);
      }
    }
    if (minLng === Infinity) return null;
    return [[minLng, minLat], [maxLng, maxLat]];
  }

  function escapeHTML(s) {
    return String(s ?? '').replace(/[&<>"]/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
    ));
  }

  // Lazily-built singletons (one map and one legend per page).
  let _maplibregl = null;
  let _map = null;
  let _mapHost = null;       // the inner div we hand to MapLibre
  let _activeBoothId = null;

  function ensureMap(host, maplibregl) {
    if (_map && host.contains(_mapHost)) return _map;

    host.innerHTML = '';
    const mapDiv = document.createElement('div');
    mapDiv.className = 'booth-inset-map';
    // Inline width/height as a belt-and-braces against any timing
    // window where the parent's explicit height hasn't been applied
    // yet — MapLibre measures the container at construct time and a
    // 0×0 canvas renders blank tiles.
    mapDiv.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;';
    host.appendChild(mapDiv);

    const legend = document.createElement('div');
    legend.className = 'booth-inset-legend';
    legend.id = 'p-booth-inset-legend';
    host.appendChild(legend);

    const attrib = document.createElement('div');
    attrib.className = 'booth-inset-attrib';
    attrib.innerHTML = '© <a href="https://carto.com/attributions" target="_blank" rel="noopener">CARTO</a> · © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OSM</a>';
    host.appendChild(attrib);

    _mapHost = mapDiv;
    _maplibregl = maplibregl;

    // Force a synchronous layout pass before constructing the map so
    // MapLibre measures non-zero dimensions.
    void mapDiv.getBoundingClientRect();

    _map = new maplibregl.Map({
      container: mapDiv,
      style: {
        version: 8,
        sources: {
          basemap: {
            type: 'raster',
            tiles: [
              'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
              'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
              'https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
              'https://d.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
            ],
            tileSize: 256,
            attribution: '© CARTO © OpenStreetMap contributors',
            maxzoom: 19,
          },
          boundary: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
          booths: {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] },
            promoteId: 'boothId',
          },
        },
        layers: [
          { id: 'basemap', type: 'raster', source: 'basemap' },
          {
            id: 'boundary-fill',
            type: 'fill',
            source: 'boundary',
            paint: { 'fill-color': '#ffffff', 'fill-opacity': 0.04 },
          },
          {
            id: 'boundary-line',
            type: 'line',
            source: 'boundary',
            paint: {
              'line-color': '#ffffff',
              'line-width': 1.4,
              'line-opacity': 0.85,
            },
          },
          {
            id: 'booth-dots',
            type: 'circle',
            source: 'booths',
            paint: {
              'circle-color': fillExpr(),
              'circle-radius': [
                'interpolate', ['linear'], ['zoom'],
                9,  3.0,
                12, 4.0,
                14, 5.0,
              ],
              'circle-stroke-color': [
                'case',
                ['boolean', ['feature-state', 'active'], false], '#ffffff',
                'rgba(8, 11, 16, 0.85)',
              ],
              'circle-stroke-width': [
                'case',
                ['boolean', ['feature-state', 'active'], false], 2.0,
                0.7,
              ],
              'circle-opacity': 0.95,
            },
          },
        ],
      },
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      // The inset is small; let users zoom and pan to navigate the
      // urban density of dots in inner-city seats.
      minZoom: 4,
      maxZoom: 16,
      center: [134, -28],
      zoom: 3,
    });

    _map.on('mouseenter', 'booth-dots', () => {
      _map.getCanvas().style.cursor = 'pointer';
    });
    _map.on('mouseleave', 'booth-dots', () => {
      _map.getCanvas().style.cursor = '';
    });
    // Surface MapLibre runtime errors to the console so failed tile
    // loads, style errors, etc. don't fail silently inside the inset.
    _map.on('error', (e) => {
      console.error('[booth-inset]', e && e.error ? e.error : e);
    });

    return _map;
  }

  function buildBoothFeatures(booths) {
    const out = [];
    for (const b of booths) {
      if (b.lat == null || b.lng == null) continue;
      out.push({
        type: 'Feature',
        id: b.boothId,
        properties: {
          boothId: b.boothId,
          name: b.name,
          winnerParty: b.winnerParty || 'oth',
          winnerPct: b.winnerPct,
          formal: b.formal,
        },
        geometry: { type: 'Point', coordinates: [b.lng, b.lat] },
      });
    }
    return { type: 'FeatureCollection', features: out };
  }

  function buildBoundaryFeatures(geometry) {
    if (!geometry) return { type: 'FeatureCollection', features: [] };
    return {
      type: 'FeatureCollection',
      features: [{ type: 'Feature', properties: {}, geometry }],
    };
  }

  function renderLegend(booths) {
    const host = document.getElementById('p-booth-inset-legend');
    if (!host) return;
    const seen = new Set();
    for (const b of booths) {
      const k = b.winnerParty || 'oth';
      if (PARTY_FILL[k]) seen.add(k);
    }
    const entries = [...seen]
      .sort((a, b) => Object.keys(PARTY_FILL).indexOf(a) - Object.keys(PARTY_FILL).indexOf(b));
    if (!entries.length) {
      host.innerHTML = '';
      return;
    }
    host.innerHTML = entries.map(k => `
      <span class="lg-row">
        <span class="lg-dot" style="background:${PARTY_FILL[k]}"></span>
        <span class="lg-name">${escapeHTML(PARTY_LABEL[k] || k.toUpperCase())}</span>
      </span>
    `).join('');
  }

  /**
   * @param {HTMLElement} host
   * @param {object} opts
   * @param {object}  opts.maplibregl
   * @param {object|null} opts.geometry  GeoJSON geometry of the seat boundary.
   * @param {Array}   opts.booths        Booth rows from the seat JSON.
   * @param {(boothId: number) => void} [opts.onSelect]
   */
  function renderBoothInset(host, opts) {
    if (!host || !opts || !opts.maplibregl) return null;
    const map = ensureMap(host, opts.maplibregl);

    const booths = opts.booths || [];
    const boundary = buildBoundaryFeatures(opts.geometry);
    const boothFC = buildBoothFeatures(booths);

    const apply = () => {
      map.getSource('boundary').setData(boundary);
      map.getSource('booths').setData(boothFC);
      const box = bbox(opts.geometry, booths);
      if (box) {
        map.fitBounds(box, { padding: 24, animate: false, maxZoom: 13 });
      }
    };
    if (map.isStyleLoaded()) apply();
    else map.once('load', apply);

    // Wire dot clicks + general map clicks. We re-bind on every render
    // so we capture the current onSelect/onDeselect callbacks.
    if (map._biDotHandler) map.off('click', 'booth-dots', map._biDotHandler);
    if (map._biMapHandler) map.off('click', map._biMapHandler);
    const dotHandler = (e) => {
      if (!e.features || !e.features.length) return;
      e._handledByDot = true;          // mark for the general handler
      opts.onSelect && opts.onSelect(e.features[0].id);
    };
    const mapHandler = (e) => {
      // The dot-layer handler runs first and tags the event. Anything
      // else is a click on the basemap / boundary / empty space and
      // should close the drawer.
      if (e._handledByDot) return;
      const hits = map.queryRenderedFeatures(e.point, { layers: ['booth-dots'] });
      if (hits.length) return;
      opts.onDeselect && opts.onDeselect();
    };
    map._biDotHandler = dotHandler;
    map._biMapHandler = mapHandler;
    map.on('click', 'booth-dots', dotHandler);
    map.on('click', mapHandler);

    renderLegend(booths);

    function setActive(boothId) {
      const next = boothId == null ? null : Number(boothId);
      if (_activeBoothId === next) return;
      if (_activeBoothId != null) {
        map.setFeatureState({ source: 'booths', id: _activeBoothId }, { active: false });
      }
      _activeBoothId = next;
      if (_activeBoothId != null) {
        map.setFeatureState({ source: 'booths', id: _activeBoothId }, { active: true });
      }
    }
    // New seat → drop any prior selection.
    setActive(null);

    // MapLibre needs a resize tick once the host transitions from
    // hidden to visible (the canvas is sized at construct time).
    requestAnimationFrame(() => map.resize());

    return { setActive };
  }

  window.renderBoothInset = renderBoothInset;
})();
