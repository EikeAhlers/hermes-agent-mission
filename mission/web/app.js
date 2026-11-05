// Mission Control — a live, zoomable map of what the agent is doing.
// No dependencies. Reads only from /api/*.

const sky = document.getElementById('sky');
const ctx = sky.getContext('2d');
const timeline = document.getElementById('timeline');
const tctx = timeline.getContext('2d');

const TAU = Math.PI * 2;
const GOLDEN = 2.399963229728653;

let snap = null;
let nodes = [];
let clusters = [];
let hidden = new Set();
let hoverId = null;
let selectedId = null;
let particles = [];
let stars = [];
let cam = { x: 0, y: 0, scale: .55, tx: 0, ty: 0, tscale: .55 };
let dragging = null;
let lastPointer = null;
let dpr = 1;
let view = { w: 0, h: 0 };
let lastTotals = null;
let rate = 0;

const fmt = (n) => n >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(Math.round(n));
const money = (n) => n >= 1 ? '$' + n.toFixed(2) : '$' + n.toFixed(4);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

function hash(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0) / 4294967295;
}

function resize() {
  dpr = Math.min(window.devicePixelRatio || 1, 2);
  view.w = window.innerWidth;
  view.h = window.innerHeight;
  for (const c of [sky, timeline]) {
    const w = c === sky ? view.w : c.clientWidth;
    const h = c === sky ? view.h : 64;
    c.width = Math.max(1, Math.floor(w * dpr));
    c.height = Math.max(1, Math.floor(h * dpr));
  }
  if (!stars.length) {
    for (let i = 0; i < 340; i++) {
      stars.push({ x: Math.random(), y: Math.random(), r: Math.random() * 1.1 + .2, a: Math.random() * .5 + .1 });
    }
  }
  if (snap && !cam.fit) fit();
}

function fit() {
  const span = clusters.reduce((m, c) => Math.max(m, Math.hypot(c.cx, c.cy) + c.r + 120), 320);
  const s = Math.min(view.w / (span * 2.05), view.h / (span * 1.75));
  cam.scale = cam.tscale = clamp(s, .28, 1.35);
  cam.x = cam.tx = 0;
  cam.y = cam.ty = 0;
  cam.fit = true;
}

function layout(data) {
  snap = data;
  const ranked = data.clusters.filter((c) => c.sessions > 0);
  const kept = ranked.slice(0, 10);
  const rest = ranked.slice(10);
  const list = [...kept];
  if (rest.length) {
    list.push({
      label: 'other', colour: '#64748b',
      sessions: rest.reduce((n, c) => n + c.sessions, 0),
      tokens: rest.reduce((n, c) => n + c.tokens, 0),
      folded: new Set(rest.map((c) => c.label)),
    });
  }
  const live_labels = new Set(list.map((c) => c.label));
  const n = Math.max(list.length, 1);
  const RC = n <= 2 ? 250 : 320;
  clusters = list.map((c, i) => {
    const a = -Math.PI / 2 + (i / n) * TAU;
    return { ...c, cx: Math.cos(a) * RC, cy: Math.sin(a) * RC, r: 46 + 34 * Math.sqrt(c.tokens / Math.max(1, list[0].tokens)) };
  });

  const byCluster = new Map(clusters.map((c) => [c.label, []]));
  for (const s of data.sessions) {
    const label = live_labels.has(s.cluster) ? s.cluster : 'other';
    if (!byCluster.has(label)) byCluster.set(label, []);
    byCluster.get(label).push(s);
  }

  const maxTokens = Math.max(1, ...data.sessions.map((s) => s.tokens));
  nodes = [];
  for (const c of clusters) {
    const items = (byCluster.get(c.label) || []).sort((a, b) => b.tokens - a.tokens).slice(0, 60);
    items.forEach((s, j) => {
      const ring = 18 + 10.5 * Math.sqrt(j);
      const a = j * GOLDEN + hash(s.id) * .6;
      nodes.push({
        ...s,
        colour: c.colour,
        x: c.cx + Math.cos(a) * ring,
        y: c.cy + Math.sin(a) * ring,
        cx: c.cx,
        cy: c.cy,
        r: 1.7 + 5.2 * Math.sqrt(s.tokens / maxTokens) + (s.live ? 1.8 : 0),
        tw: hash(s.id) * TAU,
      });
    });
  }

  lastTotals = lastTotals || data.totals;
  updateHud();
  buildLegend();
  drawTimeline();
  if (!cam.fit) fit();
}

