(function () {
  'use strict';
  // Decision board (.datacore/skills/decision-board). Local page: choices live
  // in localStorage under the board's slug, and Save downloads
  // <slug>.decisions.json for Claude to read back. Nothing is sent anywhere.
  // Large boards: rows are grouped by area; each group and each section can be
  // set in one click, and big groups start collapsed.
  var DATA = JSON.parse(document.getElementById('data').textContent);
  var META = DATA.meta;
  var SLUG = META.slug;
  var BUILD = META.build || '';
  var STORE = 'decision-board:' + SLUG + (BUILD ? ':' + BUILD : '');
  var SECTIONS = DATA.sections || [];
  var ROWS = [], BYID = {}, GROUPS = {};
  var BULK = [['done', 'Done'], ['next', 'Do next'], ['defer', 'Defer'], ['someday', 'Someday'], ['drop', 'Drop'], ['accept', 'Accept'], ['delegate', 'Delegate']];
  var BULKSET = {}; BULK.forEach(function (b) { BULKSET[b[0]] = b[1].toLowerCase(); });

  SECTIONS.forEach(function (s, si) {
    var map = {}, order = [];
    (s.rows || []).forEach(function (r) {
      r.section = s.key; ROWS.push(r); BYID[r.id] = r;
      var k = r.area || '';
      if (!map[k]) { map[k] = { id: 'g' + si + '-' + order.length, area: k, rows: [] }; order.push(k); }
      map[k].rows.push(r);
    });
    s.groups = order.map(function (k) { return map[k]; });
    s.groups.forEach(function (g) { GROUPS[g.id] = g; });
    s.bulkable = (s.rows || []).some(function (r) { return (r.options || []).some(function (o) { return BULKSET[o.value]; }); });
  });

  function load() { try { return JSON.parse(localStorage.getItem(STORE) || 'null'); } catch (e) { return null; } }
  var kept = load() || {};
  var dec = {};
  function seed(src) {
    Object.keys(src || {}).forEach(function (id) {
      if (!BYID[id]) return;
      dec[id] = { choice: src[id].choice || null, note: src[id].note || '' };
    });
  }
  seed(DATA.prefill);
  seed(kept.decisions);
  var savedAt = kept.savedAt || null;
  var savedSnap = kept.savedSnap || null;

  var open = {};
  SECTIONS.forEach(function (s) {
    var many = (s.rows || []).length > 12;
    s.groups.forEach(function (g) { open[g.id] = !many || g.rows.length <= 3; });
  });

  function persist() { try { localStorage.setItem(STORE, JSON.stringify({ decisions: dec, savedAt: savedAt, savedSnap: savedSnap })); } catch (e) {} }
  function h(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function choiceOf(id) { return (dec[id] && dec[id].choice) || null; }
  function countDecided(rows) { return rows.filter(function (r) { return !!choiceOf(r.id); }).length; }
  function payload() {
    var out = {};
    ROWS.forEach(function (r) {
      var d = dec[r.id];
      if (d && (d.choice || d.note)) out[r.id] = { choice: d.choice || null, note: d.note || '' };
    });
    return out;
  }
  function snapshot() { return JSON.stringify(payload()); }
  function hasOption(r, v) { return (r.options || []).some(function (o) { return o.value === v; }); }

  function stage(n, st, label, sub) {
    return '<li class="stage s' + n + ' ' + st + '"><span class="node" aria-hidden="true"></span><span class="stage-label">' + h(label) + '</span><span class="stage-sub">' + sub + '</span></li>';
  }
  function renderTop() {
    var p = META.path || [];
    var states = ['fired', 'fired', 'current', 'latent'];
    return '<header class="wrap mast"><div>' +
      '<p class="eyebrow">' + h(META.eyebrow) + '</p><h1>' + h(META.h1) + '</h1>' +
      '<p class="lede">' + h(META.lede) + '</p>' +
      '<p class="asof">As of ' + h(META.asOf) + ' · ' + h((META.sources || []).join(' · ')) + '</p></div>' +
      '<ol class="path" aria-label="Where this review stands">' + p.map(function (x, i) {
        var sub = i === 2 ? '<span data-live="decided">' + countDecided(ROWS) + '</span> of ' + ROWS.length + ' decided' : h(x[1]);
        return stage(i + 1, states[i] || 'latent', x[0], sub);
      }).join('') + '</ol></header>' +
      '<nav class="wrap idx" aria-label="Sections">' + SECTIONS.map(function (s) {
        return '<a href="#sec-' + h(s.key) + '">' + h(s.label) + '<span class="count">' + (s.rows || []).length + '</span></a>';
      }).join('') + '</nav>';
  }
  function renderBar() {
    return '<div class="wrap bar-in">' +
      '<span class="count-line"><b data-live="decided">' + countDecided(ROWS) + '</b> of ' + ROWS.length + ' decided</span>' +
      '<button type="button" class="linkbtn" data-do="rest">Use the suggestions for the rest</button>' +
      '<div class="savebox"><span class="status" role="status" id="status"></span>' +
      '<button class="btn btn-primary" type="button" data-do="save">Save</button></div></div>';
  }

  function option(r, o) {
    var on = choiceOf(r.id) === o.value;
    return '<button type="button" class="choice" role="radio" aria-checked="' + on + '" data-do="pick" data-id="' + h(r.id) + '" data-v="' + h(o.value) + '">' +
      '<span class="c-label">' + h(o.label) + '</span>' + (o.value === r.suggested ? '<span class="sug">suggested</span>' : '') +
      (o.consequence ? '<span class="c-cons">' + h(o.consequence) + '</span>' : '') + '</button>';
  }
  function row(r) {
    var d = dec[r.id] || {};
    var meta = [r.id].concat(r.meta || []);
    var links = (r.links || []).map(function (l) { return '<a class="ref" href="' + h(l.url) + '" target="_blank" rel="noopener">' + h(l.text) + '</a>'; }).join('');
    return '<li class="row" id="row-' + h(r.id) + '">' +
      '<div class="meta"><span class="rid">' + h(meta[0]) + '</span>' + meta.slice(1).map(function (m) { return '<span>' + h(m) + '</span>'; }).join('') + links + '</div>' +
      '<h3 class="title">' + h(r.title) + '</h3>' +
      (r.context ? '<p class="why">' + h(r.context) + '</p>' : '') +
      (r.preview ? '<p class="preview">' + h(r.preview) + '</p>' : '') +
      '<div class="opts" role="radiogroup" aria-label="Decision ' + h(r.id) + '">' + (r.options || []).map(function (o) { return option(r, o); }).join('') + '</div>' +
      '<label class="note-label" for="note-' + h(r.id) + '">Note for Claude</label>' +
      '<textarea class="note" id="note-' + h(r.id) + '" data-id="' + h(r.id) + '" rows="1" placeholder="Optional">' + h(d.note || '') + '</textarea></li>';
  }
  function noted(list) {
    if (!list || !list.length) return '';
    return '<div class="noted"><h3 class="group">Noted, nothing to decide</h3><ul>' + list.map(function (n) {
      var t = typeof n === 'string' ? { text: n } : n;
      return '<li>' + (t.tag ? '<span class="st' + (t.warn ? ' st-warn' : '') + '">' + h(t.tag) + '</span>' : '') + '<span>' + h(t.text) + '</span></li>';
    }).join('') + '</ul></div>';
  }
  function sugSummary(rows) {
    var c = {};
    rows.forEach(function (r) { if (r.suggested) c[r.suggested] = (c[r.suggested] || 0) + 1; });
    return Object.keys(c).sort(function (a, b) { return c[b] - c[a]; }).map(function (k) { return c[k] + ' ' + (BULKSET[k] || k); }).join(' · ');
  }
  function bulkButtons(scope, id, rows) {
    var have = {};
    rows.forEach(function (r) { (r.options || []).forEach(function (o) { have[o.value] = 1; }); });
    var attrs = ' data-do="bulk" data-scope="' + scope + '" data-g="' + h(id) + '"';
    return '<div class="gbulk"><span class="label">Set all ' + rows.length + '</span>' +
      BULK.filter(function (b) { return have[b[0]]; }).map(function (b) {
        return '<button type="button" class="gbtn"' + attrs + ' data-v="' + b[0] + '">' + h(b[1]) + '</button>';
      }).join('') +
      '<button type="button" class="gbtn quiet"' + attrs + ' data-v="@sug">Suggestions for the undecided</button>' +
      '<button type="button" class="gbtn quiet"' + attrs + ' data-v="@clear">Clear</button></div>';
  }
  function groupHead(s, g) {
    return '<div class="ghead"><button type="button" class="gtoggle" data-do="toggle" data-g="' + g.id + '" aria-expanded="' + !!open[g.id] + '" aria-controls="list-' + g.id + '">' +
      '<span class="caret" aria-hidden="true"></span>' + h(g.area || 'Items') + '<span class="count">' + g.rows.length + '</span></button>' +
      '<span class="gmeta"><span data-gdec="' + g.id + '">' + countDecided(g.rows) + '</span> of ' + g.rows.length + ' decided · suggested: ' + h(sugSummary(g.rows)) + '</span>' +
      (s.bulkable ? bulkButtons('g', g.id, g.rows) : '') + '</div>';
  }
  function section(s) {
    var rows = s.rows || [];
    var headed = s.groups.length > 1 || rows.length > 12;
    var body = s.groups.map(function (g) {
      return (headed ? groupHead(s, g) : '') + '<ol class="list" id="list-' + g.id + '"' + (open[g.id] ? '' : ' hidden') + '>' + g.rows.map(row).join('') + '</ol>';
    }).join('');
    var sbulk = s.bulkable && s.groups.length > 1 ? '<div class="sbulk"><span class="gmeta"><span data-sdec="' + h(s.key) + '">' + countDecided(rows) + '</span> of ' + rows.length + ' decided in this section</span>' + bulkButtons('s', s.key, rows) + '</div>' : '';
    return '<section class="sec" id="sec-' + h(s.key) + '"><div class="panel-head"><div><h2>' + h(s.label) + '<span class="count">' + rows.length + '</span></h2>' +
      (s.hint ? '<p class="hint">' + h(s.hint) + '</p>' : '') + '</div></div>' + sbulk + body + noted(s.noted) + '</section>';
  }

  var app = document.getElementById('app');
  app.innerHTML = '<div id="top">' + renderTop() + '</div><div class="bar" id="bar">' + renderBar() + '</div>' +
    '<main class="wrap panel">' + SECTIONS.map(section).join('') + '</main>' +
    '<footer class="wrap foot">Read from ' + h((META.sources || []).join(', ')) + ' through org-workspace. Choices stay in this browser until you save; Save downloads ' + h(SLUG) + '.decisions.json for Claude to read. Org files change only after you say “apply the decisions”.</footer>';
  var statusEl = document.getElementById('status');

  function setStatus() {
    var changed = savedSnap !== null ? snapshot() !== savedSnap : countDecided(ROWS) > 0;
    if (savedAt && !changed) statusEl.textContent = 'Saved to Downloads as ' + SLUG + '.decisions.json · ' + savedAt.slice(11, 16);
    else if (savedAt) statusEl.textContent = 'Changed since the last save';
    else statusEl.textContent = countDecided(ROWS) ? 'Kept in this browser · not saved yet' : 'Nothing decided yet';
  }
  function refreshCounts() {
    var n = String(countDecided(ROWS));
    Array.prototype.forEach.call(document.querySelectorAll('[data-live="decided"]'), function (el) { el.textContent = n; });
    Array.prototype.forEach.call(document.querySelectorAll('[data-gdec]'), function (el) { var g = GROUPS[el.getAttribute('data-gdec')]; if (g) el.textContent = String(countDecided(g.rows)); });
    Array.prototype.forEach.call(document.querySelectorAll('[data-sdec]'), function (el) {
      var s = SECTIONS.filter(function (x) { return x.key === el.getAttribute('data-sdec'); })[0];
      if (s) el.textContent = String(countDecided(s.rows || []));
    });
    setStatus();
  }
  function paintRow(id) {
    var el = document.getElementById('row-' + id);
    if (!el) return;
    var cur = choiceOf(id);
    Array.prototype.forEach.call(el.querySelectorAll('.choice'), function (b) { b.setAttribute('aria-checked', String(b.getAttribute('data-v') === cur)); });
  }
  function setChoice(r, v) {
    var cur = dec[r.id] || { choice: null, note: '' };
    dec[r.id] = { choice: v, note: cur.note || '' };
    paintRow(r.id);
  }

  app.addEventListener('click', function (e) {
    var b = e.target.closest('[data-do]');
    if (!b || !app.contains(b)) return;
    var act = b.getAttribute('data-do');
    if (act === 'pick') {
      var r = BYID[b.getAttribute('data-id')], v = b.getAttribute('data-v');
      if (r) { setChoice(r, choiceOf(r.id) === v ? null : v); persist(); refreshCounts(); }
      return;
    }
    if (act === 'toggle') {
      var gid = b.getAttribute('data-g'), list = document.getElementById('list-' + gid);
      open[gid] = !open[gid];
      if (list) list.hidden = !open[gid];
      b.setAttribute('aria-expanded', String(!!open[gid]));
      return;
    }
    if (act === 'bulk') {
      var scope = b.getAttribute('data-scope'), key = b.getAttribute('data-g'), val = b.getAttribute('data-v');
      var rows = scope === 's' ? ((SECTIONS.filter(function (s) { return s.key === key; })[0] || {}).rows || []) : ((GROUPS[key] || {}).rows || []);
      rows.forEach(function (r) {
        if (val === '@clear') setChoice(r, null);
        else if (val === '@sug') { if (!choiceOf(r.id) && r.suggested) setChoice(r, r.suggested); }
        else if (hasOption(r, val)) setChoice(r, val);
      });
      persist(); refreshCounts();
      return;
    }
    if (act === 'rest') {
      ROWS.forEach(function (r) { if (!choiceOf(r.id) && r.suggested) setChoice(r, r.suggested); });
      persist(); refreshCounts();
      return;
    }
    if (act === 'save') save();
  });
  var t = null;
  app.addEventListener('input', function (e) {
    var el = e.target;
    if (!el.classList || !el.classList.contains('note')) return;
    var id = el.getAttribute('data-id');
    var cur = dec[id] || { choice: null, note: '' };
    dec[id] = { choice: cur.choice, note: el.value };
    clearTimeout(t); t = setTimeout(function () { persist(); setStatus(); }, 250);
  });

  function save() {
    var now = new Date().toISOString();
    var body = JSON.stringify({ board: SLUG, build: BUILD, savedAt: now, decisions: payload() }, null, 2);
    var file = SLUG + '.decisions.json';
    try {
      var url = URL.createObjectURL(new Blob([body], { type: 'application/json' }));
      var a = document.createElement('a');
      a.href = url; a.download = file; a.style.display = 'none';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
      savedAt = now; savedSnap = snapshot(); persist();
      statusEl.textContent = 'Saved to Downloads as ' + file + ' · ' + now.slice(11, 16);
    } catch (err) {
      statusEl.textContent = 'The download did not start. Your choices are still kept in this browser.';
    }
  }

  setStatus();
})();
