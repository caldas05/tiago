"""Local web UI for polytime — drag-drop a MIDI, preview, configure, generate.

Run:   python app.py
Build: build.bat  (PyInstaller --onefile --noconsole)
"""
from __future__ import annotations
import base64
import json
import os
import socket
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if hasattr(sys, "_MEIPASS"):
    sys.path.insert(0, sys._MEIPASS)

from polytime import (  # noqa: E402
    polytime, detect_time_signature, detect_bpm, _parse_when, parse_scale,
    parse_range,
)
from model.measure import TimeSignature  # noqa: E402


# Auto-shutdown: the browser pings /heartbeat every few seconds. If we go
# HEARTBEAT_TIMEOUT_S without a ping, the server exits — so closing the tab
# (or the whole browser) doesn't leave a zombie process holding the port.
LAST_HEARTBEAT = time.monotonic()
# Generous timeout: browsers throttle setInterval in background tabs (Chrome
# pauses them entirely after a few minutes), so we must not kill the server
# just because the user looked at another tab.
HEARTBEAT_TIMEOUT_S = 180.0
HEARTBEAT_CHECK_S = 10.0


INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>polytime</title>
<style>
 body{font-family:system-ui,sans-serif;margin:0;padding:18px;background:#1a1a1a;color:#eee}
 h1{margin:0 0 14px;font-size:20px}
 #drop{border:2px dashed #555;border-radius:8px;padding:24px;text-align:center;
       cursor:pointer;background:#222;transition:.15s}
 #drop.hover{border-color:#7af;background:#2a3040}
 #drop input{display:none}
 .row{display:flex;gap:14px;margin:12px 0;flex-wrap:wrap;align-items:end}
 label{display:flex;flex-direction:column;font-size:13px;color:#aaa}
 label.inline{flex-direction:row;align-items:center;gap:6px;color:#ddd}
 input[type=text],input[type=number]{margin-top:4px;padding:6px 8px;background:#222;
   color:#eee;border:1px solid #444;border-radius:4px;font:inherit;width:110px}
 button{padding:8px 16px;background:#4a7;color:#000;border:0;border-radius:4px;
        font:inherit;font-weight:600;cursor:pointer}
 button:disabled{background:#555;color:#888;cursor:wait}
 button.dl{background:#7af}
 #status{margin:6px 0;font-size:13px;color:#aaa;min-height:1.2em}
 #status.err{color:#f77}
 .vizpane{display:flex;flex-direction:column;gap:10px;margin-top:10px}
 .vizpane h3{margin:0 0 4px;font-size:13px;color:#aaa;font-weight:500}
 iframe{width:100%;height:46vh;border:1px solid #333;border-radius:4px;background:#fff}
 iframe.empty{background:#222;border-style:dashed}
 canvas.pr{width:100%;height:auto;border:1px solid #333;border-radius:4px;
     background:#181818;display:block;cursor:crosshair}
 canvas.pr.empty{border-style:dashed;min-height:200px}
 .prCtrl{display:flex;gap:6px;align-items:center;font-size:12px;color:#888;margin-top:-4px}
 .prCtrl button{padding:2px 8px;background:#333;color:#ccc;border:1px solid #555;
                border-radius:3px;cursor:pointer;font:inherit}
 .prCtrl button:hover{background:#444}
 .echo{display:flex;gap:10px;align-items:center;padding:6px 10px;margin:6px 0;
       background:#222;border-left:4px solid #555;border-radius:4px;flex-wrap:wrap}
 .echo .swatch{width:14px;height:14px;border-radius:3px;flex-shrink:0}
 .echo .name{font-size:13px;color:#ddd;min-width:62px}
 .echo input[type=text]{width:90px}
 .echo .field{display:flex;flex-direction:column;font-size:11px;color:#888;gap:2px}
 .echo .field>div{display:flex;gap:4px;align-items:center}
 .echo button.mode{padding:4px 8px;background:#333;color:#ccc;border:1px solid #555;
                   border-radius:3px;cursor:pointer;font:inherit;font-size:12px}
 .echo button.mode.active{background:#7af;color:#000;border-color:#7af}
 .echo button.rm{padding:2px 8px;background:#522;color:#fcc;border:1px solid #844;
                 border-radius:3px;cursor:pointer;font:inherit;font-size:12px;margin-left:auto}
 #addEcho{padding:6px 14px;background:#2a4;color:#000;border:0;border-radius:4px;
          font:inherit;cursor:pointer}
 #addEcho:hover{background:#3b5}
 .modeHint{font-size:12px;color:#7af;min-height:1.2em;margin:4px 2px}
</style></head>
<body>
<h1>polytime — rhythm-scaled MIDI echoes</h1>
<div id="drop">
  <div>Drop a .mid file here, or click to choose</div>
  <div id="picked" style="margin-top:6px;color:#7af;font-size:13px"></div>
  <input id="file" type="file" accept=".mid,.midi,audio/midi" style="display:none">
</div>
<div id="recBox" style="margin-top:10px;padding:10px;border:1px solid #333;
     border-radius:6px;background:#1f1f1f;display:none">
  <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <strong style="font-size:13px;color:#bbb">MIDI keyboard:</strong>
    <span id="recDevice" style="font-size:12px;color:#7af">searching…</span>
    <button id="recBtn" disabled>● Record</button>
    <button id="recStop" disabled>■ Stop</button>
    <label class="inline">bpm
      <input id="recBpm" type="number" value="120" min="20" max="400" style="width:70px">
    </label>
    <span id="recStatus" style="font-size:12px;color:#aaa"></span>
  </div>
</div>
<div id="recNotSupported" style="margin-top:6px;font-size:12px;color:#888;display:none">
  MIDI keyboard input requires Chrome, Edge, Opera, or Brave (Web MIDI API).
</div>
<div class="row">
  <label>time sig (optional)
    <input id="tsig" type="text" placeholder="auto — e.g. 5/4">
  </label>
  <label class="inline">
    <input id="combine" type="checkbox" checked> include original in MIDI
  </label>
  <button id="addEcho">+ add echo</button>
  <button id="dl" class="dl" style="display:none">Download MIDI</button>
  <button id="quit" style="background:#444;color:#ccc;margin-left:auto"
          title="Stop the server and close polytime">× Quit</button>
</div>
<div id="echoes"></div>
<div class="modeHint" id="modeHint"></div>
<div id="status"></div>
<div class="vizpane">
  <h3>piano roll — original on top, one row per echo (+ combined if 2+ echoes)</h3>
  <canvas id="pr" class="pr empty"></canvas>
  <div class="prCtrl">
    <button data-action="zout">−</button>
    <button data-action="zin">+</button>
    <button data-action="fit">fit</button>
    <span>shift-drag = pan · wheel = zoom · click on original snaps to notes</span>
  </div>
</div>
<script>
const $=(id)=>document.getElementById(id);
const drop=$('drop'), file=$('file'), picked=$('picked'),
      dl=$('dl'), st=$('status'), modeHint=$('modeHint'),
      prEl=$('pr'), echoesEl=$('echoes');
let chosen=null, dlUrl=null, dlName=null, jobId=0, previewJobId=0;
let originalNotes=[];   // notes from last /preview
let echoMeta=null;      // {total_beats, pitch_lo, pitch_hi, beats_per_bar}
let processedVoices=null; // result of latest /process (per echo notes), keyed by index

function setStatus(msg, err=false){st.textContent=msg;st.className=err?'err':'';}

// ── Interactive piano roll ─────────────────────────────────────────────
// Single stacked roll: row 0 = original, row 1..N = one per echo, row N+1
// = optional combined overlay. Coordinates are stored in BEATS and MIDI
// pitch, never pixels — selections survive any zoom/pan.
//
// Selection model is mode-driven:
//   - mode === null               : pan/zoom only
//   - mode = {type:'source', i}   : clicks on the ORIGINAL row set echo[i]'s
//                                    source range (click+click or drag)
//   - mode = {type:'start',  i}   : single click anywhere sets echo[i]'s start
// shift+drag pans regardless of mode. Wheel zooms. Buttons fit/zoom.

const SNAP_TOLERANCE_BEATS = 0.5;
const DRAG_THRESHOLD_PX = 4;
const PALETTE = ['#3a7bd5','#d55e3a','#3ad57b','#d5c43a','#9933cc',
                 '#33aaff','#ff6699','#66cc88','#aaaa44'];

const c = prEl, ctx = c.getContext('2d');
let rows = [];               // [{label, notes, color, overlay?}]
let totalBeats = 16, pitchLo = 60, pitchHi = 72, beatsPerBar = 4;
let xMin = 0, xMax = 16;
let mode = null;             // {type:'source'|'start', echoIdx}
let pendingAnchor = null;    // beat anchor for click+click source range
let drag = null;
const ROW_MIN_H = 70;
const padL = 70, padR = 12, padT = 8, padB = 22;

function fitCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const r = c.getBoundingClientRect();
  const nrows = Math.max(1, rows.length);
  const wantedH = padT + padB + nrows * ROW_MIN_H;
  if (Math.abs(c.clientHeight - wantedH) > 2) c.style.height = wantedH + 'px';
  const r2 = c.getBoundingClientRect();
  c.width = Math.round(r2.width * dpr);
  c.height = Math.round(r2.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return r2;
}
const plotW = (r) => r.width - padL - padR;
const plotH = (r) => r.height - padT - padB;
const beatToX = (b, r) => padL + (b - xMin) / (xMax - xMin) * plotW(r);
const xToBeat = (x, r) => xMin + (x - padL) / plotW(r) * (xMax - xMin);
function rowBounds(r) {
  const n = Math.max(1, rows.length);
  const h = plotH(r) / n;
  return rows.map((_, i) => ({ y0: padT + i * h, y1: padT + (i+1) * h }));
}
function rowAtY(y, r) {
  const rb = rowBounds(r);
  for (let i = 0; i < rb.length; i++) {
    if (y >= rb[i].y0 && y < rb[i].y1) return i;
  }
  return -1;
}
function pitchToY(p, rowIdx, r) {
  const rb = rowBounds(r)[rowIdx];
  const lo = pitchLo - 1, hi = pitchHi + 1;
  return rb.y0 + 4 + (hi - p) / (hi - lo) * (rb.y1 - rb.y0 - 8);
}
function snapToOriginalNote(beat) {
  let best = beat, bestD = SNAP_TOLERANCE_BEATS;
  for (const n of originalNotes) {
    for (const t of [n.on, n.off]) {
      const d = Math.abs(t - beat);
      if (d < bestD) { bestD = d; best = t; }
    }
  }
  return best;
}
const clipBeats = (b) => Math.max(0, Math.min(totalBeats * 2, b));

function draw() {
  const r = fitCanvas();
  ctx.fillStyle = '#181818'; ctx.fillRect(0, 0, r.width, r.height);
  // bar grid
  ctx.strokeStyle = '#2a2a2a'; ctx.lineWidth = 1;
  ctx.fillStyle = '#666'; ctx.font = '11px system-ui,sans-serif';
  ctx.textBaseline = 'top';
  const firstBar = Math.floor(xMin / beatsPerBar);
  const lastBar = Math.ceil(xMax / beatsPerBar);
  for (let b = firstBar; b <= lastBar; b++) {
    const beat = b * beatsPerBar;
    const x = beatToX(beat, r);
    if (x < padL - 1 || x > r.width - padR + 1) continue;
    ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, r.height - padB); ctx.stroke();
    ctx.fillText('bar ' + (b+1), x + 3, r.height - padB + 3);
  }
  const rb = rowBounds(r);
  // source highlights on the original row (row 0)
  if (rb.length) {
    for (let i = 0; i < echoes.length; i++) {
      const src = echoes[i].source;
      if (!src) continue;
      const x0 = Math.max(padL, beatToX(src[0], r));
      const x1 = Math.min(r.width - padR, beatToX(src[1], r));
      if (x1 <= x0) continue;
      ctx.fillStyle = hexToRGBA(echoes[i].color, 0.18);
      ctx.fillRect(x0, rb[0].y0, x1 - x0, rb[0].y1 - rb[0].y0);
      ctx.strokeStyle = echoes[i].color; ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x0, rb[0].y0); ctx.lineTo(x0, rb[0].y1);
      ctx.moveTo(x1, rb[0].y0); ctx.lineTo(x1, rb[0].y1);
      ctx.stroke();
    }
  }
  // start markers on each echo's own row
  for (let i = 0; i < echoes.length; i++) {
    const ridx = i + 1; // echo row
    if (ridx >= rb.length) break;
    const startBeat = parseStartBeat(echoes[i].start);
    if (startBeat == null) continue;
    const x = beatToX(startBeat, r);
    if (x < padL - 1 || x > r.width - padR + 1) continue;
    ctx.strokeStyle = echoes[i].color; ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, rb[ridx].y0); ctx.lineTo(x, rb[ridx].y1);
    ctx.stroke();
    ctx.fillStyle = echoes[i].color;
    ctx.fillText('▶', x + 2, rb[ridx].y0 + 2);
  }
  // pending click anchor for source range
  if (pendingAnchor != null) {
    const x = beatToX(pendingAnchor, r);
    ctx.strokeStyle = '#f7c948'; ctx.setLineDash([4,3]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, r.height - padB); ctx.stroke();
    ctx.setLineDash([]);
  }
  // rows
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    if (i > 0) {
      ctx.strokeStyle = '#2a2a2a'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(padL, rb[i].y0); ctx.lineTo(r.width-padR, rb[i].y0); ctx.stroke();
    }
    ctx.fillStyle = '#bbb'; ctx.font = '11px system-ui,sans-serif';
    ctx.textBaseline = 'top';
    ctx.fillText(row.label || '', 4, rb[i].y0 + 4);
    const rowH = rb[i].y1 - rb[i].y0 - 8;
    const noteH = Math.max(2, rowH / (pitchHi - pitchLo + 3));
    if (row.overlay) {
      // combined: overlay original + all echo rows
      for (let j = 0; j < rows.length; j++) {
        if (j === i || rows[j].overlay) continue;
        ctx.fillStyle = rows[j].color || PALETTE[j % PALETTE.length];
        ctx.globalAlpha = 0.7;
        drawNotes(rows[j].notes, i, r, noteH);
      }
      ctx.globalAlpha = 1.0;
    } else {
      ctx.fillStyle = row.color || PALETTE[i % PALETTE.length];
      drawNotes(row.notes, i, r, noteH);
    }
  }
  ctx.strokeStyle = '#444'; ctx.lineWidth = 1;
  ctx.strokeRect(padL, padT, plotW(r), plotH(r));
}
function drawNotes(notes, rowIdx, r, noteH) {
  if (!notes) return;
  for (const n of notes) {
    if (n.off < xMin || n.on > xMax) continue;
    const x = beatToX(n.on, r);
    const w = Math.max(1, beatToX(n.off, r) - x);
    const y = pitchToY(n.midi, rowIdx, r) - noteH/2;
    ctx.fillRect(x, y, w, noteH);
  }
}
function hexToRGBA(hex, a) {
  const m = /^#?([a-f\\d]{2})([a-f\\d]{2})([a-f\\d]{2})$/i.exec(hex);
  if (!m) return 'rgba(120,170,255,'+a+')';
  return 'rgba('+parseInt(m[1],16)+','+parseInt(m[2],16)+','+parseInt(m[3],16)+','+a+')';
}
function parseStartBeat(s) {
  if (s == null) return null;
  s = String(s).trim();
  if (!s) return null;
  if (s.endsWith('b')) {
    const v = parseFloat(s.slice(0,-1));
    return isFinite(v) ? v * beatsPerBar : null;
  }
  const v = parseFloat(s);
  return isFinite(v) ? v : null;
}
function fitView() {
  xMin = 0;
  xMax = Math.max(4, totalBeats * 1.05);
  draw();
}
function zoomAt(factor, anchorBeat) {
  const span = (xMax - xMin) / factor;
  if (span < 0.5) return;
  const t = (anchorBeat - xMin) / (xMax - xMin);
  xMin = anchorBeat - t * span;
  xMax = anchorBeat + (1 - t) * span;
  if (xMin < -span * 0.1) { xMax -= xMin; xMin = 0; }
  draw();
}

// ── click/drag/wheel ───────────────────────────────────────────────────
c.addEventListener('mousedown', (e) => {
  const r = c.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  const beat = clipBeats(xToBeat(mx, r));
  const rowIdx = rowAtY(my, r);
  if (e.shiftKey && e.button === 0) {
    drag = { kind: 'shift-pending', startX: mx, startBeat: beat,
             rowIdx, lastX: mx, moved: false };
  } else if (e.button === 2) {
    drag = { kind: 'pan', startX: mx, lastX: mx, moved: false };
  } else {
    drag = { kind: 'pending', startX: mx, startBeat: beat, rowIdx, moved: false };
  }
});
window.addEventListener('mousemove', (e) => {
  if (!drag) return;
  const r = c.getBoundingClientRect();
  const mx = e.clientX - r.left;
  if (!drag.moved && Math.abs(mx - drag.startX) > DRAG_THRESHOLD_PX) {
    drag.moved = true;
    if (drag.kind === 'shift-pending') drag.kind = 'pan';
    else if (drag.kind === 'pending') {
      if (mode && mode.type === 'source' && drag.rowIdx === 0) {
        drag.kind = 'drag-source';
      } else {
        drag.kind = 'noop';
      }
    }
  }
  if (drag.kind === 'drag-source') {
    const cur = clipBeats(xToBeat(mx, r));
    const lo = Math.min(drag.startBeat, cur), hi = Math.max(drag.startBeat, cur);
    if (hi > lo) {
      echoes[mode.echoIdx].source = [lo, hi];
      syncEchoUI(mode.echoIdx);
      draw();
    }
  } else if (drag.kind === 'pan') {
    const dx = mx - drag.lastX;
    const dBeat = -dx / plotW(r) * (xMax - xMin);
    xMin += dBeat; xMax += dBeat;
    drag.lastX = mx;
    draw();
  }
});
window.addEventListener('mouseup', (e) => {
  if (!drag) return;
  if (!drag.moved) {
    const r = c.getBoundingClientRect();
    const mx = e.clientX - r.left;
    let beat = clipBeats(xToBeat(mx, r));
    if (mode && mode.type === 'source') {
      if (drag.rowIdx !== 0) {
        setMsg('click on the ORIGINAL row (top) to set source', true);
      } else {
        beat = snapToOriginalNote(beat);
        if (pendingAnchor == null) {
          pendingAnchor = beat;
          draw();
        } else {
          const lo = Math.min(pendingAnchor, beat), hi = Math.max(pendingAnchor, beat);
          pendingAnchor = null;
          if (hi > lo) {
            echoes[mode.echoIdx].source = [lo, hi];
            syncEchoUI(mode.echoIdx);
            setMode(null);
            schedulePreview();
          }
          draw();
        }
      }
    } else if (mode && mode.type === 'start') {
      if (drag.rowIdx === 0) beat = snapToOriginalNote(beat);
      echoes[mode.echoIdx].start = beat.toFixed(2);
      syncEchoUI(mode.echoIdx);
      setMode(null);
      schedulePreview();
      draw();
    }
  } else if (drag.kind === 'drag-source' && mode) {
    setMode(null);
    schedulePreview();
  }
  drag = null;
});
c.addEventListener('contextmenu', (e) => e.preventDefault());
c.addEventListener('wheel', (e) => {
  e.preventDefault();
  const r = c.getBoundingClientRect();
  const mx = e.clientX - r.left;
  zoomAt(Math.exp(-e.deltaY * 0.0015), xToBeat(mx, r));
}, {passive: false});

document.querySelectorAll('.prCtrl button[data-action]').forEach(btn => {
  btn.addEventListener('click', () => {
    switch (btn.dataset.action) {
      case 'zin':   zoomAt(1.4, (xMin+xMax)/2); break;
      case 'zout':  zoomAt(1/1.4, (xMin+xMax)/2); break;
      case 'fit':   fitView(); break;
    }
  });
});
window.addEventListener('resize', () => { draw(); });

// ── echo strip state ───────────────────────────────────────────────────
const echoes = [];  // [{scale, source:[s,e]|null, start, color}]
function makeEcho() {
  const i = echoes.length;
  return { scale: '3/2', source: null, start: (2 * (i+1)) + 'b',
           color: PALETTE[i % PALETTE.length] };
}
function setMode(m) {
  mode = m;
  pendingAnchor = null;
  document.querySelectorAll('.echo button.mode').forEach(b => b.classList.remove('active'));
  if (m) {
    const sel = '.echo[data-idx="'+m.echoIdx+'"] button.mode[data-mode="'+m.type+'"]';
    const btn = document.querySelector(sel);
    if (btn) btn.classList.add('active');
    modeHint.textContent =
      m.type === 'source'
        ? 'click on ORIGINAL row to set echo '+(m.echoIdx+1)+' source (click+click or drag)'
        : 'click anywhere to set echo '+(m.echoIdx+1)+' start';
  } else {
    modeHint.textContent = '';
  }
  draw();
}
function renderEchoes() {
  echoesEl.innerHTML = '';
  echoes.forEach((e, i) => {
    const div = document.createElement('div');
    div.className = 'echo';
    div.dataset.idx = i;
    div.style.borderLeftColor = e.color;
    div.innerHTML = `
      <span class="swatch" style="background:${e.color}"></span>
      <span class="name">echo ${i+1}</span>
      <label class="field">scale<input data-f="scale" type="text" value="${e.scale}"></label>
      <div class="field">source
        <div><input data-f="source" type="text" value="${e.source ? e.source[0].toFixed(2)+'..'+e.source[1].toFixed(2) : ''}" placeholder="all"><button class="mode" data-mode="source">pick</button></div>
      </div>
      <div class="field">start
        <div><input data-f="start" type="text" value="${e.start}" placeholder="2b"><button class="mode" data-mode="start">pick</button></div>
      </div>
      <button class="rm">×</button>`;
    echoesEl.appendChild(div);
    div.querySelectorAll('input[data-f]').forEach(inp => {
      inp.addEventListener('input', () => {
        const f = inp.dataset.f;
        if (f === 'source') {
          const v = inp.value.trim();
          if (v.includes('..')) {
            const [a, b] = v.split('..').map(parseFloat);
            if (isFinite(a) && isFinite(b) && b > a) e.source = [a, b];
            else e.source = null;
          } else e.source = null;
        } else {
          e[f] = inp.value;
        }
        draw();
        schedulePreview();
      });
    });
    div.querySelectorAll('button.mode').forEach(b => {
      b.addEventListener('click', () => {
        const t = b.dataset.mode;
        if (mode && mode.type === t && mode.echoIdx === i) setMode(null);
        else setMode({ type: t, echoIdx: i });
      });
    });
    div.querySelector('button.rm').addEventListener('click', () => {
      echoes.splice(i, 1);
      // re-assign colors so they stay consecutive
      echoes.forEach((x, k) => x.color = PALETTE[k % PALETTE.length]);
      if (mode && mode.echoIdx === i) setMode(null);
      renderEchoes();
      rebuildRows();
      schedulePreview();
    });
  });
}
function syncEchoUI(i) {
  const div = echoesEl.querySelector('.echo[data-idx="'+i+'"]');
  if (!div) return;
  const e = echoes[i];
  div.querySelector('input[data-f="source"]').value =
    e.source ? e.source[0].toFixed(2)+'..'+e.source[1].toFixed(2) : '';
  div.querySelector('input[data-f="start"]').value = e.start;
}

$('addEcho').addEventListener('click', () => {
  if (echoes.length >= 8) { setStatus('max 8 echoes', true); return; }
  echoes.push(makeEcho());
  renderEchoes();
  rebuildRows();
  schedulePreview();
});

// ── rows = original + per-echo + (combined if 2+) ──────────────────────
function rebuildRows() {
  rows = [{ label: 'original', notes: originalNotes, color: '#888' }];
  for (let i = 0; i < echoes.length; i++) {
    const v = processedVoices && processedVoices[i+1];
    rows.push({ label: 'echo '+(i+1), notes: v ? v.notes : [], color: echoes[i].color });
  }
  if (echoes.length >= 2) {
    rows.push({ label: 'combined', overlay: true });
  }
  draw();
}

let previewTimer = null;
function schedulePreview() {
  if (!chosen || !echoes.length) { draw(); return; }
  clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, 350);
}
async function runPreview() {
  if (!chosen || !echoes.length) return;
  const mine = ++previewJobId;
  const fd = buildFormData();
  try {
    const r = await fetch('/process', { method: 'POST', body: fd });
    const j = await r.json();
    if (mine !== previewJobId) return;
    if (!r.ok) { setStatus('preview: '+(j.error||'failed'), true); return; }
    setStatus('time signature: '+j.detected_ts);
    // Map j.voices to row indices. Server returns theme first if combine,
    // then echo_1, echo_2… We want echoes by their original index.
    processedVoices = {};
    let echoCursor = 1;
    for (const v of j.voices) {
      if (v.label === 'theme' || v.label.startsWith('theme')) {
        processedVoices[0] = v;
      } else {
        processedVoices[echoCursor++] = v;
      }
    }
    rebuildRows();
    dlUrl = j.midi_data_url; dlName = j.midi_filename;
    dl.style.display = 'inline-block';
  } catch (e) {
    if (mine === previewJobId) setStatus('preview error: '+e.message, true);
  }
}
function buildFormData() {
  const fd = new FormData();
  fd.append('mid', chosen);
  fd.append('tsig', $('tsig').value);
  fd.append('combine', $('combine').checked ? '1' : '0');
  fd.append('echoes', JSON.stringify(echoes.map(e => ({
    scale: e.scale,
    source: e.source ? (e.source[0]+'..'+e.source[1]) : '',
    start: e.start || '0',
  }))));
  return fd;
}
function setMsg(m, err) { setStatus(m, !!err); }
$('tsig').addEventListener('input', schedulePreview);
$('combine').addEventListener('change', schedulePreview);




async function pickFile(f){
  const mine = ++jobId;
  chosen=f; picked.textContent=f.name;
  originalNotes=[]; processedVoices=null;
  rebuildRows(); prEl.classList.add('empty');
  dl.style.display='none'; dlUrl=null; dlName=null;
  setStatus('loading preview...');
  const fd=new FormData(); fd.append('mid', f);
  try{
    const r=await fetch('/preview',{method:'POST',body:fd});
    const j=await r.json();
    if(mine !== jobId) return;
    if(!r.ok) throw new Error(j.error||'preview failed');
    originalNotes = j.notes;
    totalBeats = j.total_beats || 16;
    pitchLo = j.pitch_lo ?? 60;
    pitchHi = j.pitch_hi ?? 72;
    beatsPerBar = j.beats_per_bar || 4;
    if (!echoes.length) { echoes.push(makeEcho()); renderEchoes(); }
    rebuildRows();
    fitView();
    prEl.classList.remove('empty');
    $('tsig').placeholder='auto — detected '+j.detected_ts;
    setStatus('detected time signature: '+j.detected_ts+' · '+j.notes.length+' notes');
    schedulePreview();
  }catch(e){
    if(mine !== jobId) return;
    setStatus('error: '+e.message, true);
  }
}

// preventDefault on the whole window so the browser never tries to navigate
// to a dropped file (which is what eats the first drop event).
['dragenter','dragover','drop'].forEach(ev=>
  window.addEventListener(ev, e=>e.preventDefault()));
drop.addEventListener('click',()=>file.click());
file.addEventListener('change',e=>{if(e.target.files[0])pickFile(e.target.files[0]);});
drop.addEventListener('dragenter',e=>{e.preventDefault();drop.classList.add('hover');});
drop.addEventListener('dragover',e=>{e.preventDefault();drop.classList.add('hover');});
drop.addEventListener('dragleave',e=>{drop.classList.remove('hover');});
drop.addEventListener('drop',e=>{
  e.preventDefault();
  drop.classList.remove('hover');
  const f=e.dataTransfer&&e.dataTransfer.files&&e.dataTransfer.files[0];
  if(f) pickFile(f);
});

dl.addEventListener('click',()=>{
  if(!dlUrl)return;
  const a=document.createElement('a');a.href=dlUrl;a.download=dlName;a.click();
});

// Keep-alive: ping every 5s so the server knows we're still here. If the
// user closes the tab, the pings stop, and the server self-terminates after
// ~20s. Also send an explicit shutdown beacon on unload as a fast path.
function heartbeat(){ fetch('/heartbeat').catch(()=>{}); }
setInterval(heartbeat, 10000);
// Background tabs get their timers throttled (Chrome pauses them after ~5min);
// fire an immediate heartbeat whenever the tab becomes visible again so the
// server's watchdog doesn't decide we're gone.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') heartbeat();
});
// Distinguish "user closed the tab" (intent to quit) from a navigation:
// only send shutdown beacon on pagehide with persisted=false (real close).
window.addEventListener('pagehide', (e) => {
  if (!e.persisted) {
    try { navigator.sendBeacon('/shutdown'); } catch (e2) {}
  }
});
$('quit').addEventListener('click', () => {
  if (!confirm('Stop polytime?')) return;
  fetch('/shutdown', {method:'POST'}).catch(()=>{});
  document.body.innerHTML='<div style=\"padding:40px;font-family:sans-serif;'
    +'color:#aaa;text-align:center\">polytime stopped. You can close this tab.</div>';
});

// ── MIDI keyboard input (Web MIDI API) ──────────────────────────────────
const recBox=$('recBox'), recDevice=$('recDevice'), recBtn=$('recBtn'),
      recStop=$('recStop'), recStatus=$('recStatus'), recBpm=$('recBpm');
let midiAccess=null, recording=false, recStart=0, recEvents=[], openNotes={},
    recTimer=null, activeInputs=[];

if (navigator.requestMIDIAccess) {
  recBox.style.display='block';
  navigator.requestMIDIAccess().then(setupMidi, () => {
    recDevice.textContent='permission denied';
  });
} else {
  $('recNotSupported').style.display='block';
}

function setupMidi(access) {
  midiAccess=access;
  refreshInputs();
  access.onstatechange=refreshInputs;
}
function refreshInputs() {
  for (const inp of activeInputs) inp.onmidimessage=null;
  activeInputs=[];
  const names=[];
  for (const inp of midiAccess.inputs.values()) {
    inp.onmidimessage=onMidi;
    activeInputs.push(inp);
    names.push(inp.name);
  }
  if (names.length) {
    recDevice.textContent=names.join(', ');
    recBtn.disabled=false;
  } else {
    recDevice.textContent='no device — plug one in';
    recBtn.disabled=true;
  }
}
function onMidi(e) {
  if (!recording) return;
  const [status, data1, data2] = e.data;
  const cmd = status & 0xf0;
  const t = performance.now() - recStart;
  if (cmd === 0x90 && data2 > 0) {
    openNotes[data1] = t;
  } else if (cmd === 0x80 || (cmd === 0x90 && data2 === 0)) {
    const onT = openNotes[data1];
    if (onT === undefined) return;
    delete openNotes[data1];
    recEvents.push({midi: data1, onMs: onT, offMs: t});
  }
}
recBtn.addEventListener('click', () => {
  recording=true; recEvents=[]; openNotes={}; recStart=performance.now();
  recBtn.disabled=true; recStop.disabled=false;
  recStatus.textContent='recording — play now';
  recStatus.style.color='#f77';
  recTimer=setInterval(()=>{
    const s=Math.floor((performance.now()-recStart)/1000);
    recStatus.textContent=`recording ${s}s · ${recEvents.length} notes`;
  }, 250);
});
recStop.addEventListener('click', () => {
  recording=false; recBtn.disabled=false; recStop.disabled=true;
  clearInterval(recTimer);
  recStatus.style.color='#aaa';
  for (const midi in openNotes) {
    recEvents.push({midi: +midi, onMs: openNotes[midi],
                    offMs: performance.now()-recStart});
  }
  openNotes={};
  if (!recEvents.length) {
    recStatus.textContent='nothing recorded';
    return;
  }
  const bpm = Math.max(20, Math.min(400, parseFloat(recBpm.value) || 120));
  const blob = buildMidi(recEvents, bpm);
  const f = new File([blob], 'recording.mid', {type: 'audio/midi'});
  recStatus.textContent=`captured ${recEvents.length} notes`;
  pickFile(f);
});

// Minimal Standard MIDI File writer (Type-1, one track) — produces a file
// any DAW / parser will accept. ppq=480, includes a tempo meta so playback
// matches what was captured.
function buildMidi(notes, bpm) {
  const PPQ = 480;
  const msPerBeat = 60000 / bpm;
  const anchor = Math.min(...notes.map(n => n.onMs));
  const evs = [];
  for (const n of notes) {
    const onTick  = Math.max(0, Math.round((n.onMs  - anchor) / msPerBeat * PPQ));
    const offTick = Math.max(onTick + 1,
                             Math.round((n.offMs - anchor) / msPerBeat * PPQ));
    evs.push({tick: onTick,  type: 'on',  midi: n.midi});
    evs.push({tick: offTick, type: 'off', midi: n.midi});
  }
  // note_off before note_on at the same tick so zero-gap legato survives.
  evs.sort((a, b) => a.tick - b.tick || (a.type === 'off' ? -1 : 1));
  function vlq(n) {
    if (n < 0) n = 0;
    const out = [n & 0x7f];
    n >>= 7;
    while (n > 0) { out.unshift((n & 0x7f) | 0x80); n >>= 7; }
    return out;
  }
  const body = [];
  // tempo meta (FF 51 03 ttt ttt ttt)
  const usPerBeat = Math.round(60000000 / bpm);
  body.push(0, 0xff, 0x51, 0x03,
            (usPerBeat >> 16) & 0xff, (usPerBeat >> 8) & 0xff, usPerBeat & 0xff);
  // 4/4 time-sig meta (the polytime UI lets the user override before generating)
  body.push(0, 0xff, 0x58, 0x04, 4, 2, 24, 8);
  let prev = 0;
  for (const e of evs) {
    body.push(...vlq(e.tick - prev));
    body.push(e.type === 'on' ? 0x90 : 0x80, e.midi,
              e.type === 'on' ? 80 : 0);
    prev = e.tick;
  }
  // end of track
  body.push(0, 0xff, 0x2f, 0x00);

  const trackLen = body.length;
  const header = [
    0x4d, 0x54, 0x68, 0x64, 0, 0, 0, 6,
    0, 1, 0, 1,
    (PPQ >> 8) & 0xff, PPQ & 0xff,
  ];
  const trackHdr = [
    0x4d, 0x54, 0x72, 0x6b,
    (trackLen >>> 24) & 0xff, (trackLen >>> 16) & 0xff,
    (trackLen >>>  8) & 0xff,  trackLen        & 0xff,
  ];
  return new Uint8Array([...header, ...trackHdr, ...body]);
}
</script>
</body></html>
"""


def parse_multipart(body: bytes, boundary: bytes) -> dict[str, bytes | tuple[str, bytes]]:
    """Tiny multipart/form-data parser. Plain fields → bytes; file fields → (filename, bytes)."""
    out: dict[str, bytes | tuple[str, bytes]] = {}
    sep = b"--" + boundary
    for part in body.split(sep):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        header_blob, _, content = part.partition(b"\r\n\r\n")
        headers = header_blob.decode("utf-8", "replace")
        disp = next((ln for ln in headers.split("\r\n")
                     if ln.lower().startswith("content-disposition")), "")
        name = None
        filename = None
        for piece in disp.split(";"):
            piece = piece.strip()
            if piece.startswith("name="):
                name = piece.split("=", 1)[1].strip('"')
            elif piece.startswith("filename="):
                filename = piece.split("=", 1)[1].strip('"')
        if name is None:
            continue
        if content.endswith(b"\r\n"):
            content = content[:-2]
        out[name] = (filename, content) if filename is not None else content
    return out


def _voices_from_midi(path: Path) -> tuple[list[dict], float, int, int]:
    """Read the output MIDI back into a per-track note list for the client
    piano roll. Tracks with no notes (e.g. tempo-only) are dropped."""
    import mido
    mf = mido.MidiFile(str(path))
    ppq = mf.ticks_per_beat
    voices = []
    all_pitches: list[int] = []
    max_end = 0.0
    for track in mf.tracks:
        name = None
        for msg in track:
            if msg.type == "track_name":
                # save_mido writes "<part>/<voice>". In polytime that's
                # "X/X" because part_name == voice_id; un-double it. Voice IDs
                # themselves can contain '/', so we can't just split-and-keep-last.
                raw = msg.name
                if "/" in raw:
                    half = len(raw) // 2
                    if raw[:half] == raw[half+1:] and raw[half] == "/":
                        raw = raw[:half]
                name = raw
                break
        if name is None:
            name = "voice"
        absolute = 0
        open_notes: dict[int, int] = {}
        notes: list[dict] = []
        for msg in track:
            absolute += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                open_notes[msg.note] = absolute
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                start = open_notes.pop(msg.note, None)
                if start is None:
                    continue
                on = start / ppq
                off = absolute / ppq
                notes.append({"midi": msg.note, "on": on, "off": off})
                all_pitches.append(msg.note)
                if off > max_end:
                    max_end = off
        if notes:
            voices.append({"label": name, "notes": notes})
    if not all_pitches:
        all_pitches = [60, 72]
    return voices, max(max_end, 4.0), min(all_pitches), max(all_pitches)


def parse_ts(s: str | None) -> TimeSignature | None:
    if not s:
        return None
    s = s.strip()
    if not s or "/" not in s:
        return None
    num, den = s.split("/", 1)
    return TimeSignature(int(num), int(den))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a, **_k):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_fields(self) -> dict:
        ctype = self.headers.get("Content-Type", "")
        if "boundary=" not in ctype:
            raise ValueError("expected multipart/form-data")
        boundary = ctype.split("boundary=", 1)[1].strip().strip('"').encode()
        length = int(self.headers.get("Content-Length", "0"))
        return parse_multipart(self.rfile.read(length), boundary)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path == "/heartbeat":
            global LAST_HEARTBEAT
            LAST_HEARTBEAT = time.monotonic()
            self._send(200, b"ok", "text/plain")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        try:
            if self.path == "/preview":
                self._handle_preview()
            elif self.path == "/process":
                self._handle_process()
            elif self.path == "/shutdown":
                self._send(200, b"bye", "text/plain")
                threading.Thread(
                    target=lambda: (time.sleep(0.2), os._exit(0)),
                    daemon=True,
                ).start()
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:
            traceback.print_exc()
            self._send(400, json.dumps({"error": str(e)}).encode("utf-8"),
                       "application/json; charset=utf-8")

    def _handle_preview(self):
        fields = self._read_fields()
        mid_field = fields.get("mid")
        if not isinstance(mid_field, tuple):
            raise ValueError("missing MIDI file")
        _, mid_bytes = mid_field

        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            f.write(mid_bytes)
            in_path = Path(f.name)
        try:
            detected = detect_time_signature(in_path)
            ts = detected or TimeSignature(4, 4)
            from score_io.live.midi_file import load_mido
            from polytime import _flatten_score
            from model.events import Note, Chord
            score = load_mido(str(in_path), time_signature=ts)
            theme = _flatten_score(score)
            # Flatten chords to individual notes so the client gets a uniform
            # {midi, on, off} list.
            notes_out = []
            for e in theme.events:
                on = float(e.offset)
                off = on + float(e.duration.actual_beats)
                if isinstance(e, Chord):
                    for p in e.pitches:
                        notes_out.append({"midi": p.midi, "on": on, "off": off})
                elif isinstance(e, Note):
                    notes_out.append({"midi": e.pitch.midi, "on": on, "off": off})
            total_beats = max((n["off"] for n in notes_out), default=4.0)
            pitches = [n["midi"] for n in notes_out] or [60, 72]
        finally:
            try: in_path.unlink()
            except OSError: pass

        payload = {
            "notes": notes_out,
            "total_beats": total_beats,
            "pitch_lo": min(pitches),
            "pitch_hi": max(pitches),
            "beats_per_bar": float(ts.beats_per_measure),
            "detected_ts": f"{ts.numerator}/{ts.denominator}" +
                           ("" if detected else " (default)"),
        }
        self._send(200, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _handle_process(self):
        fields = self._read_fields()
        mid_field = fields.get("mid")
        if not isinstance(mid_field, tuple):
            raise ValueError("missing MIDI file")
        filename, mid_bytes = mid_field
        tsig_str = (fields.get("tsig") or b"").decode().strip()
        combine = (fields.get("combine") or b"1").decode().strip() == "1"
        echoes_raw = (fields.get("echoes") or b"[]").decode().strip()
        try:
            echoes_list = json.loads(echoes_raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"bad echoes JSON: {e}")
        if not isinstance(echoes_list, list) or not echoes_list:
            raise ValueError("provide at least one echo voice")
        if len(echoes_list) > 8:
            raise ValueError("max 8 echo voices")

        base_bpm = 120.0
        scales = tuple(parse_scale(str(e.get("scale", "")).strip(), base_bpm)
                       for e in echoes_list)
        stem = Path(filename or "input").stem

        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as f:
            f.write(mid_bytes)
            in_path = Path(f.name)
        out_mid = Path(tempfile.mkstemp(suffix=".mid")[1])

        try:
            detected_bpm = detect_bpm(in_path)
            if detected_bpm:
                base_bpm = detected_bpm
                # Re-parse scales now that we know the file's tempo (only
                # changes results for entries that used the `bpm` suffix).
                scales = tuple(parse_scale(str(e.get("scale", "")).strip(), base_bpm)
                               for e in echoes_list)
            ts_override = parse_ts(tsig_str)
            detected = detect_time_signature(in_path)
            ts = ts_override or detected or TimeSignature(4, 4)
            ts_label = f"{ts.numerator}/{ts.denominator}"
            if ts_override:
                ts_label += " (override)"
            elif not detected:
                ts_label += " (default)"

            cap = ts.beats_per_measure
            ats = tuple(
                _parse_when(str(e.get("start", "")).strip() or "0", cap)
                for e in echoes_list
            )
            theme_ranges = tuple(
                parse_range(str(e.get("source", "")).strip() or "", cap)
                for e in echoes_list
            )
            mid_path, _viz_path = polytime(
                in_path, at=ats[0], scales=scales, ats=ats,
                out=out_mid, diff_png=None, time_signature=ts,
                combine=combine, viz_connectors=False,
                theme_ranges=theme_ranges,
            )
            mid_data = mid_path.read_bytes()
            # Read the produced MIDI back to recover the per-track note streams
            # — much simpler than threading them back through polytime().
            voices_payload, total_beats, pitch_lo, pitch_hi = _voices_from_midi(
                mid_path
            )
        finally:
            for p in (in_path, out_mid):
                try: p.unlink()
                except OSError: pass

        payload = {
            "voices": voices_payload,
            "total_beats": total_beats,
            "pitch_lo": pitch_lo,
            "pitch_hi": pitch_hi,
            "beats_per_bar": float(ts.beats_per_measure),
            "midi_data_url": "data:audio/midi;base64," +
                             base64.b64encode(mid_data).decode("ascii"),
            "midi_filename": f"{stem}_polytime.mid",
            "detected_ts": ts_label,
        }
        self._send(200, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _watchdog():
    """Exit if the browser hasn't heart-beaten in HEARTBEAT_TIMEOUT_S.
    Grace period: ignore the first ~15s so a slow browser launch isn't fatal."""
    grace_until = time.monotonic() + 15.0
    while True:
        time.sleep(HEARTBEAT_CHECK_S)
        now = time.monotonic()
        if now < grace_until:
            continue
        if now - LAST_HEARTBEAT > HEARTBEAT_TIMEOUT_S:
            os._exit(0)


def main():
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Timer(0.4, lambda: webbrowser.open_new_tab(url)).start()
    threading.Thread(target=_watchdog, daemon=True).start()
    print(f"polytime running at {url}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