function updateHud() {
  const t = snap.totals;
  document.getElementById('m-running').textContent = t.running;
  document.getElementById('m-sessions').textContent = t.sessions;
  document.getElementById('m-tokens').textContent = fmt(t.tokens);
  document.getElementById('m-cost').textContent = money(t.cost);
  document.getElementById('m-tools').textContent = fmt(t.tools);
  document.getElementById('profile').textContent = `${snap.profile} · last ${snap.hours}h`;
  const lastBucket = snap.timeline[snap.timeline.length - 1] || { tokens: 0 };
  rate = Math.round(lastBucket.tokens / 60);
  document.getElementById('rate').textContent = fmt(rate || 0);
}

function buildLegend() {
  const box = document.getElementById('legend');
  box.innerHTML = '';
  for (const c of clusters) {
    const el = document.createElement('div');
    el.className = 'chip' + (hidden.has(c.label) ? '' : ' on');
    el.innerHTML = `<i style="background:${c.colour}"></i>${c.label} <span style="opacity:.6">${c.sessions}</span>`;
    el.onclick = () => {
      hidden.has(c.label) ? hidden.delete(c.label) : hidden.add(c.label);
      buildLegend();
    };
    box.appendChild(el);
  }
}

// ---------- drawing ----------

function toScreen(x, y) {
  return { x: view.w / 2 + (x - cam.x) * cam.scale, y: view.h / 2 + (y - cam.y) * cam.scale };
}

function toWorld(px, py) {
  return { x: (px - view.w / 2) / cam.scale + cam.x, y: (py - view.h / 2) / cam.scale + cam.y };
}

function drawSky(time) {
  const g = ctx.createRadialGradient(view.w * .5, view.h * .48, 40, view.w * .5, view.h * .5, Math.max(view.w, view.h) * .8);
  g.addColorStop(0, '#0a1022');
  g.addColorStop(.55, '#070a15');
  g.addColorStop(1, '#03040a');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, view.w, view.h);

  ctx.save();
  for (const s of stars) {
    const tw = .55 + .45 * Math.sin(time * .0009 + s.x * 30);
    ctx.globalAlpha = s.a * tw;
    ctx.fillStyle = '#cfe4ff';
    ctx.beginPath();
    ctx.arc(s.x * view.w, s.y * view.h, s.r, 0, TAU);
    ctx.fill();
  }
  ctx.restore();
}

function drawNebula() {
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (const c of clusters) {
    if (hidden.has(c.label)) continue;
    const p = toScreen(c.cx, c.cy);
    const rad = c.r * cam.scale * 2.6;
    const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, rad);
    g.addColorStop(0, hexA(c.colour, .10));
    g.addColorStop(.45, hexA(c.colour, .045));
    g.addColorStop(1, hexA(c.colour, 0));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(p.x, p.y, rad, 0, TAU);
    ctx.fill();
  }
  ctx.restore();
}

function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function drawCore(time) {
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const p = toScreen(0, 0);
  const pulse = 1 + .06 * Math.sin(time * .0016);
  for (const [r, a] of [[26, .16], [14, .35], [7, .95]]) {
    const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r * pulse * Math.max(.7, cam.scale));
    g.addColorStop(0, `rgba(186,230,253,${a})`);
    g.addColorStop(1, 'rgba(56,189,248,0)');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(p.x, p.y, r * pulse * Math.max(.7, cam.scale), 0, TAU);
    ctx.fill();
  }
  // orbit ring
  ctx.strokeStyle = 'rgba(125,211,252,.18)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(p.x, p.y, 44 * cam.scale, 0, TAU);
  ctx.stroke();
  ctx.restore();
}

