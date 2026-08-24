/* Data layer. No DOM access lives here.
 *
 * Dequantizes the embedded payload into typed arrays once, derives the series
 * that were deliberately left out of the payload, and exposes lookups the render
 * layers need. Keeping this separate is what lets the chart and diagram code stay
 * about drawing.
 */
'use strict';

const SIM = (function () {
  const node = document.getElementById('sim-data');
  let raw = null;
  try {
    const text = node ? node.textContent.trim() : '';
    raw = text ? JSON.parse(text) : null;
  } catch (err) {
    console.error('sim1: could not parse embedded payload', err);
    raw = null;
  }

  const empty = {
    loaded: false,
    n: 0,
    t: new Float64Array(0),
    series: {},
    states: {},
    racks: {},
    rackNames: [],
    events: [],
    kpis: {},
    meta: {},
    labels: {},
  };
  if (!raw || !raw.t || !raw.t.length) return empty;

  /* {scale, v:[int|null]} -> Float64Array with NaN for gaps. NaN, not 0: a gap
   * is "undefined here" (PUE with no IT load, autonomy with no discharge), and
   * drawing it as zero would invent a data point. */
  function dequantize(entry) {
    const out = new Float64Array(entry.v.length);
    const scale = entry.scale;
    for (let i = 0; i < entry.v.length; i++) {
      const value = entry.v[i];
      out[i] = value === null ? NaN : value * scale;
    }
    return out;
  }

  const t = Float64Array.from(raw.t);
  const n = t.length;
  const series = {};
  for (const key of Object.keys(raw.series)) series[key] = dequantize(raw.series[key]);

  const racks = {};
  for (const name of Object.keys(raw.racks || {})) {
    const source = raw.racks[name];
    const entry = { state: source.state };
    for (const key of Object.keys(source)) {
      if (key === 'state') continue;
      entry[key] = dequantize(source[key]);
    }
    racks[name] = entry;
  }

  function combine(a, b, fn) {
    const left = series[a];
    const right = series[b];
    if (!left || !right) return null;
    const out = new Float64Array(n);
    for (let i = 0; i < n; i++) out[i] = fn(left[i], right[i]);
    return out;
  }

  function cumulative(source) {
    const out = new Float64Array(n);
    let total = 0;
    for (let i = 0; i < n; i++) {
      const value = source[i];
      total += Number.isFinite(value) ? value : 0;
      out[i] = total;
    }
    return out;
  }

  /* Derived rather than shipped: every one of these is a pure function of the
   * primitives above, and leaving them out of the payload is a third of its size. */
  const derived = {
    grid_kw: combine('a_grid_kw', 'b_grid_kw', (x, y) => x + y),
    batt_out_total_kw: combine('a_batt_out_kw', 'b_batt_out_kw', (x, y) => x + y),
    heat_generated_kw: combine('cool_liquid_heat_kw', 'cool_air_heat_kw', (x, y) => x + y),
    heat_rejected_kw: combine('cool_rejected_liquid_kw', 'cool_rejected_air_kw', (x, y) => x + y),
    autonomy_min_a: series.a_autonomy_s ? scaled(series.a_autonomy_s, 1 / 60) : null,
    autonomy_min_b: series.b_autonomy_s ? scaled(series.b_autonomy_s, 1 / 60) : null,
  };
  if (series.gen_fuel_l) {
    const capacity = (raw.meta && raw.meta.fuel_capacity_l) || 0;
    derived.fuel_used_l = scaled(series.gen_fuel_l, -1, capacity);
  }
  for (const key of Object.keys(derived)) {
    if (derived[key]) series[key] = derived[key];
  }

  function scaled(source, factor, offset) {
    const base = offset === undefined ? 0 : offset;
    const out = new Float64Array(source.length);
    for (let i = 0; i < source.length; i++) out[i] = base + source[i] * factor;
    return out;
  }

  /* Binary search: the timebase is non-uniform, so index cannot be computed. */
  function indexAtTime(time) {
    let low = 0;
    let high = n - 1;
    if (time <= t[0]) return 0;
    if (time >= t[high]) return high;
    while (low + 1 < high) {
      const mid = (low + high) >> 1;
      if (t[mid] <= time) low = mid;
      else high = mid;
    }
    return low;
  }

  function get(name) {
    return series[name] || null;
  }

  function at(name, index) {
    const source = series[name];
    if (!source) return NaN;
    return source[Math.max(0, Math.min(n - 1, index))];
  }

  function stateAt(name, index) {
    const entry = raw.states[name];
    if (!entry) return '';
    const code = entry.v[Math.max(0, Math.min(n - 1, index))];
    return entry.codes[code] || '';
  }

  function rackStateAt(rack, index) {
    const entry = racks[rack] && racks[rack].state;
    if (!entry) return '';
    const clamped = Math.max(0, Math.min(entry.v.length - 1, index));
    return entry.codes[entry.v[clamped]] || '';
  }

  function rackAt(rack, key, index) {
    const source = racks[rack] && racks[rack][key];
    if (!source) return NaN;
    return source[Math.max(0, Math.min(source.length - 1, index))];
  }

  return {
    loaded: true,
    raw: raw,
    n: n,
    t: t,
    series: series,
    states: raw.states,
    racks: racks,
    rackNames: raw.rack_names || Object.keys(racks),
    events: raw.events || [],
    kpis: raw.kpis || {},
    meta: raw.meta || {},
    labels: raw.labels || {},
    duration: raw.duration_s,
    scenario: raw.scenario,
    description: raw.description,
    ticks: raw.ticks,
    get: get,
    at: at,
    stateAt: stateAt,
    rackAt: rackAt,
    rackStateAt: rackStateAt,
    indexAtTime: indexAtTime,
    cumulative: cumulative,
  };
})();

const FMT = {
  clock(seconds) {
    const total = Math.max(0, Math.round(seconds));
    const days = Math.floor(total / 86400);
    const hh = String(Math.floor((total % 86400) / 3600)).padStart(2, '0');
    const mm = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
    const ss = String(total % 60).padStart(2, '0');
    return (days > 0 ? `d${days + 1} ` : '') + `${hh}:${mm}:${ss}`;
  },
  kw(value) {
    if (!Number.isFinite(value)) return '—';
    return `${value.toFixed(value < 100 ? 1 : 0)} kW`;
  },
  num(value, digits) {
    if (!Number.isFinite(value)) return '—';
    return value.toFixed(digits === undefined ? 1 : digits);
  },
  pct(value) {
    return Number.isFinite(value) ? `${value.toFixed(1)} %` : '—';
  },
  degrees(value) {
    return Number.isFinite(value) ? `${value.toFixed(1)} °C` : '—';
  },
  minutes(seconds) {
    if (!Number.isFinite(seconds)) return '—';
    if (seconds >= 5400) return `${(seconds / 3600).toFixed(1)} h`;
    return `${(seconds / 60).toFixed(1)} min`;
  },
  words(text) {
    return String(text || '').replace(/_/g, ' ');
  },
};
