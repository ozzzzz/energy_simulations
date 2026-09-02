/* The flow diagram.
 *
 * Topology is static data. The SVG is built once at init; per-tick updates touch
 * only stroke-width, stroke-dashoffset, class and textContent. No innerHTML, no
 * re-render — that is what keeps the scrubber smooth at 600x.
 */
'use strict';

const DIAGRAM = (function () {
  const NS = 'http://www.w3.org/2000/svg';
  const REF_KW = 800;

  const svg = document.getElementById('diagram');
  const nodes = new Map();
  const edges = [];
  const nodeEls = new Map();
  const edgeEls = new Map();
  let dashPhase = 0;

  function el(name, attrs) {
    const node = document.createElementNS(NS, name);
    for (const key of Object.keys(attrs || {})) node.setAttribute(key, attrs[key]);
    return node;
  }

  function port(node, side) {
    if (side === 'e') return { x: node.x + node.w, y: node.y + node.h / 2 };
    if (side === 'w') return { x: node.x, y: node.y + node.h / 2 };
    if (side === 'n') return { x: node.x + node.w / 2, y: node.y };
    return { x: node.x + node.w / 2, y: node.y + node.h };
  }

  /* Orthogonal router with a single elbow. Enough for a hand-placed layout, and
   * it keeps every wire readable at a glance. */
  function route(a, aSide, b, bSide) {
    const p0 = port(a, aSide);
    const p1 = port(b, bSide);
    const horizontal = (side) => side === 'e' || side === 'w';
    if (horizontal(aSide) && horizontal(bSide)) {
      const mx = (p0.x + p1.x) / 2;
      return `M${p0.x} ${p0.y} L${mx} ${p0.y} L${mx} ${p1.y} L${p1.x} ${p1.y}`;
    }
    if (!horizontal(aSide) && !horizontal(bSide)) {
      const my = (p0.y + p1.y) / 2;
      return `M${p0.x} ${p0.y} L${p0.x} ${my} L${p1.x} ${my} L${p1.x} ${p1.y}`;
    }
    if (horizontal(aSide)) return `M${p0.x} ${p0.y} L${p1.x} ${p0.y} L${p1.x} ${p1.y}`;
    return `M${p0.x} ${p0.y} L${p0.x} ${p1.y} L${p1.x} ${p1.y}`;
  }

  function addNode(spec) {
    nodes.set(spec.id, spec);
    return spec;
  }

  function addEdge(spec) {
    edges.push(spec);
  }

  function layout() {
    const meta = SIM.meta || {};
    const rackNames = SIM.rackNames.length ? SIM.rackNames : ['rack-1'];
    const COLS = { grid: 16, tx: 168, ats: 320, ups: 472, pdu: 624 };
    const W = 112;
    const H = 54;
    const rowA = 36;
    const rowB = 196;

    for (const [side, row, cls] of [
      ['a', rowA, 'side-a'],
      ['b', rowB, 'side-b'],
    ]) {
      const upper = side.toUpperCase();
      addNode({
        id: `grid_${side}`,
        x: COLS.grid,
        y: row,
        w: W,
        h: H,
        cls: cls,
        title: `UTILITY ${upper}`,
        state: `${side}_grid_state`,
        value: (i) => FMT.kw(SIM.at(`${side}_grid_kw`, i)),
      });
      addNode({
        id: `tx_${side}`,
        x: COLS.tx,
        y: row,
        w: W,
        h: H,
        cls: cls,
        title: `TRANSFORMER ${upper}`,
        state: `${side}_tx_state`,
        value: (i) => `${FMT.num(SIM.at(`${side}_tx_load_pct`, i))}% · ${FMT.degrees(SIM.at(`${side}_tx_winding_c`, i))}`,
      });
      addNode({
        id: `ats_${side}`,
        x: COLS.ats,
        y: row,
        w: W,
        h: H,
        cls: cls,
        title: `ATS ${upper}`,
        state: `${side}_ats_state`,
        value: () => '',
      });
      addNode({
        id: `ups_${side}`,
        x: COLS.ups,
        y: row,
        w: W,
        h: H,
        cls: cls,
        title: `UPS ${upper}`,
        state: `${side}_ups_state`,
        value: (i) => `${FMT.kw(SIM.at(`${side}_ups_output_kw`, i))} · ${FMT.num(SIM.at(`${side}_ups_load_pct`, i))}%`,
        alarms: [{ key: `${side}_ups_load_pct`, limit: 100 }],
      });
      addNode({
        id: `batt_${side}`,
        x: COLS.ups,
        y: row + 68,
        w: W,
        h: 48,
        cls: cls,
        title: `BATTERY ${upper}`,
        gauge: `${side}_batt_soc`,
        value: (i) =>
          `${FMT.num(SIM.at(`${side}_batt_soc`, i) * 100, 0)}% · ${FMT.minutes(SIM.at(`${side}_autonomy_s`, i))}`,
      });
      addNode({
        id: `pdu_${side}`,
        x: COLS.pdu,
        y: row,
        w: W,
        h: H,
        cls: cls,
        title: `PDU ${upper}`,
        value: (i) => FMT.kw(SIM.at(`${side}_delivered_kw`, i)),
      });
    }

    addNode({
      id: 'fuel',
      x: COLS.tx,
      y: 112,
      w: W,
      h: 64,
      cls: 'gen',
      title: 'FUEL TANK',
      gauge: 'gen_fuel_frac',
      value: (i) => `${FMT.num(SIM.at('gen_fuel_l', i), 0)} L`,
    });
    addNode({
      id: 'gen',
      x: COLS.ats,
      y: 112,
      w: W,
      h: 64,
      cls: 'gen',
      title: 'DIESEL GENSET',
      state: 'gen_state',
      value: (i) => `${FMT.kw(SIM.at('gen_output_kw', i))} · ${FMT.num(SIM.at('gen_fuel_rate_l_per_h', i), 0)} L/h`,
    });

    addNode({ id: 'bus', x: 756, y: 16, w: 14, h: 280, cls: 'rack', title: '', bare: true });

    const rackH = Math.min(52, Math.floor(268 / rackNames.length) - 6);
    rackNames.forEach((name, index) => {
      addNode({
        id: `rack_${index}`,
        rack: name,
        x: 800,
        y: 16 + index * (rackH + 6),
        w: 152,
        h: rackH,
        cls: 'rack',
        title: `${name.toUpperCase()} · ${((meta.rack_segments || {})[name] || '').slice(0, 5).toUpperCase()}`,
        rackState: name,
        value: (i) => `${FMT.kw(SIM.rackAt(name, 'drawn_kw', i))} · ${FMT.degrees(SIM.rackAt(name, 'temp_c', i))}`,
      });
    });

    const users = SIM.rackNames.filter((name) => (meta.rack_segments || {})[name] === 'interactive');
    if (users.length) {
      addNode({
        id: 'users',
        x: 978,
        y: 16,
        w: 152,
        h: 76,
        cls: 'user',
        title: 'USERS',
        value: (i) => {
          const util = SIM.at('user_utilisation_pct', i);
          const load = Number.isFinite(util) ? `${FMT.num(util, 0)}% util` : 'saturated';
          return `${FMT.num(SIM.at('user_offered_rps', i))} rps · ${load}`;
        },
        subtitle: (i) => `capacity ${FMT.num(SIM.at('user_capacity_rps', i), 0)} rps`,
        // Alarms on dropped traffic rather than on utilisation, because the
        // worst case — zero capacity — has no utilisation figure at all.
        alarms: [
          { key: 'user_dropped_rps', limit: 0.01 },
          { key: 'user_utilisation_pct', limit: 100 },
        ],
      });
      addNode({
        id: 'queue',
        x: 978,
        y: 104,
        w: 152,
        h: 76,
        cls: 'user',
        title: 'QUEUE',
        value: (i) => `${FMT.num(SIM.at('user_queued_requests', i), 0)} waiting`,
        subtitle: (i) => `${FMT.num(SIM.at('user_queue_latency_s', i), 1)} s of ${FMT.num(meta.slo_latency_s, 0)} s`,
        alarms: [{ key: 'user_queue_latency_s', limit: (meta.slo_latency_s || 8) * 0.75 }],
      });
      addNode({
        id: 'drops',
        x: 978,
        y: 192,
        w: 152,
        h: 76,
        cls: 'drop',
        title: 'DROPPED',
        value: (i) => `${FMT.num(SIM.at('user_dropped_rps', i))} rps`,
        subtitle: (i) => {
          const offered = SIM.at('user_offered_rps', i);
          const dropped = SIM.at('user_dropped_rps', i);
          return offered > 0 ? `${FMT.num((dropped / offered) * 100, 1)}% of offered` : 'none offered';
        },
        alarms: [{ key: 'user_dropped_rps', limit: 0.01 }],
      });
    }

    addNode({
      id: 'heat',
      x: 800,
      y: 312,
      w: 152,
      h: 44,
      cls: 'cool',
      title: 'RACK HEAT',
      value: (i) => FMT.kw(SIM.at('heat_generated_kw', i)),
    });
    addNode({
      id: 'cool',
      x: COLS.pdu,
      y: 372,
      w: W,
      h: 62,
      cls: 'cool',
      title: 'COOLING PLANT',
      value: (i) =>
        `${FMT.kw(SIM.at('mech_kw', i))} · ${FMT.num((SIM.at('mech_kw', i) / (SIM.at('it_drawn_kw', i) || 1)) * 100, 0)}% of IT`,
    });
    addNode({
      id: 'cdu',
      x: COLS.ups,
      y: 372,
      w: W,
      h: 62,
      cls: 'cool',
      title: 'CDU / PUMPS',
      state: 'cool_cdu_state',
      value: (i) => FMT.kw(SIM.at('cool_liquid_heat_kw', i)),
    });
    addNode({
      id: 'loop',
      x: COLS.ats,
      y: 372,
      w: W,
      h: 62,
      cls: 'cool',
      title: 'COOLANT LOOP',
      value: (i) =>
        `${FMT.degrees(SIM.at('cool_loop_supply_c', i))} → ${FMT.degrees(SIM.at('cool_loop_return_c', i))}`,
      alarms: [{ key: 'cool_loop_supply_c', limit: 30 }],
    });
    addNode({
      id: 'chiller',
      x: COLS.tx,
      y: 372,
      w: W,
      h: 62,
      cls: 'cool',
      title: 'CHILLER',
      state: 'cool_chiller_state',
      value: (i) =>
        `${FMT.kw(SIM.at('cool_rejected_liquid_kw', i))} / ${FMT.kw(meta.chiller_capacity_kw)}`,
    });
    addNode({
      id: 'reject',
      x: COLS.grid,
      y: 372,
      w: W,
      h: 62,
      cls: 'cool',
      title: 'REJECTED (LIQUID)',
      value: (i) => FMT.kw(SIM.at('cool_rejected_liquid_kw', i)),
    });
    addNode({
      id: 'room',
      x: COLS.pdu,
      y: 470,
      w: W,
      h: 56,
      cls: 'cool',
      title: 'ROOM AIR',
      value: (i) => FMT.degrees(SIM.at('cool_crah_room_c', i)),
    });
    addNode({
      id: 'crah',
      x: COLS.ups,
      y: 470,
      w: W,
      h: 56,
      cls: 'cool',
      title: 'CRAH',
      state: 'cool_crah_state',
      value: (i) => FMT.kw(SIM.at('cool_rejected_air_kw', i)),
    });
    addNode({
      id: 'reject_air',
      x: COLS.grid,
      y: 470,
      w: W,
      h: 56,
      cls: 'cool',
      title: 'REJECTED (AIR)',
      value: (i) => FMT.kw(SIM.at('cool_rejected_air_kw', i)),
    });

    for (const side of ['a', 'b']) {
      const color = side === 'a' ? PALETTE.sideA : PALETTE.sideB;
      addEdge({ from: `grid_${side}`, a: 'e', to: `tx_${side}`, b: 'w', key: `${side}_grid_kw`, color: color });
      addEdge({ from: `tx_${side}`, a: 'e', to: `ats_${side}`, b: 'w', key: `${side}_grid_kw`, color: color });
      addEdge({
        from: `ats_${side}`,
        a: 'e',
        to: `ups_${side}`,
        b: 'w',
        keys: [`${side}_grid_kw`, `${side}_generator_kw`],
        color: color,
      });
      addEdge({ from: `ups_${side}`, a: 'e', to: `pdu_${side}`, b: 'w', key: `${side}_ups_output_kw`, color: color });
      addEdge({ from: `pdu_${side}`, a: 'e', to: 'bus', b: 'w', key: `${side}_delivered_kw`, color: color });
      addEdge({
        from: `batt_${side}`,
        a: 'e',
        to: `ups_${side}`,
        b: 'e',
        key: `${side}_batt_out_kw`,
        color: PALETTE.batt,
      });
      addEdge({
        from: `ups_${side}`,
        a: 's',
        to: `batt_${side}`,
        b: 'n',
        key: `${side}_batt_charge_kw`,
        color: PALETTE.batt,
      });
      addEdge({
        from: 'gen',
        a: side === 'a' ? 'n' : 's',
        to: `ats_${side}`,
        b: side === 'a' ? 's' : 'n',
        key: `${side}_generator_kw`,
        color: PALETTE.gen,
      });
    }
    addEdge({ from: 'fuel', a: 'e', to: 'gen', b: 'w', key: 'gen_fuel_rate_l_per_h', color: PALETTE.gen, fixed: 3 });

    SIM.rackNames.forEach((name, index) => {
      addEdge({ from: 'bus', a: 'e', to: `rack_${index}`, b: 'w', rack: [name, 'drawn_kw'], color: PALETTE.it });
      addEdge({ from: `rack_${index}`, a: 's', to: 'heat', b: 'n', rack: [name, 'removed_kw'], color: PALETTE.heat });
    });

    if (users.length) {
      const refRps = meta.capacity_rps || meta.peak_rps || 1;
      addEdge({ from: 'users', a: 'w', to: 'queue', b: 'n', key: 'user_offered_rps', color: PALETTE.it, ref: refRps });
      addEdge({ from: 'queue', a: 's', to: 'drops', b: 'n', key: 'user_dropped_rps', color: PALETTE.bad, ref: refRps });
      for (const name of users) {
        const index = SIM.rackNames.indexOf(name);
        addEdge({
          from: 'queue',
          a: 'w',
          to: `rack_${index}`,
          b: 'e',
          // Served work is split across the racks serving it, so each strand
          // carries its own share rather than the site total.
          scale: 1 / users.length,
          key: 'user_served_rps',
          color: PALETTE.it,
          ref: refRps / users.length,
        });
      }
    }

    addEdge({ from: 'bus', a: 's', to: 'cool', b: 'e', key: 'mech_kw', color: PALETTE.mech });
    addEdge({ from: 'heat', a: 'w', to: 'cdu', b: 'e', key: 'cool_liquid_heat_kw', color: PALETTE.heat });
    addEdge({ from: 'cdu', a: 'w', to: 'loop', b: 'e', key: 'cool_liquid_heat_kw', color: PALETTE.heat });
    addEdge({ from: 'loop', a: 'w', to: 'chiller', b: 'e', key: 'cool_rejected_liquid_kw', color: PALETTE.mech });
    addEdge({ from: 'chiller', a: 'w', to: 'reject', b: 'e', key: 'cool_rejected_liquid_kw', color: PALETTE.mech });
    addEdge({ from: 'heat', a: 's', to: 'room', b: 'e', key: 'cool_air_heat_kw', color: PALETTE.air });
    addEdge({ from: 'room', a: 'w', to: 'crah', b: 'e', key: 'cool_rejected_air_kw', color: PALETTE.air });
    addEdge({ from: 'crah', a: 'w', to: 'reject_air', b: 'e', key: 'cool_rejected_air_kw', color: PALETTE.air });
  }

  function build() {
    if (!svg) return;
    layout();

    const edgeLayer = el('g', { class: 'edges' });
    const nodeLayer = el('g', { class: 'nodes' });
    svg.appendChild(edgeLayer);
    svg.appendChild(nodeLayer);

    edges.forEach((edge, index) => {
      const from = nodes.get(edge.from);
      const to = nodes.get(edge.to);
      if (!from || !to) return;
      const path = el('path', {
        class: 'edge',
        d: route(from, edge.a, to, edge.b),
        stroke: edge.color,
        'stroke-width': '1.2',
      });
      edgeLayer.appendChild(path);
      edgeEls.set(index, { el: path, spec: edge });
    });

    for (const spec of nodes.values()) {
      const group = el('g', { class: `node ${spec.cls || ''}` });
      group.appendChild(
        el('rect', { x: spec.x, y: spec.y, width: spec.w, height: spec.h, rx: spec.bare ? 4 : 7 })
      );
      const parts = { group: group };
      if (!spec.bare) {
        const title = el('text', { class: 'title', x: spec.x + 9, y: spec.y + 16 });
        title.textContent = spec.title;
        group.appendChild(title);

        // Only for nodes that actually have a state. An empty text element here
        // is invisible but sits exactly where a gauge goes, so creating one
        // unconditionally left a collision waiting for whoever added a state next.
        if (spec.state || spec.rackState) {
          parts.state = el('text', { class: 'state', x: spec.x + 9, y: spec.y + spec.h - 20 });
          group.appendChild(parts.state);
        }

        parts.value = el('text', { class: 'value', x: spec.x + 9, y: spec.y + spec.h - 7 });
        group.appendChild(parts.value);

        if (spec.subtitle) {
          parts.subtitle = el('text', { class: 'state', x: spec.x + 9, y: spec.y + 30 });
          group.appendChild(parts.subtitle);
        }

        if (spec.gauge) {
          const gauge = el('g', { class: 'gauge' });
          // Anchored under the title rather than above the bottom edge, so it
          // stays clear of the text whatever height the node is.
          const gaugeY = spec.y + 23;
          gauge.appendChild(el('rect', { x: spec.x + 9, y: gaugeY, width: spec.w - 18, height: 5, rx: 2 }));
          parts.gaugeFill = el('rect', {
            class: 'fill',
            x: spec.x + 9,
            y: gaugeY,
            width: 0,
            height: 5,
            rx: 2,
          });
          gauge.appendChild(parts.gaugeFill);
          parts.gauge = gauge;
          group.appendChild(gauge);
        }
      }
      nodeLayer.appendChild(group);
      nodeEls.set(spec.id, parts);
    }
  }

  function flowFor(spec, index) {
    const scale = spec.scale === undefined ? 1 : spec.scale;
    if (spec.rack) return SIM.rackAt(spec.rack[0], spec.rack[1], index) * scale;
    if (spec.keys) {
      let total = 0;
      for (const key of spec.keys) {
        const value = SIM.at(key, index);
        if (Number.isFinite(value)) total += value;
      }
      return total * scale;
    }
    return SIM.at(spec.key, index) * scale;
  }

  function update(index, advance) {
    if (!svg) return;
    dashPhase = (dashPhase + (advance || 0)) % 1e6;

    for (const [, entry] of edgeEls) {
      const flow = flowFor(entry.spec, index);
      const reference = entry.spec.ref || REF_KW;
      // Threshold scales with the edge's own units: 0.05 kW is noise on a busbar
      // but 0.05 rps is a real trickle of traffic.
      const live = Number.isFinite(flow) && flow > reference * 6e-5;
      entry.el.classList.toggle('dead', !live);
      if (!live) {
        entry.el.setAttribute('stroke-dashoffset', '0');
        continue;
      }
      /* Square root, not linear: a 20 kW pump flow still has to be visible next
       * to a 700 kW busbar. */
      const width = entry.spec.fixed || 2 + 10 * Math.sqrt(Math.min(1, flow / reference));
      entry.el.setAttribute('stroke-width', width.toFixed(2));
      const speed = entry.spec.fixed ? 20 : 12 + 90 * Math.min(1, flow / reference);
      entry.el.setAttribute('stroke-dashoffset', (-dashPhase * speed).toFixed(1));
    }

    for (const spec of nodes.values()) {
      const parts = nodeEls.get(spec.id);
      if (!parts || spec.bare) continue;

      let state = '';
      if (spec.state) state = SIM.stateAt(spec.state, index);
      else if (spec.rackState) state = SIM.rackStateAt(spec.rackState, index);
      if (parts.state) parts.state.textContent = FMT.words(state);

      if (parts.value && spec.value) parts.value.textContent = spec.value(index);
      if (parts.subtitle && spec.subtitle) parts.subtitle.textContent = spec.subtitle(index);

      const group = parts.group;
      group.classList.remove('down', 'alarm', 'idle');
      if (['offline', 'failed', 'faulted', 'tripped', 'overheated', 'emergency_shutdown'].includes(state)) {
        group.classList.add('down');
      } else if (['bypass', 'brownout', 'overload', 'throttling', 'cranking', 'retry_wait'].includes(state)) {
        group.classList.add('alarm');
      } else if (state === 'stopped' || state === 'idle') {
        group.classList.add('idle');
      } else if (spec.alarms) {
        for (const alarm of spec.alarms) {
          const value = SIM.at(alarm.key, index);
          if (Number.isFinite(value) && value > alarm.limit) {
            group.classList.add('alarm');
            break;
          }
        }
      }

      if (parts.gaugeFill) {
        const fraction = gaugeFraction(spec, index);
        parts.gaugeFill.setAttribute('width', ((spec.w - 18) * Math.max(0, Math.min(1, fraction))).toFixed(1));
        parts.gauge.classList.toggle('low', fraction < 0.2);
      }
    }
  }

  function gaugeFraction(spec, index) {
    if (spec.gauge === 'gen_fuel_frac') {
      const capacity = (SIM.meta && SIM.meta.fuel_capacity_l) || 1;
      return SIM.at('gen_fuel_l', index) / capacity;
    }
    return SIM.at(spec.gauge, index);
  }

  return { build: build, update: update };
})();