function drawLinks(time) {
  ctx.save();
  for (const c of clusters) {
    if (hidden.has(c.label)) continue;
    const p = toScreen(c.cx, c.cy);
    const o = toScreen(0, 0);
    const g = ctx.createLinearGradient(o.x, o.y, p.x, p.y);
    g.addColorStop(0, hexA(c.colour, .02));
    g.addColorStop(1, hexA(c.colour, .16));
    ctx.strokeStyle = g;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(o.x, o.y);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
  }
  ctx.restore();
}

function drawClusterLabels() {
  ctx.save();
  ctx.textAlign = 'center';
  for (const c of clusters) {
    if (hidden.has(c.label)) continue;
    const p = toScreen(c.cx, c.cy);
    const label = c.label.toUpperCase();
    ctx.font = '700 10px ui-sans-serif, system-ui, sans-serif';
    const wLabel = ctx.measureText(label).width;
    ctx.font = '10px ui-sans-serif, system-ui, sans-serif';
    const wTok = ctx.measureText(fmt(c.tokens) + ' tok').width;
    const w = Math.max(wLabel, wTok) + 18;
    const top = p.y - (c.r * cam.scale + 22);
    ctx.fillStyle = 'rgba(4,6,14,.72)';
    roundRect(ctx, p.x - w / 2, top, w, 34, 8);
    ctx.fill();
    ctx.strokeStyle = hexA(c.colour, .18);
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = 'rgba(226,236,250,.92)';
    ctx.font = '700 10px ui-sans-serif, system-ui, sans-serif';
    ctx.fillText(label, p.x, top + 14);
    ctx.fillStyle = hexA(c.colour, .8);
    ctx.font = '10px ui-sans-serif, system-ui, sans-serif';
    ctx.fillText(fmt(c.tokens) + ' tok', p.x, top + 27);
  }
  ctx.restore();
}

function drawConnections(time) {
  // quiet filaments from a session back to its own cluster core
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  const maxTok = Math.max(1, ...nodes.map((n) => n.tokens));
  for (const n of nodes) {
    if (hidden.has(n.cluster)) continue;
    const weight = n.tokens / maxTok;
    const from = toScreen(n.x, n.y);
    const to = toScreen(n.cx, n.cy);
    const arc = .18 + (n.live ? .16 : 0);
    ctx.strokeStyle = hexA(n.colour, (.05 + .13 * weight) * (.7 + .3 * Math.sin(time * .001 + n.tw)));
    ctx.lineWidth = .6 + weight;
    ctx.beginPath();
    const mx = (from.x + to.x) / 2 + (to.y - from.y) * arc;
    const my = (from.y + to.y) / 2 - (to.x - from.x) * arc;
    ctx.moveTo(from.x, from.y);
    ctx.quadraticCurveTo(mx, my, to.x, to.y);
    ctx.stroke();
  }
  ctx.restore();
}

function drawNodes(time) {
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (const n of nodes) {
    if (hidden.has(n.cluster)) continue;
    const p = toScreen(n.x, n.y);
    const shimmer = .8 + .2 * Math.sin(time * .002 + n.tw);
    const r = Math.max(1.1, n.r * cam.scale * (hoverId === n.id || selectedId === n.id ? 1.8 : 1));
    const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r * 4.2);
    g.addColorStop(0, hexA(n.colour, .95 * shimmer));
    g.addColorStop(.35, hexA(n.colour, .35 * shimmer));
    g.addColorStop(1, hexA(n.colour, 0));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(p.x, p.y, r * 4.2, 0, TAU);
    ctx.fill();

    ctx.fillStyle = n.colour;
    ctx.beginPath();
    ctx.arc(p.x, p.y, Math.max(1, r * .55), 0, TAU);
    ctx.fill();

    if (n.live) {
      const k = (time * .0009 + n.tw) % 1;
      ctx.strokeStyle = hexA(n.colour, .55 * (1 - k));
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(p.x, p.y, r + k * 26, 0, TAU);
      ctx.stroke();
    }
  }
  ctx.restore();

  if (hoverId) {
    const n = nodes.find((x) => x.id === hoverId);
    if (n) {
      const p = toScreen(n.x, n.y);
      const label = n.title.length > 46 ? n.title.slice(0, 44) + '…' : n.title;
      ctx.save();
      ctx.font = '12px ui-sans-serif, system-ui, sans-serif';
      const w = ctx.measureText(label).width + 18;
      ctx.fillStyle = 'rgba(8,12,24,.92)';
      ctx.strokeStyle = 'rgba(125,211,252,.25)';
      roundRect(ctx, p.x - w / 2, p.y - 38, w, 24, 7);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = '#dbeafe';
      ctx.textAlign = 'center';
      ctx.fillText(label, p.x, p.y - 22);
      ctx.restore();
    }
  }
}

