/* poppy-blackboard v1.0
 * ----------------------------------------------------------------------------
 * Drop-in module for the OPAS student page. Owns the blackboard pane:
 * parses signal tags from agent replies, renders keyword pills / diagrams /
 * formulas / wins / mastery / beats with hand-drawn chalk aesthetic
 * (Rough.js) and proper math typography (KaTeX), and animates them with a
 * pacing rhythm tuned for adult reading speed.
 *
 * Public API:
 *   import { initBlackboard } from './blackboard/blackboard.js';
 *   const bb = initBlackboard({
 *     container:    document.getElementById('blackboard-pane'),
 *     onChatText:   (role, text) => addBubble(role, text),
 *     voiceEnabled: () => voiceOn,            // optional
 *     getVoiceId:   () => 'EXAVITQu4...',     // optional
 *     runtimeUrl:   'http://localhost:8001',  // optional, for /voice/tts
 *     bearer:       () => BEARER,             // optional
 *   });
 *   bb.handleAgentReply(data.agent_reply);
 *
 * Dependencies (loaded by host page from CDN; module checks at runtime):
 *   - KaTeX  (window.katex)        for math typography
 *   - Rough.js (window.rough)      for chalk-style SVG
 * Both have graceful fallbacks if missing — typewriter for math, plain SVG
 * for diagrams.
 *
 * Grammar v1.0:
 *   [BEAT:title]                  open a teaching unit
 *   [BEAT:title|min=15]           with floor in seconds
 *   [CONCEPT:name]                blackboard concept label
 *   [MODE:EXPLAIN|CHECK|DIAGNOSE] internal mode marker
 *   [KEYWORD:term|definition]     supporting key term
 *   [KEYWORD!:term|definition]    HERO key term (large, longer dwell)
 *   [DRAW:type|label|details]     diagram (compare|steps|tree|formula|curve)
 *   [DRAW!:type|label|details]    HERO diagram
 *   [POINT:term]                  pulse a previously-introduced keyword
 *   [WIN:description]             celebration card
 *   [MASTERY:+N] | [MASTERY:COMPLETE]
 */

const SIGNAL_RE = /\[(BEAT|CONCEPT|MODE|KEYWORD|DRAW|POINT|WIN|MASTERY)(!)?:([^\]]+)\]/gi;

const DEFAULT_RHYTHM = {
  agent:    { dwell: 1800 },
  user:     { dwell: 1200 },
  KEYWORD:  { dwell: 4500, hero: 8500 },
  DRAW:     { dwell: 6500, hero: 9000 },
  POINT:    { dwell: 1800 },
  WIN:      { dwell: 2400 },
  MASTERY:  { dwell: 1400 },
  BEAT_END: { dwell: 1800 },
  BEAT_FLOOR_MS: 14000,
  // Mid-reply beat floor — when the agent emits multiple [BEAT:] tags in a
  // single reply, each non-final beat lingers at least this long before the
  // next one wipes the stage. Shorter than BEAT_FLOOR_MS so multi-beat
  // replies don't drag, but long enough that the headline + initial pills
  // are actually readable. Tune in opts.rhythm if a skill needs a different
  // pacing profile.
  BEAT_INTER_MS: 5500,
};

