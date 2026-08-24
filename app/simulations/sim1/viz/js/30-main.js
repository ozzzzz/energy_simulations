/* Wiring: KPI tiles, chart panels, ribbons, markers, scrubber and playback. */
'use strict';

(function () {
  if (!SIM.loaded) return;

  const meta = SIM.meta;
  const SPEEDS = [1, 60, 600, 3600];

  const KPI_TILES = [
    { key: 'uptime_pct', label: 'Uptime', fmt: (v) => FMT.pct(v), bad: (v) => v < 99.9 },
    { key: 'served_pct', label: 'IT served', fmt: (v) => FMT.pct(v), bad: (v) => v < 99.9 },
    { key: 'pue_avg', label: 'PUE', fmt: (v) => FMT.num(v, 3) },
    { key: 'it_energy_kwh', label: 'IT energy', fmt: (v) => `${FMT.num(v, 0)} kWh` },
    { key: 'facility_energy_kwh', label: 'Facility', fmt: (v) => `${FMT.num(v, 0)} kWh` },
    { key: 'time_on_battery_s', label: 'On battery', fmt: (v) => FMT.minutes(v), warn: (v) => v > 0 },
    { key: 'battery_min_soc', label: 'Min SoC', fmt: (v) => FMT.pct(v * 100), bad: (v) => v <= 0.06 },
    { key: 'worst_autonomy_s', label: 'Worst autonomy', fmt: (v) => FMT.minutes(v) },
    { key: 'diesel_l', label: 'Diesel', fmt: (v) => `${FMT.num(v, 1)} L`, warn: (v) => v > 0 },
    { key: 'gen_start_failures', label: 'Start failures', fmt: (v) => FMT.num(v, 0), bad: (v) => v > 0 },
    { key: 'throttle_events', label: 'Throttles', fmt: (v) => FMT.num(v, 0), warn: (v) => v > 0 },
    { key: 'shutdown_events', label: 'Shutdowns', fmt: (v) => FMT.num(v, 0), bad: (v) => v > 0 },
    { key: 'overload_seconds', label: 'Overload', fmt: (v) => FMT.minutes(v), warn: (v) => v > 0 },
    { key: 'total_cost_usd', label: 'Total cost', fmt: (v) => `$${FMT.num(v, 0)}` },
  ];

  const PANELS = [
    {
      title: 'Facility power',
      unit: 'kW',
      stack: true,
      yMin: 0,
      series: [
        { key: 'it_drawn_kw', color: PALETTE.it, label: 'IT' },
        { key: 'mech_kw', color: PALETTE.mech, label: 'Cooling' },
        { key: 'loss_kw', color: PALETTE.loss, label: 'Conversion loss' },
      ],
      lines: [{ value: meta.facility_nominal_kw, color: PALETTE.faint, label: 'design nominal' }],
    },
    {
      title: 'Side A vs side B',
      unit: 'kW',
      yMin: 0,
      series: [
        { key: 'a_delivered_kw', color: PALETTE.sideA, label: 'Side A' },
        { key: 'b_delivered_kw', color: PALETTE.sideB, label: 'Side B' },
        { key: 'gen_output_kw', color: PALETTE.gen, label: 'Genset', dash: [5, 4] },
      ],
      lines: [
        { value: meta.ups_rating_kw, color: PALETTE.warn, label: 'UPS nameplate' },
        { value: meta.cord_limit_kw, color: PALETTE.faint, label: 'cord limit' },
      ],
    },
    {
      title: 'Battery state of charge',
      unit: 'frac',
      yMin: 0,
      yMax: 1.02,
      series: [
        { key: 'a_batt_soc', color: PALETTE.sideA, label: 'Battery A' },
        { key: 'b_batt_soc', color: PALETTE.sideB, label: 'Battery B' },
      ],
      lines: [{ value: 0.05, color: PALETTE.bad, label: 'cutoff' }],
    },
    {
      title: 'Remaining autonomy',
      unit: 'min',
      yMin: 0,
      series: [
        { key: 'autonomy_min_a', color: PALETTE.sideA, label: 'Side A' },
        { key: 'autonomy_min_b', color: PALETTE.sideB, label: 'Side B' },
      ],
      note: 'Reported every tick, discharging or not: how long the site would last if the grid dropped now.',
    },
    {
      title: 'Diesel reserve',
      unit: 'L',
      yMin: 0,
      series: [{ key: 'gen_fuel_l', color: PALETTE.gen, label: 'Fuel remaining' }],
      lines: [{ value: meta.fuel_capacity_l, color: PALETTE.faint, label: 'tank' }],
    },
    {
      title: 'Thermal',
      unit: '°C',
      series: [
        { key: 'rack_temp_max_c', color: PALETTE.heat, label: 'Hottest rack' },
        { key: 'rack_temp_mean_c', color: PALETTE.air, label: 'Mean rack' },
        { key: 'cool_loop_supply_c', color: PALETTE.mech, label: 'Loop supply' },
        { key: 'cool_loop_return_c', color: PALETTE.sideA, label: 'Loop return', dash: [4, 3] },
        { key: 'cool_crah_room_c', color: PALETTE.dim, label: 'Room air' },
      ],
      lines: [
        { value: meta.throttle_c, color: PALETTE.warn, label: 'throttle' },
        { value: meta.shutdown_c, color: PALETTE.bad, label: 'shutdown' },
      ],
    },
    {
      title: 'Heat generated vs rejected',
      unit: 'kW',
      yMin: 0,
      series: [
        { key: 'heat_generated_kw', color: PALETTE.heat, label: 'Generated' },
        { key: 'heat_rejected_kw', color: PALETTE.mech, label: 'Rejected' },
        { key: 'cool_rejected_air_kw', color: PALETTE.air, label: 'via air', dash: [4, 3] },
      ],
      lines: [{ value: meta.chiller_capacity_kw, color: PALETTE.warn, label: 'chiller capacity' }],
      note: 'The gap between the two is heat accumulating in the coolant loop.',
    },
    {
      title: 'Instantaneous PUE',
      unit: '',
      series: [{ key: 'pue', color: PALETTE.it, label: 'PUE' }],
      lines: [{ value: meta.pue_nominal, color: PALETTE.faint, label: 'design' }],
      note: 'Gaps are ticks with no IT load at all, where PUE is undefined rather than large.',
    },
    {
      title: 'Unserved IT demand',
      unit: 'kW',
      yMin: 0,
      series: [
        { key: 'it_unserved_kw', color: PALETTE.bad, label: 'Unserved (mean)' },
        { key: 'it_unserved_kw__max', color: PALETTE.warn, label: 'Unserved (peak)', dash: [3, 3] },
      ],
      note: 'Measured against the raw workload ask, so throttling and shutdown both count.',
    },
    {
      title: 'Energy balance residual',
      unit: 'kW',
      series: [{ key: 'balance_residual_kw__max', color: PALETTE.dim, label: 'worst residual in bucket' }],
      note: 'sources − (IT + cooling + losses + charging). Flat at zero means the model conserved energy every tick.',
    },
  ];

  const state = { index: 0, playing: false, speed: 60, simTime: SIM.t[0], last: 0 };
  const panels = [];

  function tile(spec) {
    const value = SIM.kpis[spec.key];
    const node = document.createElement('div');
    node.className = 'kpi';
    if (typeof value === 'number') {
      if (spec.bad && spec.bad(value)) node.classList.add('bad');
      else if (spec.warn && spec.warn(value)) node.classList.add('warn');
    }
    const key = document.createElement('span');
    key.className = 'k';
    key.textContent = spec.label;
    const val = document.createElement('span');
    val.className = 'v';
    val.textContent = typeof value === 'number' ? spec.fmt(value) : value === null || value === undefined ? '—' : String(value);
    node.appendChild(key);
    node.appendChild(val);
    return node;
  }

  function buildKpis() {
    const host = document.getElementById('kpi-tiles');
    for (const spec of KPI_TILES) {
      if (!(spec.key in SIM.kpis)) continue;
      host.appendChild(tile(spec));
    }
  }

  function buildPanels() {
    const host = document.getElementById('charts');
    for (const spec of PANELS) {
      const available = spec.series.filter((entry) => SIM.get(entry.key));
      if (!available.length) continue;
      const resolved = Object.assign({}, spec, { series: available });

      const section = document.createElement('div');
      section.className = 'chart';

      const head = document.createElement('div');
      head.className = 'head';
      const title = document.createElement('h2');
      title.textContent = spec.unit ? `${spec.title} (${spec.unit})` : spec.title;
      const legend = document.createElement('div');
      legend.className = 'legend';
      for (const entry of available) {
        const item = document.createElement('span');
        const swatch = document.createElement('i');
        swatch.style.background = entry.color;
        item.appendChild(swatch);
        item.appendChild(document.createTextNode(entry.label));
        legend.appendChild(item);
      }
      head.appendChild(title);
      head.appendChild(legend);

      const plot = document.createElement('div');
      plot.className = 'plot';
      const base = document.createElement('canvas');
      const overlay = document.createElement('canvas');
      plot.appendChild(base);
      plot.appendChild(overlay);

      const hover = document.createElement('div');
      hover.className = 'hover';

      section.appendChild(head);
      section.appendChild(plot);
      section.appendChild(hover);
      if (spec.note) {
        const note = document.createElement('p');
        note.className = 'note';
        note.textContent = spec.note;
        section.appendChild(note);
      }
      host.appendChild(section);

      const panel = { spec: resolved, base: base, overlay: overlay, hover: hover, scale: null };
      panels.push(panel);

      plot.addEventListener('mousemove', (event) => {
        const rect = plot.getBoundingClientRect();
        const fraction = (event.clientX - rect.left - 52) / Math.max(1, rect.width - 62);
        const span = SIM.t[SIM.n - 1] - SIM.t[0];
        seek(SIM.indexAtTime(SIM.t[0] + Math.max(0, Math.min(1, fraction)) * span));
      });
    }
    redraw();
  }

  function redraw() {
    for (const panel of panels) panel.scale = drawSeries(panel.base, panel.spec);
    paintCursors();
  }

  function paintCursors() {
    for (const panel of panels) {
      drawCursor(panel.overlay, panel.scale, state.index);
      const parts = panel.spec.series.map((entry) => {
        const value = SIM.at(entry.key, state.index);
        return `${entry.label}: ${Number.isFinite(value) ? value.toFixed(2) : '—'}`;
      });
      panel.hover.textContent = parts.join('   ');
    }
  }

  function buildRibbons() {
    const host = document.getElementById('ribbons');
    const rows = [];
    for (const column of Object.keys(SIM.states)) {
      rows.push({ name: FMT.words(column.replace(/_state$/, '').replace(/^cool_/, '')), entry: SIM.states[column] });
    }
    for (const name of SIM.rackNames) {
      const entry = SIM.racks[name] && SIM.racks[name].state;
      if (entry) rows.push({ name: name, entry: entry });
    }
    for (const row of rows) {
      const wrap = document.createElement('div');
      wrap.className = 'ribbon';
      const label = document.createElement('div');
      label.className = 'name';
      label.textContent = row.name;
      const canvas = document.createElement('canvas');
      wrap.appendChild(label);
      wrap.appendChild(canvas);
      host.appendChild(wrap);
      row.canvas = canvas;
    }
    requestAnimationFrame(() => {
      for (const row of rows) drawRibbon(row.canvas, row.entry);
    });
    return rows;
  }

  function buildMarkers() {
    const host = document.getElementById('markers');
    const span = SIM.t[SIM.n - 1] - SIM.t[0] || 1;
    const seen = new Set();
    for (const event of SIM.events) {
      if (event.kind !== 'scheduled') continue;
      const label = event.detail || `${event.component} ${event.to}`;
      const stamp = `${Math.round(event.t)}|${label}`;
      if (seen.has(stamp)) continue;
      seen.add(stamp);
      const marker = document.createElement('button');
      marker.type = 'button';
      marker.className = 'marker';
      marker.textContent = label;
      marker.style.left = `${((event.t - SIM.t[0]) / span) * 100}%`;
      marker.addEventListener('click', () => {
        state.simTime = event.t;
        seek(SIM.indexAtTime(event.t));
      });
      host.appendChild(marker);
    }
  }

  function buildSpeeds() {
    const host = document.getElementById('speeds');
    for (const speed of SPEEDS) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = `${speed}×`;
      button.setAttribute('aria-pressed', String(speed === state.speed));
      button.addEventListener('click', () => {
        state.speed = speed;
        for (const other of host.children) {
          other.setAttribute('aria-pressed', String(other === button));
        }
      });
      host.appendChild(button);
    }
  }

  function seek(index) {
    state.index = Math.max(0, Math.min(SIM.n - 1, index));
    state.simTime = SIM.t[state.index];
    document.getElementById('scrub').value = String(state.index);
    render(0);
  }

  function render(advance) {
    DIAGRAM.update(state.index, advance);
    document.getElementById('readout-clock').textContent = FMT.clock(state.simTime);

    const residual = SIM.at('balance_residual_kw', state.index);
    const readout = document.getElementById('readout-balance');
    const shown = Number.isFinite(residual) ? residual : 0;
    readout.textContent = `in − out = ${shown.toExponential(1)} kW`;
    readout.classList.toggle('bad', Math.abs(shown) > 1e-6);

    document.getElementById('tick-count').textContent =
      `point ${state.index + 1} / ${SIM.n} · ${SIM.ticks} ticks simulated`;
    paintCursors();
  }

  function frame(now) {
    if (state.playing) {
      const elapsed = state.last ? (now - state.last) / 1000 : 0;
      state.last = now;
      state.simTime += elapsed * state.speed;
      const end = SIM.t[SIM.n - 1];
      if (state.simTime >= end) {
        state.simTime = end;
        setPlaying(false);
      }
      state.index = SIM.indexAtTime(state.simTime);
      document.getElementById('scrub').value = String(state.index);
      render(elapsed);
    } else {
      state.last = now;
      render(0);
    }
    requestAnimationFrame(frame);
  }

  function setPlaying(playing) {
    state.playing = playing;
    state.last = 0;
    document.getElementById('play').textContent = playing ? '❚❚' : '▶';
  }

  function boot() {
    document.getElementById('scenario-name').textContent = `sim1 · ${SIM.scenario}`;
    document.getElementById('scenario-description').textContent = SIM.description;
    document.getElementById('model-note').textContent =
      `${meta.n_racks} racks, ${meta.it_nominal_kw} kW nominal / ${meta.it_peak_kw} kW peak IT behind a 2N chain ` +
      `(${meta.ups_rating_kw} kW UPS and ${meta.battery_capacity_kwh} kWh of battery per side, ` +
      `one ${meta.generator_rating_kw} kW genset, ${meta.chiller_capacity_kw} kW liquid and ` +
      `${meta.crah_capacity_kw} kW air cooling). Every tick resolves in four passes: capacity down, demand up, ` +
      `power down, then consume. The only lagged quantity is the coolant loop's stored heat.`;

    const scrub = document.getElementById('scrub');
    scrub.max = String(SIM.n - 1);
    scrub.addEventListener('input', () => seek(Number(scrub.value)));

    document.getElementById('play').addEventListener('click', () => setPlaying(!state.playing));
    window.addEventListener('keydown', (event) => {
      if (event.key === ' ') {
        event.preventDefault();
        setPlaying(!state.playing);
      } else if (event.key === 'ArrowRight') seek(state.index + 1);
      else if (event.key === 'ArrowLeft') seek(state.index - 1);
      else if (event.key === 'Home') seek(0);
      else if (event.key === 'End') seek(SIM.n - 1);
    });

    buildKpis();
    buildSpeeds();
    buildMarkers();
    DIAGRAM.build();
    buildPanels();
    const ribbons = buildRibbons();

    let resizeTimer = 0;
    window.addEventListener('resize', () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        redraw();
        for (const row of ribbons) drawRibbon(row.canvas, row.entry);
      }, 120);
    });

    seek(0);
    requestAnimationFrame(frame);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