function roundRect(c, x, y, w, h, r) {
  c.beginPath();
  c.moveTo(x + r, y);
  c.arcTo(x + w, y, x + w, y + h, r);
  c.arcTo(x + w, y + h, x, y + h, r);
  c.arcTo(x, y + h, x, y, r);
  c.arcTo(x, y, x + w, y, r);
  c.closePath();
}

function stepParticles(dt) {
  for (const n of nodes) {
    if (!n.live || hidden.has(n.cluster)) continue;
    if (Math.random() < dt * 18) {
      particles.push({ x: n.x, y: n.y, t: 0, life: 1.1 + Math.random() * .7, colour: n.colour, speed: .35 + Math.random() * .35 });
    }
  }
  particles = particles.filter((p) => (p.t += dt) < p.life);
}

function drawParticles() {
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (const p of particles) {
    const k = p.t / p.life;
    const x = p.x * (1 - k), y = p.y * (1 - k);
    const s = toScreen(x, y);
    ctx.fillStyle = hexA(p.colour, (1 - k) * .75);
    ctx.beginPath();
    ctx.arc(s.x, s.y, 1.5 * (1 - k) + .4, 0, TAU);
    ctx.fill();
  }
  ctx.restore();
}

function drawTimeline() {
  if (!snap) return;
  const w = timeline.clientWidth, h = timeline.clientHeight || 56;
  tctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  tctx.clearRect(0, 0, w, h);
  const data = snap.timeline;
  const max = Math.max(1, ...data.map((d) => d.tokens));
  const bw = w / data.length;
  tctx.font = '9px ui-sans-serif, system-ui, sans-serif';
  tctx.fillStyle = 'rgba(124,138,165,.55)';
  for (let hh = 0; hh <= data.length; hh += 6) {
    const x = (hh / data.length) * w;
    tctx.fillText(String(hh).padStart(2, '0') + 'h', Math.min(w - 24, x + 4), h - 4);
  }
  data.forEach((d, i) => {
    const bh = Math.max(1.5, (d.tokens / max) * (h - 10));
    const x = i * bw + 1;
    const g = tctx.createLinearGradient(0, h - bh, 0, h);
    g.addColorStop(0, 'rgba(125,211,252,.55)');
    g.addColorStop(1, 'rgba(56,189,248,.06)');
    tctx.fillStyle = g;
    tctx.fillRect(x, h - bh, Math.max(1, bw - 2), bh);
  });
}

// ---------- interactions ----------

function pick(px, py) {
  let best = null, bestD = 16;
  for (const n of nodes) {
    if (hidden.has(n.cluster)) continue;
    const p = toScreen(n.x, n.y);
    const d = Math.hypot(p.x - px, p.y - py);
    if (d < bestD) { best = n; bestD = d; }
  }
  return best;
}

sky.addEventListener('pointermove', (e) => {
  if (dragging) {
    cam.tx = cam.x = cam.x - (e.clientX - dragging.x) / cam.scale;
    cam.ty = cam.y = cam.y - (e.clientY - dragging.y) / cam.scale;
    dragging = { x: e.clientX, y: e.clientY, moved: true };
    return;
  }
  const n = pick(e.clientX, e.clientY);
  hoverId = n ? n.id : null;
  sky.style.cursor = n ? 'pointer' : 'grab';
});

