/* Canvas chart renderer.
 *
 * Canvas rather than SVG on purpose: six traces over ~2500 points is 15,000 SVG
 * nodes, and the scrubber goes to treacle. The cursor lives on a second,
 * transparent canvas layered on top, so moving it never repaints the data.
 */
'use strict';

const PALETTE = {
  it: '#38d39f',
  mech: '#35c9d8',
  loss: '#8892a4',
  sideA: '#4aa3ff',
  sideB: '#b48cff',
  gen: '#f0932b',
  batt: '#f5d76e',
  heat: '#ff6b6b',
  air: '#d3c15c',
  ink: '#e6edf3',
  dim: '#93a1b1',
  faint: '#6b7686',
  grid: '#242b36',
  warn: '#ffb020',
  bad: '#ff5c5c',
};

const STATE_COLORS = {
  online: '#38d39f',
  running: '#38d39f',
  on_primary: '#38d39f',
  idle: '#4d5666',
  brownout: '#ffb020',
  overload: '#ffb020',
  throttling: '#ffb020',
  transferring: '#f0932b',
  retransferring: '#f0932b',
  cranking: '#f0932b',
  retry_wait: '#f0932b',
  on_generator: '#f0932b',
  on_battery: '#f5d76e',
  cooldown: '#7c8798',
  bypass: '#c86bff',
  stopped: '#3a424f',
  offline: '#ff5c5c',
  faulted: '#ff5c5c',
  failed: '#ff5c5c',
  tripped: '#ff5c5c',
  overheated: '#ff5c5c',
  emergency_shutdown: '#ff5c5c',
  recovering: '#35c9d8',
  normal: '#38d39f',
};

function stateColor(name) {
  return STATE_COLORS[name] || '#5a6474';
}

function fitCanvas(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || canvas.parentElement.clientWidth || 600;
  const height = canvas.clientHeight || 160;
  canvas.width = Math.max(1, Math.round(width * ratio));
  canvas.height = Math.max(1, Math.round(height * ratio));
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { ctx: ctx, w: width, h: height };
}

const PAD = { left: 52, right: 10, top: 8, bottom: 16 };

function makeScales(spec, w, h) {
  const t = SIM.t;
  const x0 = t[0];
  const x1 = t[SIM.n - 1] || 1;
  let lo = Number.isFinite(spec.yMin) ? spec.yMin : Infinity;
  let hi = Number.isFinite(spec.yMax) ? spec.yMax : -Infinity;

  if (!Number.isFinite(spec.yMin) || !Number.isFinite(spec.yMax)) {
    const consider = (value) => {
      if (!Number.isFinite(value)) return;
      if (!Number.isFinite(spec.yMin) && value < lo) lo = value;
      if (!Number.isFinite(spec.yMax) && value > hi) hi = value;
    };
    if (spec.stack) {
      for (let i = 0; i < SIM.n; i++) {
        let sum = 0;
        for (const entry of spec.series) {
          const source = SIM.get(entry.key);
          if (source && Number.isFinite(source[i])) sum += source[i];
        }
        consider(sum);
      }
      consider(0);
    } else {
      for (const entry of spec.series) {
        const source = SIM.get(entry.key);
        if (!source) continue;
        for (let i = 0; i < SIM.n; i++) consider(source[i]);
      }
    }
    for (const line of spec.lines || []) consider(line.value);
  }

  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi - lo < 1e-9) {
    lo = Number.isFinite(lo) ? lo - 1 : 0;
    hi = Number.isFinite(hi) ? hi + 1 : 1;
  }
  const span = hi - lo;
  hi += span * 0.08;
  if (lo > 0 && lo < span) lo = 0;

  const plotW = w - PAD.left - PAD.right;
  const plotH = h - PAD.top - PAD.bottom;
  return {
    lo: lo,
    hi: hi,
    x: (time) => PAD.left + ((time - x0) / (x1 - x0 || 1)) * plotW,
    y: (value) => PAD.top + plotH - ((value - lo) / (hi - lo)) * plotH,
    plotW: plotW,
    plotH: plotH,
  };
}