export function initBlackboard(opts = {}) {
  const cfg = {
    container:    opts.container,
    onChatText:   opts.onChatText   || (() => {}),
    voiceEnabled: opts.voiceEnabled || (() => false),
    getVoiceId:   opts.getVoiceId   || (() => 'EXAVITQu4vr4xnSDxMaL'),
    runtimeUrl:   opts.runtimeUrl   || '',
    bearer:       opts.bearer       || (() => ''),
    rhythm:       Object.assign({}, DEFAULT_RHYTHM, opts.rhythm || {}),
    speed:        opts.speed || 1,
  };
  if (!cfg.container) throw new Error('initBlackboard: container element is required');

  cfg.container.classList.add('bb-pane');
  cfg.container.innerHTML = `
    <div class="bb-timeline" data-bb="timeline"></div>
    <div class="bb-stage" data-bb="stage">
      <div class="bb-stage-content" data-bb="stage-content">
        <div class="bb-empty" data-bb="empty">
          <div>blackboard ready</div>
          <div class="bb-empty-sub">key terms, diagrams &amp; wins land here</div>
        </div>
      </div>
      <div class="bb-hero-win" data-bb="hero-win"><div class="bb-hero-win-card" data-bb="hero-win-text">★</div></div>
      <div class="bb-toast" data-bb="toast"></div>
    </div>
    <div class="bb-map" data-bb="map">
      <h4>Concept map</h4>
      <svg data-bb="map-svg" viewBox="0 0 240 320" preserveAspectRatio="xMidYMid meet"></svg>
    </div>
    <div class="bb-rewards" data-bb="rewards">
      <div class="bb-wins-strip" data-bb="wins-strip"></div>
      <div class="bb-mastery-cluster">
        <span class="bb-lbl">Mastery</span>
        <div class="bb-mastery-bar"><div class="bb-mastery-fill" data-bb="mastery-fill"></div></div>
        <span class="bb-mastery-pct" data-bb="mastery-pct">0%</span>
      </div>
    </div>
  `;

  if (!document.querySelector('[data-bb="milestone-burst"]')) {
    const burst = document.createElement('div');
    burst.className = 'bb-milestone-burst';
    burst.setAttribute('data-bb', 'milestone-burst');
    burst.innerHTML = `
      <div class="bb-ring"></div><div class="bb-ring"></div><div class="bb-ring"></div>
      <div class="bb-banner">
        <div class="bb-banner-label" data-bb="ms-label">Mastery</div>
        <div class="bb-banner-title" data-bb="ms-title">50%</div>
        <div class="bb-banner-sub"   data-bb="ms-sub">Halfway through the unit</div>
      </div>`;
    document.body.appendChild(burst);
  }

  const $ = sel => cfg.container.querySelector(`[data-bb="${sel}"]`);
  const $body = sel => document.querySelector(`[data-bb="${sel}"]`);

  const state = {
    beats: [],
    currentBeat: -1,
    mastery: 0,
    mapNodes: [],
    mapEdges: [],
  };

  const sleep = ms => new Promise(r => setTimeout(r, ms / cfg.speed));
  const escapeHtml = s => String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
  const slug = s => String(s).toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9\-]/g, '');

  function dwellFor(cue) {
    if (typeof cue.hold === 'number') return cue.hold;
    const r = cfg.rhythm[cue.kind];
    if (!r) return 800;
    return cue.hero && r.hero ? r.hero : r.dwell;
  }

  function parseSignals(rawText) {
    const cues = [];
    let cleaned = String(rawText || '');
    cleaned = cleaned.replace(SIGNAL_RE, (_m, kind, bang, payload) => {
      cues.push({ kind: kind.toUpperCase(), hero: !!bang, payload: payload.trim() });
      return '';
    });
    cleaned = cleaned.replace(/[ \t]{2,}/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
    return { cleaned, cues };
  }

  async function startBeat(payload) {
    const [title, ...rest] = payload.split('|').map(s => s.trim());
    const minMatch = rest.join('|').match(/min\s*=\s*(\d+)/);
    const minMs = minMatch ? parseInt(minMatch[1], 10) * 1000 : null;

    state.currentBeat += 1;
    const idx = state.currentBeat;
    state.beats.push({ id: 'b' + (idx + 1), title, keywords: [], draws: [], wins: [], minMs, started: performance.now() });

    const emptyEl = $('empty');
    if (emptyEl) emptyEl.style.display = 'none';

    const stage = $('stage-content');
    stage.classList.add('bb-dimmed');
    await sleep(160);
    stage.innerHTML = `
      <div class="bb-beat-label">Beat ${idx + 1}</div>
      <div class="bb-beat-headline" data-bb-headline></div>
      <div class="bb-kw-cluster" data-bb-kw></div>
      <div class="bb-draw-area" data-bb-draws></div>
    `;
    stage.classList.remove('bb-dimmed');
    await typewrite(stage.querySelector('[data-bb-headline]'), title);

    document.querySelectorAll('[data-bb-pip]').forEach(p => {
      p.classList.remove('active'); p.classList.add('done');
    });
    const pip = document.createElement('div');
    pip.className = 'bb-pip active';
    pip.setAttribute('data-bb-pip', String(idx));
    pip.innerHTML = `<span class="bb-pip-num">${String(idx + 1).padStart(2,'0')}</span>${escapeHtml(title)}`;
    $('timeline').appendChild(pip);
    $('timeline').scrollLeft = $('timeline').scrollWidth;
  }

  async function typewrite(el, text, charMs = 18) {
    if (!el) return;
    el.classList.add('bb-typing');
    for (let i = 0; i < text.length; i++) {
      el.textContent = text.slice(0, i + 1);
      await sleep(charMs + Math.random() * charMs);
    }
    el.classList.remove('bb-typing');
  }

  async function renderKeyword(payload, hero = false) {
    const idx = state.currentBeat;
    if (idx < 0) return;
    const cluster = cfg.container.querySelector('[data-bb-kw]');
    if (!cluster) return;
    const [term, def = ''] = payload.split('|').map(s => s.trim());
    const cite = state.beats.flatMap(b => b.keywords).length + 1;
    state.beats[idx].keywords.push({ term, def, cite, hero });

    const pill = document.createElement('div');
    pill.className = 'bb-kw' + (hero ? ' bb-kw-hero' : '');
    pill.id = 'bb-kw-' + slug(term);
    pill.innerHTML = `
      <span class="bb-cite">${cite}</span>
      <span class="bb-term">${escapeHtml(term)}</span>
      <span class="bb-def"></span>
    `;
    cluster.appendChild(pill);
    await typewrite(pill.querySelector('.bb-def'), def);
    addToConceptMap(term, idx);
  }

  function animateRoughGroup(g, ms = 900, delayStep = 80) {
    if (!g) return;
    g.querySelectorAll('path').forEach((p, i) => {
      try {
        const len = p.getTotalLength();
        if (!isFinite(len) || len === 0) return;
        p.style.strokeDasharray = len;
        p.style.strokeDashoffset = len;
        p.style.transition = `stroke-dashoffset ${ms}ms ease-out ${i * delayStep}ms`;
        requestAnimationFrame(() => requestAnimationFrame(() => {
          p.style.strokeDashoffset = 0;
        }));
      } catch (_) {}
    });
  }

  async function renderDraw(payload, hero = false) {
    const idx = state.currentBeat;
    if (idx < 0) return;
    const area = cfg.container.querySelector('[data-bb-draws]');
    if (!area) return;

    const parts = payload.split('|').map(s => s.trim());
    const [type = 'text', label = '', ...rest] = parts;
    const t = type.toLowerCase();
    const card = document.createElement('div');
    card.className = `bb-draw bb-draw-${t}` + (hero ? ' bb-draw-hero' : '');
    card.innerHTML = `<div class="bb-draw-label">${escapeHtml(label)}</div>`;
    state.beats[idx].draws.push({ type: t, label, raw: rest.join('|'), hero });

    if (t === 'formula') {
      const target = document.createElement('div');
      target.className = 'bb-formula-katex';
      card.appendChild(target);
      area.appendChild(card);
      const latex = rest.join('|');
      if (window.katex) {
        try {
          window.katex.render(latex, target, { throwOnError: false, displayMode: true });
        } catch (e) { target.textContent = latex; }
      } else {
        target.classList.add('bb-formula-text');
        await typewrite(target, latex);
      }
      return;
    }

    if (t === 'compare') {
      const [leftBody = '', rightBody = ''] = rest.join('|').split('||').map(s => s.trim());
      const labelParts = label.split(/\s+vs\s+/i);
      const [leftLabel, rightLabel] = labelParts.length === 2 ? labelParts : ['A', 'B'];
      card.insertAdjacentHTML('beforeend', `
        <div class="bb-compare-grid">
          <div class="bb-compare-col bb-compare-left"><h5>${escapeHtml(leftLabel)}</h5><p>${escapeHtml(leftBody)}</p></div>
          <div class="bb-compare-vs">vs</div>
          <div class="bb-compare-col bb-compare-right"><h5>${escapeHtml(rightLabel)}</h5><p>${escapeHtml(rightBody)}</p></div>
        </div>`);
      area.appendChild(card);
      return;
    }

    if (t === 'steps') {
      const items = rest.join('|').split(/\s*->\s*|\s*→\s*/).filter(Boolean);
      card.insertAdjacentHTML('beforeend',
        '<ol class="bb-steps">' + items.map(s => `<li>${escapeHtml(s)}</li>`).join('') + '</ol>');
      area.appendChild(card);
      return;
    }

    if (t === 'tree') {
      const segs = rest.join('|').split(';').map(s => s.trim()).filter(Boolean);
      const map = {}; let root = null;
      for (const s of segs) {
        const [parent, kids = ''] = s.split(':').map(x => x.trim());
        if (!parent) continue;
        if (root === null) root = parent;
        map[parent] = kids.split(',').map(x => x.trim()).filter(Boolean);
      }
      const build = n => {
        const ks = map[n] || [];
        return ks.length
          ? `<li><strong>${escapeHtml(n)}</strong><ul>${ks.map(build).join('')}</ul></li>`
          : `<li>${escapeHtml(n)}</li>`;
      };
      card.insertAdjacentHTML('beforeend',
        '<div class="bb-tree">' + (root ? `<ul>${build(root)}</ul>` : escapeHtml(rest.join('|'))) + '</div>');
      area.appendChild(card);
      return;
    }

    if (t === 'curve') {
      const [axes = 'x,y', shape = 'rising'] = rest.join('|').split(';').map(s => s.trim());
      const [xl = 'x', yl = 'y'] = axes.split(',').map(s => s.trim());
      const path = ({
        rising:  'M 10 90 Q 50 90, 90 10',
        falling: 'M 10 10 Q 50 10, 90 90',
        bell:    'M 5 90 Q 50 0, 95 90',
        flat:    'M 10 50 L 90 50',
        sigmoid: 'M 10 90 C 40 90, 60 10, 90 10',
      }[shape.toLowerCase()] || 'M 10 90 Q 50 90, 90 10');

      const svgNS = 'http://www.w3.org/2000/svg';
      const svg = document.createElementNS(svgNS, 'svg');
      svg.setAttribute('viewBox', '0 0 100 100');
      svg.setAttribute('preserveAspectRatio', 'none');
      svg.classList.add('bb-curve-svg');
      card.appendChild(svg);
      area.appendChild(card);

      if (window.rough) {
        const rc = window.rough.svg(svg);
        const ax = rc.line(10, 90, 95, 90, { roughness:1.3, bowing:0.8, stroke:'rgba(242,239,230,.3)', strokeWidth:0.9 });
        const ay = rc.line(10, 5,  10, 90, { roughness:1.3, bowing:0.8, stroke:'rgba(242,239,230,.3)', strokeWidth:0.9 });
        const cv = rc.path(path,           { roughness:1.5, bowing:1.2, stroke:'#D4B040',                strokeWidth:2.2 });
        svg.appendChild(ax); svg.appendChild(ay); svg.appendChild(cv);
        animateRoughGroup(ax, 400);
        animateRoughGroup(ay, 400);
        animateRoughGroup(cv, 1100);
      } else {
        svg.innerHTML = `
          <line x1="10" y1="90" x2="95" y2="90" stroke="rgba(242,239,230,.3)" stroke-width=".5"/>
          <line x1="10" y1="5"  x2="10" y2="90" stroke="rgba(242,239,230,.3)" stroke-width=".5"/>
          <path d="${path}" stroke="#D4B040" stroke-width="2.2" fill="none" stroke-linecap="round"/>`;
      }
      const tx = document.createElementNS(svgNS, 'text');
      tx.setAttribute('x','95'); tx.setAttribute('y','98'); tx.setAttribute('text-anchor','end');
      tx.setAttribute('font-size','5'); tx.setAttribute('fill','rgba(242,239,230,.5)');
      tx.textContent = xl; svg.appendChild(tx);
      const ty = document.createElementNS(svgNS, 'text');
      ty.setAttribute('x','14'); ty.setAttribute('y','10');
      ty.setAttribute('font-size','5'); ty.setAttribute('fill','rgba(242,239,230,.5)');
      ty.textContent = yl; svg.appendChild(ty);
      return;
    }

    card.insertAdjacentHTML('beforeend', `<div class="bb-draw-body">${escapeHtml(rest.join('|'))}</div>`);
    area.appendChild(card);
  }

  function pointAt(term) {
    const id = 'bb-kw-' + slug(term);
    const el = document.getElementById(id);
    if (el) {
      el.classList.remove('bb-pointed'); void el.offsetWidth; el.classList.add('bb-pointed');
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
    const node = document.getElementById('bb-map-' + slug(term));
    if (node) {
      node.classList.remove('bb-map-pulse'); void node.getBoundingClientRect();
      node.classList.add('bb-map-pulse');
    }
  }

  async function addWin(text) {
    if (state.currentBeat < 0) state.beats.push({ keywords: [], draws: [], wins: [] });
    const overlay = $('hero-win');
    const card = $('hero-win-text');
    card.textContent = text;
    overlay.classList.remove('bb-visible'); void overlay.offsetWidth;
    overlay.classList.add('bb-visible');
    await sleep(cfg.rhythm.WIN.dwell - 600);
    overlay.classList.remove('bb-visible');

    const chip = document.createElement('div');
    chip.className = 'bb-win-chip';
    chip.textContent = text;
    $('wins-strip').appendChild(chip);
    state.beats[state.currentBeat].wins.push(text);
    $('wins-strip').scrollLeft = $('wins-strip').scrollWidth;
  }

  function applyMastery(payload) {
    const prev = state.mastery;
    if (/^complete$/i.test(payload)) {
      state.mastery = 100;
    } else {
      const m = payload.match(/^\+?\s*(\d+)/);
      if (m) state.mastery = Math.min(100, state.mastery + parseInt(m[1], 10));
    }
    $('mastery-fill').style.width = state.mastery + '%';
    animateCount($('mastery-pct'), prev, state.mastery, 800);

    const ms = [
      { at: 25,  label: 'Mastery',       title: '25%',          sub: 'Foundation locked in' },
      { at: 50,  label: 'Mastery',       title: '50%',          sub: 'Halfway through the unit' },
      { at: 75,  label: 'Mastery',       title: '75%',          sub: 'Almost there' },
      { at: 100, label: 'Unit complete', title: 'Mastery 100%', sub: "Today's unit fully understood" },
    ];
    for (const m of ms) {
      if (prev < m.at && state.mastery >= m.at) {
        milestoneBurst(m.label, m.title, m.sub, m.at === 100);
        break;
      }
    }
  }

  function animateCount(el, from, to, duration) {
    const start = performance.now();
    function tick(t) {
      const p = Math.min(1, (t - start) / duration);
      el.textContent = Math.round(from + (to - from) * p) + '%';
      if (p < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  function milestoneBurst(label, title, sub, big) {
    $body('ms-label').textContent = label;
    $body('ms-title').textContent = title;
    $body('ms-sub').textContent = sub;
    const burst = $body('milestone-burst');
    burst.classList.remove('bb-visible'); void burst.offsetWidth;
    burst.classList.add('bb-visible');
    if (big) {
      cfg.container.classList.remove('bb-celebrate'); void cfg.container.offsetWidth;
      cfg.container.classList.add('bb-celebrate');
    }
  }

  async function showBeatToast() {
    const idx = state.currentBeat;
    if (idx < 0) return;
    const b = state.beats[idx];
    const toast = $('toast');
    toast.innerHTML = `<span class="bb-toast-accent">Beat ${idx + 1} ✓</span> ${b.keywords.length} concept${b.keywords.length === 1 ? '' : 's'} · ${b.wins.length} win${b.wins.length === 1 ? '' : 's'}`;
    toast.classList.add('bb-visible');
    await sleep(3800);  // Lucas + Krizia testing said 1.7s was too fast to read; bumped to 3.8s
    toast.classList.remove('bb-visible');
    await sleep(250);
  }

  function addToConceptMap(term, beatIdx) {
    const idx = state.mapNodes.length;
    const angle = (idx / 9) * Math.PI * 2 - Math.PI / 2;
    const cx = 120, cy = 160, R = 90;
    const x = cx + R * Math.cos(angle);
    const y = cy + R * Math.sin(angle);
    const colors = ['#4070C0', '#48A060', '#D4B040', '#D48040', '#CC4848'];
    const color = colors[beatIdx % colors.length];
    const node = { term, x, y, color, beat: beatIdx };
    const prev = state.mapNodes.filter(n => n.beat === beatIdx).slice(-1)[0];
    if (prev) state.mapEdges.push({ from: prev, to: node, kind: 'within' });
    const lastHead = state.mapNodes.find(n => n.beat === beatIdx - 1);
    if (!prev && lastHead) state.mapEdges.push({ from: lastHead, to: node, kind: 'cross' });
    state.mapNodes.push(node);
    drawMap();
  }

  function drawMap() {
    const svg = $('map-svg');
    if (!svg) return;
    svg.innerHTML = '';
    const useRough = !!window.rough;
    const rc = useRough ? window.rough.svg(svg) : null;

    for (const e of state.mapEdges) {
      if (useRough) {
        const opts = e.kind === 'cross'
          ? { roughness: 1.2, bowing: 1.0, stroke: 'rgba(212,176,64,.55)', strokeWidth: 1.2, strokeLineDash: [4, 3] }
          : { roughness: 1.1, bowing: 0.9, stroke: 'rgba(242,239,230,.30)', strokeWidth: 0.9 };
        svg.appendChild(rc.line(e.from.x, e.from.y, e.to.x, e.to.y, opts));
      } else {
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', e.from.x); line.setAttribute('y1', e.from.y);
        line.setAttribute('x2', e.to.x);   line.setAttribute('y2', e.to.y);
        line.setAttribute('stroke', e.kind === 'cross' ? 'rgba(212,176,64,.4)' : 'rgba(242,239,230,.18)');
        line.setAttribute('stroke-width', e.kind === 'cross' ? '1.4' : '1');
        if (e.kind === 'cross') line.setAttribute('stroke-dasharray', '3 3');
        svg.appendChild(line);
      }
    }

    for (const n of state.mapNodes) {
      const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      g.classList.add('bb-map-node-g');
      if (useRough) {
        const node = rc.circle(n.x, n.y, 14, {
          roughness: 1.4, bowing: 1.0,
          fill: n.color, fillStyle: 'solid',
          stroke: 'rgba(242,239,230,0.55)', strokeWidth: 1,
        });
        node.setAttribute('id', 'bb-map-' + slug(n.term));
        g.appendChild(node);
      } else {
        const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        c.setAttribute('id', 'bb-map-' + slug(n.term));
        c.setAttribute('cx', n.x); c.setAttribute('cy', n.y); c.setAttribute('r', '7');
        c.setAttribute('fill', n.color);
        c.setAttribute('stroke', 'rgba(242,239,230,.3)'); c.setAttribute('stroke-width', '1');
        g.appendChild(c);
      }
      const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      text.setAttribute('x', n.x);
      text.setAttribute('y', n.y + (n.y < 160 ? -12 : 18));
      text.setAttribute('text-anchor', 'middle');
      text.setAttribute('font-size', '9');
      text.setAttribute('fill', 'rgba(242,239,230,.85)');
      text.textContent = n.term;
      g.appendChild(text);
      svg.appendChild(g);
    }
  }

  async function runCue(cue) {
    switch (cue.kind) {
      case 'BEAT':    await startBeat(cue.payload); break;
      case 'CONCEPT': /* hook */ break;
      case 'MODE':    /* hook */ break;
      case 'KEYWORD': await renderKeyword(cue.payload, cue.hero); break;
      case 'DRAW':    await renderDraw(cue.payload, cue.hero); break;
      case 'POINT':   pointAt(cue.payload); break;
      case 'WIN':     await addWin(cue.payload); return;
      case 'MASTERY': applyMastery(cue.payload); break;
    }
    await sleep(dwellFor(cue));
  }

  async function runBeatFromCues(cues) {
    // Split cues into per-beat groups. Each [BEAT:] starts a new group, and
    // any non-BEAT cues attach to the most recent group. Cues that arrive
    // before the first [BEAT:] (rare — usually leftover keywords) attach to
    // a leading placeholder so they still render against the current beat.
    //
    // Why split: when a single agent reply contains multiple [BEAT:] tags,
    // processing them as one flat list lets each successive [BEAT:] wipe the
    // previous beat's stage content after only ~1.5s (typewrite + generic
    // dwell). The min-floor only landed on the *last* beat. By grouping per
    // beat, every beat gets its own minimum dwell so it stays readable.
    const groups = [];
    let current = null;
    for (const c of cues) {
      if (c.kind === 'BEAT') {
        current = [c];
        groups.push(current);
      } else if (current) {
        current.push(c);
      } else {
        if (groups.length === 0) groups.push([]);
        groups[0].push(c);
      }
    }
    if (groups.length === 0) return;

    for (let i = 0; i < groups.length; i++) {
      const group = groups[i];
      const isLast = (i === groups.length - 1);
      const beatCue = group.find(c => c.kind === 'BEAT');
      const beatStart = performance.now();
      for (const c of group) await runCue(c);
      const idx = state.currentBeat;
      // Last beat in a reply gets the full floor (so the agent's closing beat
      // gets the long absorb time). Mid-reply beats get a shorter inter-beat
      // floor — enough to read the headline + any keywords, but not so long
      // that a 3-beat reply locks the UI for 45s.
      const minMs = isLast
        ? ((state.beats[idx] && state.beats[idx].minMs) || cfg.rhythm.BEAT_FLOOR_MS)
        : (cfg.rhythm.BEAT_INTER_MS || 5500);
      const elapsed = (performance.now() - beatStart) * cfg.speed;
      const remain = minMs - elapsed;
      if (remain > 0) await sleep(remain);
      // Beat-end toast and final dwell only fire on the last beat — the
      // "Beat N ✓ — recap" celebration is meant to mark the close of a
      // teaching unit, not bridge between mid-reply beat transitions.
      if (isLast && beatCue) await showBeatToast();
      if (isLast) await sleep(cfg.rhythm.BEAT_END.dwell);
    }
  }

  let _audioEl = null;
  async function speak(text) {
    if (!cfg.voiceEnabled() || !text || !cfg.runtimeUrl) return;
    if (!_audioEl) _audioEl = new Audio();
    try {
      const res = await fetch(`${cfg.runtimeUrl}/voice/tts`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${cfg.bearer()}`,
        },
        body: JSON.stringify({ text, voice_id: cfg.getVoiceId() }),
      });
      if (!res.ok) return;
      const blob = await res.blob();
      _audioEl.src = URL.createObjectURL(blob);
      _audioEl.play().catch(() => {});
    } catch (_) {}
  }

  let _busy = Promise.resolve();
  async function handleAgentReply(rawText) {
    const { cleaned, cues } = parseSignals(rawText);
    if (cleaned) cfg.onChatText('agent', cleaned);
    speak(cleaned);
    _busy = _busy.then(() => runBeatFromCues(cues)).catch(() => {});
    return _busy;
  }

  function sessionLog() { return JSON.parse(JSON.stringify(state.beats)); }
  function setSpeed(n)  { cfg.speed = Math.max(0.25, Number(n) || 1); }
  function setRhythm(r) { Object.assign(cfg.rhythm, r); }
  function reset() {
    state.beats = []; state.currentBeat = -1; state.mastery = 0;
    state.mapNodes = []; state.mapEdges = [];
    cfg.container.querySelector('[data-bb="timeline"]').innerHTML = '';
    cfg.container.querySelector('[data-bb="stage-content"]').innerHTML =
      '<div class="bb-empty"><div>blackboard ready</div><div class="bb-empty-sub">key terms, diagrams &amp; wins land here</div></div>';
    cfg.container.querySelector('[data-bb="map-svg"]').innerHTML = '';
    cfg.container.querySelector('[data-bb="wins-strip"]').innerHTML = '';
    cfg.container.querySelector('[data-bb="mastery-fill"]').style.width = '0%';
    cfg.container.querySelector('[data-bb="mastery-pct"]').textContent = '0%';
  }

  return { handleAgentReply, sessionLog, setSpeed, setRhythm, reset };
}