sky.addEventListener('pointerdown', (e) => {
  dragging = { x: e.clientX, y: e.clientY, moved: false };
  sky.classList.add('dragging');
});

window.addEventListener('pointerup', (e) => {
  const wasDrag = dragging && dragging.moved;
  dragging = null;
  sky.classList.remove('dragging');
  if (wasDrag) return;
  const n = pick(e.clientX, e.clientY);
  if (n) openSession(n.id);
  else closeDrawer();
});

sky.addEventListener('wheel', (e) => {
  e.preventDefault();
  const before = toWorld(e.clientX, e.clientY);
  cam.tscale = clamp(cam.tscale * (e.deltaY < 0 ? 1.1 : .91), .18, 3.2);
  // keep the point under the cursor fixed while the camera eases in
  cam.scale = cam.tscale;
  const after = toWorld(e.clientX, e.clientY);
  cam.tx += before.x - after.x;
  cam.ty += before.y - after.y;
  cam.x = cam.tx; cam.y = cam.ty;
}, { passive: false });

document.addEventListener('keydown', (e) => {
  if (e.key === '/' && document.activeElement !== document.getElementById('search')) {
    e.preventDefault();
    document.getElementById('search').focus();
  }
  if (e.key === 'Escape') { closeDrawer(); document.getElementById('hits').classList.remove('on'); }
  if (e.key === 'f') { cam.fit = false; fit(); }
});

// ---------- data ----------

async function refresh() {
  try {
    const res = await fetch('/api/snapshot?hours=24');
    const data = await res.json();
    const isFirst = !snap;
    layout(data);
    if (isFirst) fit();
    if (selectedId) {
      const n = nodes.find((x) => x.id === selectedId);
      if (!n) closeDrawer();
    }
  } catch (err) {
    /* keep the last frame; the live dot shows the disconnect */
  }
}

function openSession(id) {
  selectedId = id;
  const drawer = document.getElementById('drawer');
  const body = document.getElementById('drawer-body');
  const n = nodes.find((x) => x.id === id);
  drawer.classList.add('open');
  body.innerHTML = `<h2>${escapeHtml(n ? n.title : id)}</h2><div class="sub">loading…</div>`;
  fetch('/api/session/' + encodeURIComponent(id))
    .then((r) => r.json())
    .then((s) => renderDetail(s, n))
    .catch(() => { body.innerHTML = '<p class="hint">Could not load that session.</p>'; });
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function cleanContent(raw) {
  let text = String(raw || '');
  try {
    const obj = JSON.parse(text);
    if (obj && typeof obj === 'object') {
      text = obj.output ?? obj.result ?? obj.summary ?? obj.content ?? obj.message ?? JSON.stringify(obj);
    }
  } catch (e) { /* plain text */ }
  text = String(text).replace(/\s+/g, ' ').trim();
  return text.length > 300 ? text.slice(0, 298) + '…' : text;
}

function ago(ts) {
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 60) return Math.round(d) + 's';
  if (d < 3600) return Math.round(d / 60) + 'm';
  if (d < 86400) return (d / 3600).toFixed(1) + 'h';
  return (d / 86400).toFixed(1) + 'd';
}

function stamp(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { day: '2-digit', month: 'short' }) + ' ' +
         d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