function drawFrame(ctx, spec, scale, w, h) {
  ctx.strokeStyle = PALETTE.grid;
  ctx.fillStyle = PALETTE.faint;
  ctx.lineWidth = 1;
  ctx.font = '10px ui-monospace, Menlo, Consolas, monospace';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';

  for (let i = 0; i <= 4; i++) {
    const value = scale.lo + ((scale.hi - scale.lo) * i) / 4;
    const y = Math.round(scale.y(value)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(PAD.left, y);
    ctx.lineTo(w - PAD.right, y);
    ctx.stroke();
    ctx.fillText(formatTick(value, spec.unit), PAD.left - 6, y);
  }

  /* Event windows shaded first, so traces sit on top of them. */
  ctx.fillStyle = 'rgba(255,176,32,0.07)';
  for (const event of SIM.events) {
    if (event.kind !== 'scheduled') continue;
    const x = scale.x(event.t);
    ctx.fillRect(x, PAD.top, 1.5, scale.plotH);
  }

  ctx.textAlign = 'left';
  ctx.fillStyle = PALETTE.faint;
  const first = SIM.t[0];
  const last = SIM.t[SIM.n - 1];
  ctx.fillText(FMT.clock(first), PAD.left, h - 7);
  ctx.textAlign = 'right';
  ctx.fillText(FMT.clock(last), w - PAD.right, h - 7);
}

function formatTick(value, unit) {
  const magnitude = Math.abs(value);
  if (unit === 'frac') return value.toFixed(2);
  if (magnitude >= 1000) return `${(value / 1000).toFixed(1)}k`;
  if (magnitude >= 10) return value.toFixed(0);
  return value.toFixed(magnitude < 1 ? 2 : 1);
}

function drawStack(ctx, spec, scale) {
  const totals = new Float64Array(SIM.n);
  for (const entry of spec.series) {
    const source = SIM.get(entry.key);
    if (!source) continue;
    ctx.beginPath();
    for (let i = 0; i < SIM.n; i++) {
      const value = Number.isFinite(source[i]) ? source[i] : 0;
      const y = scale.y(totals[i] + value);
      if (i === 0) ctx.moveTo(scale.x(SIM.t[i]), y);
      else ctx.lineTo(scale.x(SIM.t[i]), y);
    }
    for (let i = SIM.n - 1; i >= 0; i--) {
      ctx.lineTo(scale.x(SIM.t[i]), scale.y(totals[i]));
    }
    ctx.closePath();
    ctx.fillStyle = entry.color + '55';
    ctx.fill();
    ctx.strokeStyle = entry.color;
    ctx.lineWidth = 1;
    ctx.stroke();
    for (let i = 0; i < SIM.n; i++) {
      totals[i] += Number.isFinite(source[i]) ? source[i] : 0;
    }
  }
}

function drawLine(ctx, source, scale, color, width, dash) {
  ctx.beginPath();
  ctx.strokeStyle = color;
  ctx.lineWidth = width || 1.4;
  ctx.setLineDash(dash || []);
  let open = false;
  for (let i = 0; i < SIM.n; i++) {
    const value = source[i];
    if (!Number.isFinite(value)) {
      open = false;
      continue;
    }
    const x = scale.x(SIM.t[i]);
    const y = scale.y(value);
    if (!open) {
      ctx.moveTo(x, y);
      open = true;
    } else {
      ctx.lineTo(x, y);
    }
  }
  ctx.stroke();
  ctx.setLineDash([]);
}

function drawSeries(canvas, spec) {
  const { ctx, w, h } = fitCanvas(canvas);
  const scale = makeScales(spec, w, h);
  drawFrame(ctx, spec, scale, w, h);

  if (spec.stack) drawStack(ctx, spec, scale);

  for (const entry of spec.series) {
    if (spec.stack && !entry.overlay) continue;
    const source = SIM.get(entry.key);
    if (!source) continue;
    drawLine(ctx, source, scale, entry.color, entry.width, entry.dash);
  }

  for (const line of spec.lines || []) {
    if (!Number.isFinite(line.value)) continue;
    const y = Math.round(scale.y(line.value)) + 0.5;
    ctx.beginPath();
    ctx.strokeStyle = line.color;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.moveTo(PAD.left, y);
    ctx.lineTo(w - PAD.right, y);
    ctx.stroke();
    ctx.setLineDash([]);
    if (line.label) {
      ctx.fillStyle = line.color;
      ctx.font = '9.5px ui-sans-serif, sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillText(line.label, PAD.left + 4, y - 2);
    }
  }
  return scale;
}

function drawCursor(canvas, scale, index) {
  const { ctx, w, h } = fitCanvas(canvas);
  if (!scale || index < 0) return;
  const x = Math.round(scale.x(SIM.t[index])) + 0.5;
  ctx.strokeStyle = 'rgba(230,237,243,0.45)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, PAD.top);
  ctx.lineTo(x, h - PAD.bottom);
  ctx.stroke();
  void w;
}

function drawRibbon(canvas, entry) {
  const { ctx, w, h } = fitCanvas(canvas);
  if (!entry) return;
  const t = SIM.t;
  const span = (t[SIM.n - 1] - t[0]) || 1;
  for (let i = 0; i < SIM.n; i++) {
    const x0 = ((t[i] - t[0]) / span) * w;
    const x1 = i + 1 < SIM.n ? ((t[i + 1] - t[0]) / span) * w : w;
    ctx.fillStyle = stateColor(entry.codes[entry.v[i]]);
    ctx.fillRect(x0, 0, Math.max(1, x1 - x0), h);
  }
}