function renderDetail(s, n) {
  if (s.error) { document.getElementById('drawer-body').innerHTML = '<p class="hint">Session not found.</p>'; return; }
  const tokens = (s.input_tokens || 0) + (s.output_tokens || 0);
  const started = s.started_at ? new Date(s.started_at * 1000) : null;
  const running = !s.ended_at;
  const tools = (s.tools || []).map((t) => `<div class="tool"><span>${escapeHtml(t.name)}</span><b>${t.n}</b></div>`).join('');
  const msgs = (s.messages || []).slice(-7).map((m) => `
    <div class="msg"><div class="who">${escapeHtml(m.tool_name || m.role)} · ${ago(m.timestamp || Date.now() / 1000)}</div>
    <p>${escapeHtml(cleanContent(m.content))}</p></div>`).join('');
  document.getElementById('drawer-body').innerHTML = `
    <h2>${escapeHtml(s.title || 'untitled')}</h2>
    <div class="sub">${escapeHtml(s.source || '')} · ${escapeHtml(s.model || '')} · ${escapeHtml(s.id)}</div>
    <div class="grid">
      <div class="cell"><div class="k">tokens</div><div class="v">${fmt(tokens)}</div></div>
      <div class="cell"><div class="k">cost</div><div class="v">${money(s.estimated_cost_usd || 0)}</div></div>
      <div class="cell"><div class="k">tool messages</div><div class="v">${(s.tools || []).reduce((a, t) => a + t.n, 0)}</div></div>
      <div class="cell"><div class="k">${running ? 'started' : 'ended'}</div><div class="v">${stamp(s.started_at || 0)}</div></div>
    </div>
    <h3>top tools</h3>${tools || '<p class="hint">none</p>'}
    <h3>last messages</h3>${msgs || '<p class="hint">none</p>'}`;
}

function closeDrawer() {
  document.getElementById('drawer').classList.remove('open');
  selectedId = null;
}

document.getElementById('close').onclick = closeDrawer;

let searchTimer = null;
document.getElementById('search').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  const box = document.getElementById('hits');
  if (q.length < 2) { box.classList.remove('on'); return; }
  searchTimer = setTimeout(async () => {
    const res = await fetch('/api/search?q=' + encodeURIComponent(q));
    const data = await res.json();
    box.innerHTML = data.hits.map((h) => `
      <div class="hit" data-id="${escapeHtml(h.id)}">
        <b>${escapeHtml(h.title || h.id)}</b>
        <span>${escapeHtml((h.snippet || '').replace(/\s+/g, ' '))}</span>
      </div>`).join('') || '<div class="hit"><span>no hits</span></div>';
    box.classList.add('on');
    for (const el of box.querySelectorAll('.hit[data-id]')) {
      el.onclick = () => {
        const id = el.dataset.id;
        const n = nodes.find((x) => x.id === id);
        if (n) { cam.tx = n.x; cam.ty = n.y; cam.tscale = Math.max(cam.tscale, .8); }
        openSession(id);
        box.classList.remove('on');
      };
    }
  }, 220);
});

function connect() {
  const es = new EventSource('/api/stream');
  const live = document.getElementById('live');
  es.onopen = () => { live.classList.remove('off'); document.getElementById('live-label').textContent = 'live'; };
  es.onerror = () => { live.classList.add('off'); document.getElementById('live-label').textContent = 'reconnecting'; };
  es.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (snap) {
      const changed = data.totals.sessions !== snap.totals.sessions ||
        data.totals.tokens !== snap.totals.tokens ||
        (data.live || []).length !== snap.totals.running;
      snap.totals = data.totals;
      document.getElementById('m-running').textContent = data.totals.running;
      document.getElementById('m-sessions').textContent = data.totals.sessions;
      document.getElementById('m-tokens').textContent = fmt(data.totals.tokens);
      document.getElementById('m-cost').textContent = money(data.totals.cost);
      if (changed) setTimeout(refresh, 700);
    }
  };
}

// ---------- loop ----------

let lastFrame = performance.now();
function frame(now) {
  const dt = Math.min(.05, (now - lastFrame) / 1000);
  lastFrame = now;

  cam.x += (cam.tx - cam.x) * .18;
  cam.y += (cam.ty - cam.y) * .18;
  cam.scale += (cam.tscale - cam.scale) * .18;

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  drawSky(now);
  drawNebula();
  drawLinks(now);
  drawConnections(now);
  stepParticles(dt);
  drawParticles();
  drawCore(now);
  drawClusterLabels();
  drawNodes(now);

  requestAnimationFrame(frame);
}

window.__mission = { pick, toScreen, nodes: () => nodes, cam, view };

window.addEventListener('resize', () => { resize(); drawTimeline(); });
resize();
requestAnimationFrame(frame);
refresh().then(connect);
setInterval(() => { if (!document.hidden) refresh(); }, 20000);
