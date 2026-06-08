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
 #drop{border:2px dashed #555;border-radius:8px;padding:18px;text-align:center;
       cursor:pointer;background:#222;transition:.15s;font-size:13px;color:#aaa}
 #drop.hover{border-color:#7af;background:#2a3040}
 #drop input{display:none}
 .row{display:flex;gap:14px;margin:12px 0;flex-wrap:wrap;align-items:end}
 label{display:flex;flex-direction:column;font-size:13px;color:#aaa}
 label.inline{flex-direction:row;align-items:center;gap:6px;color:#ddd}
 input[type=text],input[type=number]{margin-top:4px;padding:6px 8px;background:#222;
   color:#eee;border:1px solid #444;border-radius:4px;font:inherit;width:110px}
 button{padding:8px 16px;background:#4a7;color:#000;border:0;border-radius:4px;
        font:inherit;font-weight:600;cursor:pointer}
 button:disabled{background:#3a3a3a;color:#777;cursor:default}
 #pauseBtn:not(:disabled){background:#d4a017;color:#000}
 #stopBtn:not(:disabled){background:#c25a5a;color:#fff}
 button.dl{background:#7af;font-size:14px;padding:10px 22px}
 #dlBar{margin:12px 0;display:none}
 details.extras{margin:10px 0;background:#1f1f1f;border:1px solid #333;
                border-radius:6px;padding:6px 10px}
 details.extras>summary{cursor:pointer;color:#aaa;font-size:12px;list-style:none;
                        user-select:none}
 details.extras>summary::-webkit-details-marker{display:none}
 details.extras>summary::before{content:'\\25B8 ';color:#666}
 details.extras[open]>summary::before{content:'\\25BE ';color:#888}
 details.extras .row{margin:8px 0 4px}
 details.extras .hint{font-size:11px;color:#777;margin:2px 0 6px;line-height:1.4}
 #status{margin:6px 0;font-size:13px;color:#aaa;min-height:1.2em}
 #status.err{color:#f77}
 .vizpane{display:flex;flex-direction:column;gap:10px;margin-top:10px}
 .vizpane h3{margin:0 0 4px;font-size:13px;color:#aaa;font-weight:500}
 iframe{width:100%;height:46vh;border:1px solid #333;border-radius:4px;background:#fff}
 iframe.empty{background:#222;border-style:dashed}
 canvas.pr{width:100%;height:auto;border:1px solid #333;border-radius:4px;
     background:#181818;display:block;cursor:crosshair}
 canvas.pr.empty{border-style:dashed;min-height:240px}
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
  <div>Drop .mid files or click</div>
  <div id="picked" style="margin-top:6px;color:#7af;font-size:13px"></div>
  <input id="file" type="file" accept=".mid,.midi,audio/midi" multiple style="display:none">
</div>
<div id="inputList" style="margin-top:8px;display:flex;flex-direction:column;gap:6px"></div>
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
    <label class="inline" title="Hear what you play through the internal synth">
      <input id="recMonitor" type="checkbox" checked> 🔊 Monitor input
    </label>
    <span id="recStatus" style="font-size:12px;color:#aaa"></span>
  </div>
</div>
<div id="recNotSupported" style="margin-top:6px;font-size:12px;color:#888;display:none">
  MIDI keyboard input requires Chrome, Edge, Opera, or Brave (Web MIDI API).
</div>
<div class="row">
  <button id="addEcho">+ add polytime</button>
  <button id="quit" style="background:#444;color:#ccc;margin-left:auto"
          title="Stop the server and close polytime">× Quit</button>
</div>
<details class="extras">
  <summary>extra options</summary>
  <div class="row">
    <label>time signature
      <input id="tsig" type="text" placeholder="auto — e.g. 5/4">
    </label>
    <label>output crop (beats)
      <input id="outCrop" type="text" placeholder="full · or 0..16">
    </label>
  </div>
  <div class="hint">
    <b>time signature</b> overrides what's in the file (only affects how bars/beats line up visually).
    <b>output crop</b> trims the final MIDI to a beat range — e.g. <code>0..16</code> keeps only the first 16 beats.
  </div>
</details>
<div id="echoes"></div>
<div class="modeHint" id="modeHint"></div>
<div id="status"></div>
<div id="dlBar"><button id="dl" class="dl">⬇ Download MIDI</button></div>
<div id="playBox" style="margin-top:10px;padding:10px;border:1px solid #333;
     border-radius:6px;background:#1f1f1f;display:none">
  <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <strong style="font-size:13px;color:#bbb">Live playback:</strong>
    <select id="playOut" style="padding:6px 8px;background:#222;color:#eee;
            border:1px solid #444;border-radius:4px;font:inherit;max-width:240px"></select>
    <button id="playBtn" disabled>▶ Play</button>
    <button id="pauseBtn" disabled>⏸ Pause</button>
    <button id="stopBtn" disabled>⏹ Stop</button>
    <label class="inline">speed
      <input id="playSpeed" type="range" min="0.25" max="2" step="0.05" value="1"
             style="width:110px">
      <span id="playSpeedVal" style="width:48px;color:#7af">1.00×</span>
    </label>
    <label class="inline">volume
      <input id="playVol" type="range" min="0" max="127" step="1" value="100"
             style="width:90px">
      <span id="playVolVal" style="width:30px;color:#7af">100</span>
    </label>
    <label class="inline">
      <input id="playLoop" type="checkbox"> loop
    </label>
    <span id="playStatus" style="font-size:12px;color:#aaa"></span>
  </div>
  <div id="playVoices" style="display:none"></div>
</div>
<div id="playNotSupported" style="margin-top:6px;font-size:12px;color:#888;display:none">
  Live playback requires Chrome, Edge, Opera, or Brave (Web MIDI API).
</div>
<div class="vizpane">
  <h3>piano roll</h3>
  <canvas id="pr" class="pr empty"></canvas>
  <div class="prCtrl">
    <button data-action="zout" title="zoom out">−</button>
    <button data-action="zin" title="zoom in">+</button>
    <button data-action="fit" title="fit to data">fit</button>
    <label style="margin-left:14px;color:#aaa" title="Show one row per polytime, plus the combined overlay. Off shows only the combined result.">
      <input type="checkbox" id="combinedOnly"> combined only
    </label>
    <span style="margin-left:auto;color:#666;font-size:11px">drag to pan · scroll to zoom</span>
  </div>
</div>
<script>
const $=(id)=>document.getElementById(id);
const drop=$('drop'), file=$('file'), picked=$('picked'),
      dl=$('dl'), dlBar=$('dlBar'), st=$('status'), modeHint=$('modeHint'),
      prEl=$('pr'), echoesEl=$('echoes');
let dlUrl=null, dlName=null, jobId=0, previewJobId=0;
const inputs=[];  // [{id, file, name, offset:number, kind:'file'|'rec'}]
let nextInputId=1;
let originalNotes=[];   // flat union of all included inputs (kept for snap helpers)
let inputNotes=[];      // per-input notes from /preview: [{id,name,offset,notes,...}]
let echoMeta=null;      // {total_beats, pitch_lo, pitch_hi, beats_per_bar}
let processedVoices=null; // result of latest /process (per echo notes), keyed by index
let detectedBpm=120;    // from /preview; speed slider multiplies this

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
// Hoisted: the prCtrl IIFE below reads combinedOnly before rebuildRows is
// reached. With `let` declared at rebuildRows the IIFE hit the temporal
// dead zone and crashed init — breaking every event handler after it.
// Combined-only is the default — most users want the final result, not the
// internal per-voice rows. Persists via localStorage: '0' explicitly opts
// out, anything else (including absent) → on.
let combinedOnly = localStorage.getItem('combinedOnly') !== '0';
let ROW_MIN_H = (() => {
  const saved = parseInt(localStorage.getItem('rowMinH') || '', 10);
  return (saved >= 40 && saved <= 400) ? saved : 120;
})();
const ROW_H_STEP = 20, ROW_H_MIN = 40, ROW_H_MAX = 400;
function bumpRowH(delta) {
  ROW_MIN_H = Math.max(ROW_H_MIN, Math.min(ROW_H_MAX, ROW_MIN_H + delta));
  localStorage.setItem('rowMinH', String(ROW_MIN_H));
  fitCanvas(); draw();
}
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
// Per-row [lo, hi] computed from the row's own notes (or echoes' notes for
// the combined overlay). Transposed/inverted echoes can sit far outside the
// source range; using global pitchLo/pitchHi caused notes to render outside
// row bounds and overlap neighbours.
function rowPitchRange(rowIdx) {
  const row = rows[rowIdx];
  if (!row) return [pitchLo, pitchHi];
  let lo = Infinity, hi = -Infinity;
  const eat = (ns) => { for (const n of (ns || [])) {
    if (n.midi < lo) lo = n.midi; if (n.midi > hi) hi = n.midi;
  }};
  if (row.overlay) {
    for (let k = 0; k < echoes.length; k++) {
      if (echoes[k].include === false) continue;
      const pv = processedVoices && processedVoices[k+1];
      if (pv) eat(pv.notes);
    }
  } else {
    eat(row.notes);
  }
  if (!isFinite(lo)) { lo = pitchLo; hi = pitchHi; }
  if (hi - lo < 12) { const mid = (lo + hi) / 2; lo = mid - 6; hi = mid + 6; }
  return [lo, hi];
}
function pitchToY(p, rowIdx, r) {
  const rb = rowBounds(r)[rowIdx];
  const [rlo, rhi] = rowPitchRange(rowIdx);
  const lo = rlo - 1, hi = rhi + 1;
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
  // Snap to the nearest integer beat too — small recording-jitter offsets
  // (15.96 → 16) shouldn't bleed into the output crop.
  const ib = Math.round(best);
  if (Math.abs(best - ib) <= 0.15) best = ib;
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
  // Source highlights paint on whatever input row the echo points at.
  if (rb.length) {
    for (let i = 0; i < echoes.length; i++) {
      const src = echoes[i].source;
      if (!src) continue;
      const sid = echoes[i].source_id;
      let targetIdx = -1;
      for (let k = 0; k < rows.length; k++) {
        if (rows[k].kind === 'input' && (sid == null || rows[k].inputId === sid)) {
          targetIdx = k; break;
        }
      }
      if (targetIdx < 0) continue;
      const x0 = Math.max(padL, beatToX(src[0], r));
      const x1 = Math.min(r.width - padR, beatToX(src[1], r));
      if (x1 <= x0) continue;
      ctx.fillStyle = hexToRGBA(echoes[i].color, 0.18);
      ctx.fillRect(x0, rb[targetIdx].y0, x1 - x0, rb[targetIdx].y1 - rb[targetIdx].y0);
      ctx.strokeStyle = echoes[i].color; ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x0, rb[targetIdx].y0); ctx.lineTo(x0, rb[targetIdx].y1);
      ctx.moveTo(x1, rb[targetIdx].y0); ctx.lineTo(x1, rb[targetIdx].y1);
      ctx.stroke();
    }
  }
  // start markers on each echo's own row (locate by kind+echoIdx, not assumed offset)
  for (let i = 0; i < echoes.length; i++) {
    let ridx = -1;
    for (let k = 0; k < rows.length; k++) {
      if (rows[k].kind === 'echo' && rows[k].echoIdx === i) { ridx = k; break; }
    }
    if (ridx < 0 || ridx >= rb.length) continue;
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
    const [rlo, rhi] = rowPitchRange(i);
    const noteH = Math.max(2, rowH / (rhi - rlo + 3));
    if (row.overlay) {
      // Combined output = every included polytime. Read notes/colors from
      // `echoes` directly so the overlay still renders when per-voice rows
      // are hidden ("combined only" mode).
      for (let k = 0; k < echoes.length; k++) {
        const ek = echoes[k];
        if (ek.include === false) continue;
        const pv = processedVoices && processedVoices[k+1];
        if (!pv || !pv.notes) continue;
        ctx.fillStyle = ek.color || PALETTE[k % PALETTE.length];
        ctx.globalAlpha = 0.7;
        // Preserve per-echo crops by passing the echo's pid into drawNotes
        // via a synthetic row entry temporarily — cheapest path that keeps
        // applyRowCrops working without changing its signature.
        const saved = rows[i].pid;
        rows[i].pid = 'echo:'+ek.id;
        drawNotes(pv.notes, i, r, noteH);
        rows[i].pid = saved;
      }
      ctx.globalAlpha = 1.0;
    } else {
      ctx.fillStyle = row.color || PALETTE[i % PALETTE.length];
      drawNotes(row.notes, i, r, noteH);
    }
  }
  // Playhead — vertical sweep line spanning every row.
  if (playheadBeat != null) {
    const x = beatToX(playheadBeat, r);
    if (x >= padL - 1 && x <= r.width - padR + 1) {
      ctx.strokeStyle = '#ffec5c'; ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(x, padT); ctx.lineTo(x, r.height - padB);
      ctx.stroke();
    }
  }
  ctx.strokeStyle = '#444'; ctx.lineWidth = 1;
  ctx.strokeRect(padL, padT, plotW(r), plotH(r));
}
function applyRowCrops(notes, pid) {
  // Honor the per-row crop + the combined "global" crop from the player
  // panel so what you see equals what you hear (and what you'd download
  // if cropping ever extends to output). Notes straddling a boundary get
  // clipped; fully-outside notes are dropped.
  if (!notes || !notes.length) return notes;
  const s = pid ? voiceSettings.get(pid) : null;
  const global = combinedCropRange();
  const local = s ? parseCrop(s.crop) : null;
  if (!local && !global) return notes;
  const out = [];
  for (const n of notes) {
    let on = n.on, off = n.off, kill = false;
    for (const r of [local, global]) {
      if (!r) continue;
      if (off <= r[0] || on >= r[1]) { kill = true; break; }
      on = Math.max(on, r[0]); off = Math.min(off, r[1]);
    }
    if (kill || off <= on) continue;
    out.push({ midi: n.midi, on, off });
  }
  return out;
}
function drawNotes(notes, rowIdx, r, noteH) {
  if (!notes) return;
  const filtered = applyRowCrops(notes, rows[rowIdx] && rows[rowIdx].pid);
  for (const n of filtered) {
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
  if (e.button === 2) {
    drag = { kind: 'pan', startX: mx, lastX: mx, moved: false };
  } else {
    // Default left-click: pan unless a pick mode is active. In source mode
    // a drag on the original row sets the range; otherwise the click sets
    // the start/source on mouseup.
    drag = { kind: 'pending', startX: mx, startBeat: beat, rowIdx, moved: false };
  }
});
window.addEventListener('mousemove', (e) => {
  if (!drag) return;
  const r = c.getBoundingClientRect();
  const mx = e.clientX - r.left;
  if (!drag.moved && Math.abs(mx - drag.startX) > DRAG_THRESHOLD_PX) {
    drag.moved = true;
    if (drag.kind === 'pending') {
      const r2 = rows[drag.rowIdx];
      if (mode && mode.type === 'source' && r2 && r2.kind === 'input') {
        drag.kind = 'drag-source';
      } else {
        drag.kind = 'pan';
        drag.lastX = drag.startX;
      }
    }
  }
  if (drag.kind === 'drag-source') {
    const cur = clipBeats(xToBeat(mx, r));
    const lo = Math.min(drag.startBeat, cur), hi = Math.max(drag.startBeat, cur);
    if (hi > lo) {
      echoes[mode.echoIdx].source = [lo, hi];
      const startRow = rows[drag.rowIdx];
      if (startRow && startRow.kind === 'input') {
        echoes[mode.echoIdx].source_id = startRow.inputId;
      }
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
      const clickedRow = rows[drag.rowIdx];
      if (!clickedRow || clickedRow.kind !== 'input') {
        setMsg('click on an INPUT row to set source', true);
      } else {
        beat = snapToOriginalNote(beat);
        if (pendingAnchor == null) {
          pendingAnchor = beat;
          // Lock the source input on the first click; second click locks the range.
          echoes[mode.echoIdx].source_id = clickedRow.inputId;
          syncEchoUI(mode.echoIdx);
          draw();
        } else {
          const lo = Math.min(pendingAnchor, beat), hi = Math.max(pendingAnchor, beat);
          pendingAnchor = null;
          if (hi > lo) {
            echoes[mode.echoIdx].source = [lo, hi];
            echoes[mode.echoIdx].source_id = clickedRow.inputId;
            syncEchoUI(mode.echoIdx);
            setMode(null);
            schedulePreview();
          }
          draw();
        }
      }
    } else if (mode && mode.type === 'start') {
      const clickedRow = rows[drag.rowIdx];
      if (clickedRow && clickedRow.kind === 'input') beat = snapToOriginalNote(beat);
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


document.querySelectorAll('.prCtrl button[data-action]').forEach(btn => {
  btn.addEventListener('click', () => {
    switch (btn.dataset.action) {
      case 'zin':   zoomAt(1.4, (xMin+xMax)/2); break;
      case 'zout':  zoomAt(1/1.4, (xMin+xMax)/2); break;
      case 'fit':   fitView(); break;
      case 'hin':   bumpRowH(+ROW_H_STEP); break;
      case 'hout':  bumpRowH(-ROW_H_STEP); break;
    }
  });
});
(() => {
  const cb = $('combinedOnly');
  if (!cb) return;
  cb.checked = combinedOnly;
  cb.addEventListener('change', () => {
    combinedOnly = cb.checked;
    localStorage.setItem('combinedOnly', combinedOnly ? '1' : '0');
    // '1' or '0' both count as explicit opt-in/out for future loads.
    rebuildRows();
    fitCanvas(); draw();
  });
})();
window.addEventListener('resize', () => { draw(); });
window.addEventListener('keydown', (e) => {
  const t = e.target;
  if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
  if (e.key === '+' || e.key === '=') { zoomAt(1.4, (xMin+xMax)/2); e.preventDefault(); }
  else if (e.key === '-' || e.key === '_') { zoomAt(1/1.4, (xMin+xMax)/2); e.preventDefault(); }
});

// ── echo strip state ───────────────────────────────────────────────────
const echoes = [];  // [{id, scale, source:[s,e]|null, start, color, include, source_id}]
let nextEchoId = 1;
function makeEcho(opts) {
  opts = opts || {};
  const i = echoes.length;
  return {
    id: nextEchoId++,
    scale: opts.scale || '1',
    source: null,
    start: opts.start || '0',
    pitch: opts.pitch || '',
    color: PALETTE[i % PALETTE.length],
    include: true,
    source_id: opts.source_id != null ? opts.source_id
             : (inputs[0] ? inputs[0].id : null),
  };
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
// ── pitch builder (intuitive UI for the pitch DSL) ───────────────────
// Maps a small set of "kind" + params to the DSL string stored in e.pitch.
// Single op per echo via the builder — chaining ('t+5;i@C4') stays
// expressible via the DSL but isn't reachable from the buttons. Add later
// if anyone asks.
const PITCH_LETTERS = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];
const PITCH_SCALES  = ['major','minor','dorian','phrygian','lydian',
                       'mixolydian','locrian','harmonic_minor',
                       'melodic_minor','whole_tone','pentatonic_major',
                       'chromatic'];
function parsePitchOp(s) {
  s = (s || '').trim();
  if (s === '' || s === '_') return { kind: 'none' };
  let m;
  if ((m = /^t([+-]?\\d+)$/.exec(s))) {
    return { kind: 'transpose', semis: parseInt(m[1], 10) };
  }
  if ((m = /^i@([A-G][#b]?)(-?\\d+)$/.exec(s))) {
    return { kind: 'invert', letter: m[1], octave: parseInt(m[2], 10) };
  }
  if ((m = /^id@([A-G][#b]?)(-?\\d+)\\/([A-G][#b]?)-([a-z_]+)$/.exec(s))) {
    return {
      kind: 'invert_diatonic',
      letter: m[1], octave: parseInt(m[2], 10),
      scale_root: m[3], scale_name: m[4],
    };
  }
  // Unrecognised (e.g. chained) — leave it custom and let the user know.
  return { kind: 'custom', raw: s };
}
function synthPitchOp(p) {
  switch (p.kind) {
    case 'none':     return '';
    case 'transpose':
      return 't' + (p.semis >= 0 ? '+' : '') + p.semis;
    case 'invert':   return 'i@' + p.letter + p.octave;
    case 'invert_diatonic':
      return 'id@' + p.letter + p.octave + '/' + p.scale_root + '-' + p.scale_name;
    case 'custom':   return p.raw;
  }
  return '';
}
function pitchBuilderHTML(p) {
  const kindSel = (cur) => {
    const opts = [
      ['none','none'],
      ['transpose','transpose'],
      ['invert','invert (chromatic)'],
    ];
    return '<select data-b="kind" style="background:#222;color:#eee;border:1px solid #444;padding:2px 4px;font:inherit">'
      + opts.map(([v,l]) => '<option value="'+v+'"'+(v===cur?' selected':'')+'>'+l+'</option>').join('')
      + '</select>';
  };
  const noteSel = (cur, key='letter') => '<select data-b="'+key+'" style="background:#222;color:#eee;border:1px solid #444;padding:2px 4px;font:inherit">'
      + PITCH_LETTERS.map(l => '<option'+(l===cur?' selected':'')+'>'+l+'</option>').join('')
      + '</select>';
  const octSel = (cur, key='octave') => '<select data-b="'+key+'" style="background:#222;color:#eee;border:1px solid #444;padding:2px 4px;font:inherit">'
      + [0,1,2,3,4,5,6,7,8].map(o => '<option'+(o===cur?' selected':'')+'>'+o+'</option>').join('')
      + '</select>';
  const scaleSel = (cur) => '<select data-b="scale_name" style="background:#222;color:#eee;border:1px solid #444;padding:2px 4px;font:inherit">'
      + PITCH_SCALES.map(n => '<option'+(n===cur?' selected':'')+'>'+n+'</option>').join('')
      + '</select>';
  let body = '';
  if (p.kind === 'transpose') {
    body = '<input data-b="semis" type="number" value="'+p.semis+'" style="width:54px;background:#222;color:#eee;border:1px solid #444;padding:2px 4px;font:inherit"> semis';
  } else if (p.kind === 'invert') {
    body = 'axis ' + noteSel(p.letter) + octSel(p.octave);
  } else if (p.kind === 'invert_diatonic') {
    body = 'axis ' + noteSel(p.letter) + octSel(p.octave) + ' in '
         + noteSel(p.scale_root, 'scale_root') + scaleSel(p.scale_name);
  } else if (p.kind === 'custom') {
    body = '<input data-b="raw" type="text" value="'+(p.raw||'').replace(/"/g,'&quot;')+'" style="width:140px;background:#222;color:#eee;border:1px solid #444;padding:2px 4px;font:inherit" title="raw DSL — chains and edge cases">';
  }
  return kindSel(p.kind) + ' ' + body;
}
function wirePitchBuilder(echo, container) {
  function render() {
    const p = parsePitchOp(echo.pitch);
    container.innerHTML = pitchBuilderHTML(p);
    container.querySelectorAll('[data-b]').forEach(el => {
      const evt = (el.tagName === 'SELECT') ? 'change' : 'input';
      el.addEventListener(evt, () => {
        const key = el.dataset.b;
        const cur = parsePitchOp(echo.pitch);
        if (key === 'kind') {
          // Re-seed defaults when switching op kind.
          const k = el.value;
          let np = { kind: k };
          if (k === 'transpose')       np.semis = 0;
          else if (k === 'invert')     { np.letter = 'C'; np.octave = 4; }
          else if (k === 'invert_diatonic') {
            np.letter = 'E'; np.octave = 4;
            np.scale_root = 'C'; np.scale_name = 'major';
          } else if (k === 'custom')   np.raw = cur.raw || '';
          echo.pitch = synthPitchOp(np);
          render();
        } else {
          const np = Object.assign({}, cur);
          if (key === 'semis' || key === 'octave') np[key] = parseInt(el.value, 10) || 0;
          else np[key] = el.value;
          echo.pitch = synthPitchOp(np);
        }
        schedulePreview();
      });
    });
  }
  render();
}

function renderEchoes() {
  echoesEl.innerHTML = '';
  echoes.forEach((e, i) => {
    const div = document.createElement('div');
    div.className = 'echo';
    div.dataset.idx = i;
    div.style.borderLeftColor = e.color;
    const sourceOpts = ['<option value="">all inputs (merged)</option>']
      .concat(inputs.map(inp => '<option value="'+inp.id+'"'+
        (String(e.source_id||'')===String(inp.id)?' selected':'')+'>'+
        inp.name.replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))+'</option>'))
      .join('');
    div.innerHTML = `
      <label class="inline" style="margin:0"><input type="checkbox" data-f="include"${e.include!==false?' checked':''}> on</label>
      <span class="swatch" style="background:${e.color}"></span>
      <span class="name">echo ${i+1}</span>
      <label class="field">from
        <select data-f="source_id" style="padding:3px 6px;background:#222;color:#eee;border:1px solid #444;border-radius:3px;font:inherit;max-width:140px">${sourceOpts}</select>
      </label>
      <label class="field">scale<input data-f="scale" type="text" value="${e.scale}"></label>
      <div class="field" data-f="pitchBuilder">pitch
        <div class="pitchBuilder" style="display:flex;gap:4px;align-items:center"></div>
      </div>
      <div class="field">range
        <div><input data-f="source" type="text" value="${e.source ? e.source[0].toFixed(2)+'..'+e.source[1].toFixed(2) : ''}" placeholder="all"><button class="mode" data-mode="source">pick</button></div>
      </div>
      <div class="field">start
        <div><input data-f="start" type="text" value="${e.start}" placeholder="2b"><button class="mode" data-mode="start">pick</button></div>
      </div>
      <button class="rm">×</button>`;
    echoesEl.appendChild(div);
    const pbHost = div.querySelector('[data-f="pitchBuilder"] .pitchBuilder');
    if (pbHost) wirePitchBuilder(e, pbHost);
    div.querySelectorAll('input[data-f]').forEach(inp => {
      const evt = (inp.type === 'checkbox') ? 'change' : 'input';
      inp.addEventListener(evt, () => {
        const f = inp.dataset.f;
        if (f === 'source') {
          const v = inp.value.trim();
          if (v.includes('..')) {
            const [a, b] = v.split('..').map(parseFloat);
            if (isFinite(a) && isFinite(b) && b > a) e.source = [a, b];
            else e.source = null;
          } else e.source = null;
        } else if (f === 'include') {
          e.include = inp.checked;
        } else {
          e[f] = inp.value;
        }
        draw();
        schedulePreview();
      });
    });
    div.querySelectorAll('select[data-f]').forEach(sel => {
      sel.addEventListener('change', () => {
        e.source_id = sel.value ? +sel.value : null;
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
// Each row carries a stable `pid` (player id). voiceSettings is keyed by pid
// so per-voice crop persists across row insertions/removals — keying by array
// index meant removing an upstream row would shift state onto its neighbour
// (e.g. a stale crop silently muting an echo).
function rebuildRows() {
  rows = [];
  if (combinedOnly) {
    // Combined view skips per-voice rows entirely — only the overlay row,
    // which pulls notes from `echoes`/`processedVoices` directly.
    const hasEchoes = echoes.some((e, k) =>
      e.include !== false && processedVoices && processedVoices[k+1]);
    if (hasEchoes) {
      rows.push({ label: 'combined', overlay: true, pid: 'combined' });
    }
  } else {
    for (const inp of inputNotes) {
      rows.push({
        label: (inp.name || ('input '+inp.id)) + ' (source)',
        notes: inp.notes || [],
        color: '#9aa',
        kind: 'input',
        inputId: inp.id,
        pid: 'src:'+inp.id,
      });
    }
    for (let i = 0; i < echoes.length; i++) {
      const v = processedVoices && processedVoices[i+1];
      rows.push({
        label: 'echo '+(i+1) + (echoes[i].include === false ? ' (off)' : ''),
        notes: v ? v.notes : [],
        color: echoes[i].include === false ? '#444' : echoes[i].color,
        kind: 'echo',
        echoIdx: i,
        pid: 'echo:'+echoes[i].id,
      });
    }
    if (rows.length) rows.push({ label: 'combined', overlay: true, pid: 'combined' });
  }
  // Drop voiceSettings whose pid is no longer present so removed rows don't
  // leave behind state that can re-attach to a future row.
  const live = new Set(rows.map(r => r.pid));
  for (const k of [...voiceSettings.keys()]) {
    if (!live.has(k)) voiceSettings.delete(k);
  }
  if (typeof renderPlayVoices === 'function') renderPlayVoices();
  draw();
}

let previewTimer = null;
function scaleLooksComplete(s) {
  s = String(s || '').trim();
  if (!s) return false;
  // Reject trailing operators / dangling slashes like "3/" while typing.
  if (/[/*+\\-^.]$/.test(s)) return false;
  return true;
}
function schedulePreview() {
  if (!inputs.length || !echoes.length) { draw(); return; }
  if (!echoes.every(e => scaleLooksComplete(e.scale))) { draw(); return; }
  clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, 350);
}
async function runPreview() {
  if (!inputs.length || !echoes.length) return;
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
    dlBar.style.display = 'block';
  } catch (e) {
    if (mine === previewJobId) setStatus('preview error: '+e.message, true);
  }
}
function buildFormData() {
  const fd = new FormData();
  inputs.forEach((inp, i) => fd.append('mid_'+i, inp.file, inp.name));
  fd.append('inputs', JSON.stringify(inputs.map(inp => ({
    id: inp.id, name: inp.name, offset: Number(inp.offset) || 0,
    kind: inp.kind,
  }))));
  fd.append('tsig', $('tsig').value);
  fd.append('output_crop', $('outCrop').value);
  fd.append('echoes', JSON.stringify(echoes.map(e => ({
    scale: e.scale,
    source: e.source ? (e.source[0]+'..'+e.source[1]) : '',
    start: e.start || '0',
    source_id: e.source_id == null ? null : Number(e.source_id),
    include: e.include !== false,
    pitch: e.pitch || '',
  }))));
  return fd;
}
function setMsg(m, err) { setStatus(m, !!err); }
$('tsig').addEventListener('input', schedulePreview);
$('outCrop').addEventListener('input', schedulePreview);




function addInput(file, opts) {
  opts = opts || {};
  const kind = opts.kind || 'file';
  const name = opts.name || file.name || ('recording '+nextInputId+'.mid');
  inputs.push({ id: nextInputId++, file, name, offset: 0, kind });
}
function addInputs(files, opts) {
  for (const f of files) addInput(f, opts);
  renderInputs();
  refreshPreview();
}
function removeInput(id) {
  const i = inputs.findIndex(x => x.id === id);
  if (i < 0) return;
  inputs.splice(i, 1);
  renderInputs();
  if (inputs.length === 0) {
    originalNotes=[]; processedVoices=null; rebuildRows();
    prEl.classList.add('empty'); dlBar.style.display='none';
    setStatus('');
  } else {
    refreshPreview();
  }
}
function renderInputs() {
  // Echo "from" dropdowns enumerate inputs — refresh them when the list changes.
  if (typeof renderEchoes === 'function' && echoes.length) renderEchoes();
  const el = $('inputList');
  el.innerHTML = '';
  picked.textContent = inputs.length
    ? inputs.length+' input'+(inputs.length>1?'s':'')+' loaded'
    : '';
  inputs.forEach((inp) => {
    const div = document.createElement('div');
    div.className = 'echo';
    div.innerHTML =
      '<span class="name" style="min-width:0">'+(inp.kind==='rec'?'🎹 ':'📄 ')+
        inp.name.replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))+'</span>'+
      '<label class="field">offset (beats)<div><input data-off="'+inp.id+
        '" type="number" step="0.5" value="'+inp.offset+
        '" style="width:80px"></div></label>'+
      '<button class="mode" data-copy="'+inp.id+'" '+
        'title="Add a polytime with ratio 1 from this input (= a copy)">+ as copy</button>'+
      '<button class="rm" data-rm="'+inp.id+'">×</button>';
    el.appendChild(div);
  });
  el.querySelectorAll('input[data-off]').forEach(inp => {
    inp.addEventListener('input', () => {
      const t = inputs.find(x => x.id === +inp.dataset.off);
      if (t) { t.offset = parseFloat(inp.value) || 0; refreshPreview(); }
    });
  });
  el.querySelectorAll('button[data-copy]').forEach(b => {
    b.addEventListener('click', () => {
      if (echoes.length >= 8) { setStatus('max 8 polytimes', true); return; }
      echoes.push(makeEcho({ source_id: +b.dataset.copy, scale: '1' }));
      renderEchoes();
      rebuildRows();
      schedulePreview();
    });
  });
  el.querySelectorAll('button[data-rm]').forEach(b => {
    b.addEventListener('click', () => removeInput(+b.dataset.rm));
  });
}
async function refreshPreview() {
  if (!inputs.length) return;
  const mine = ++jobId;
  originalNotes=[]; processedVoices=null;
  rebuildRows(); prEl.classList.add('empty');
  dlBar.style.display='none'; dlUrl=null; dlName=null;
  setStatus('loading preview...');
  const fd = new FormData();
  inputs.forEach((inp, i) => fd.append('mid_'+i, inp.file, inp.name));
  fd.append('inputs', JSON.stringify(inputs.map(inp => ({
    id: inp.id, name: inp.name, offset: Number(inp.offset) || 0,
    kind: inp.kind,
  }))));
  try{
    const r=await fetch('/preview',{method:'POST',body:fd});
    const j=await r.json();
    if(mine !== jobId) return;
    if(!r.ok) throw new Error(j.error||'preview failed');
    inputNotes = j.inputs || [];
    // Flat union of included inputs — used by snap-to-note and by the player
    // as a fallback when nothing else identifies the source.
    originalNotes = [];
    for (const inp of inputNotes) {
      if (inp.include === false) continue;
      for (const n of inp.notes) originalNotes.push(n);
    }
    detectedBpm = j.bpm || 120;
    totalBeats = j.total_beats || 16;
    pitchLo = j.pitch_lo ?? 60;
    pitchHi = j.pitch_hi ?? 72;
    beatsPerBar = j.beats_per_bar || 4;
    if (!echoes.length) { echoes.push(makeEcho()); renderEchoes(); }
    rebuildRows();
    fitView();
    prEl.classList.remove('empty');
    $('tsig').placeholder='auto — detected '+j.detected_ts;
    const totalNotes = inputNotes.reduce((a, b) => a + (b.notes ? b.notes.length : 0), 0);
    setStatus(inputs.length+' input(s) · '+j.detected_ts+' · '+totalNotes+' source notes');
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
file.addEventListener('change',e=>{
  addInputs(e.target.files);
  file.value = '';
});
drop.addEventListener('dragenter',e=>{e.preventDefault();drop.classList.add('hover');});
drop.addEventListener('dragover',e=>{e.preventDefault();drop.classList.add('hover');});
drop.addEventListener('dragleave',e=>{drop.classList.remove('hover');});
drop.addEventListener('drop',e=>{
  e.preventDefault();
  drop.classList.remove('hover');
  const fs = e.dataTransfer && e.dataTransfer.files;
  if (fs && fs.length) addInputs(fs);
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
      recStop=$('recStop'), recStatus=$('recStatus'), recBpm=$('recBpm'),
      recMonitor=$('recMonitor');
let midiAccess=null, recording=false, recStart=0, recEvents=[], openNotes={},
    recTimer=null, activeInputs=[];

// Permission isn't asked until the user actually wants to record (or hits
// Play with a MIDI output selected). Until then we just show the panel.
if (navigator.requestMIDIAccess) {
  recBox.style.display='block';
  recDevice.textContent='click ● Record to scan for devices';
  recBtn.disabled=false;  // first click triggers the prompt
} else {
  $('recNotSupported').style.display='block';
}
let midiRequestInFlight=null;
function ensureMidiAccess() {
  if (midiAccess) return Promise.resolve(midiAccess);
  if (!navigator.requestMIDIAccess) return Promise.reject(new Error('no Web MIDI'));
  if (midiRequestInFlight) return midiRequestInFlight;
  midiRequestInFlight = navigator.requestMIDIAccess().then(setupMidi, (err) => {
    recDevice.textContent='permission denied';
    midiRequestInFlight = null;
    throw err;
  });
  return midiRequestInFlight;
}

function setupMidi(access) {
  midiAccess=access;
  refreshInputs();
  refreshOutputs();
  access.onstatechange=()=>{ refreshInputs(); refreshOutputs(); };
  return access;
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
  const [status, data1, data2] = e.data;
  const cmd = status & 0xf0;
  const isOn = cmd === 0x90 && data2 > 0;
  const isOff = cmd === 0x80 || (cmd === 0x90 && data2 === 0);
  // Monitor: echo every key to the currently selected playback output so you
  // hear yourself, whether or not a recording is in progress. Routing follows
  // the Play panel's output picker — internal synth, or any external MIDI
  // device (a GM synth on Windows, an IAC-bus soundfont app on macOS, etc.).
  if (recMonitor.checked) {
    if (isOn) monitorNoteOn(data1, data2);
    else if (isOff) monitorNoteOff(data1);
  }
  if (!recording) return;
  const t = performance.now() - recStart;
  if (isOn) {
    openNotes[data1] = t;
  } else if (isOff) {
    const onT = openNotes[data1];
    if (onT === undefined) return;
    delete openNotes[data1];
    recEvents.push({midi: data1, onMs: onT, offMs: t});
  }
}
// Monitoring routes through the Play panel's output picker, so it works the
// same on any OS — internal synth, or whatever external MIDI device is chosen.
const monitorHeld=new Set();
function monitorNoteOn(midi, vel) {
  if (usingInternal()) { internalNoteOn(midi, vel); }
  else { const out=selectedOutput(); if (!out) return; out.send([0x90, midi, vel]); }
  monitorHeld.add(midi);
}
function monitorNoteOff(midi) {
  if (usingInternal()) { internalNoteOff(midi); }
  else { const out=selectedOutput(); if (out) out.send([0x80, midi, 0]); }
  monitorHeld.delete(midi);
}
function monitorAllOff() {
  for (const m of [...monitorHeld]) monitorNoteOff(m);
}
// Unchecking mid-note (or switching output) would strand held voices.
recMonitor.addEventListener('change', () => {
  if (!recMonitor.checked) monitorAllOff();
});
recBtn.addEventListener('click', async () => {
  if (!midiAccess) {
    recStatus.textContent='requesting MIDI permission…';
    try { await ensureMidiAccess(); }
    catch { recStatus.textContent='MIDI permission denied'; return; }
    if (!activeInputs.length) {
      recStatus.textContent='no MIDI input — plug a keyboard, then click Record again';
      return;
    }
  }
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
  const name = 'recording '+nextInputId+'.mid';
  const f = new File([blob], name, {type: 'audio/midi'});
  recStatus.textContent=`captured ${recEvents.length} notes`;
  addInputs([f], {kind:'rec', name});
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

// ── MIDI playback (Web MIDI API) ────────────────────────────────────────
// Plays whatever's currently in the piano roll (original + every echo) to a
// chosen MIDI output port. setTimeout-based scheduler keeps pause/stop
// responsive; on stop/pause we send note-offs for every held note plus an
// all-notes-off CC so a hanging key can never strand a synth.
const playOut=$('playOut'), playBtn=$('playBtn'), pauseBtn=$('pauseBtn'),
      stopBtn=$('stopBtn'), playStatus=$('playStatus'),
      playSpeedEl=$('playSpeed'), playVolEl=$('playVol'),
      playSpeedVal=$('playSpeedVal'), playVolVal=$('playVolVal'),
      playLoopEl=$('playLoop');
let midiOuts=[], playState='stopped', playPosBeats=0, playTimers=[];
let playStartWall=0, playBpmAtStart=120;
const heldNotes=new Set();
const INTERNAL_ID='__internal__';
let audioCtx=null;
const liveVoices=new Map();  // midi → {osc, gain} for the internal synth
const voiceSettings=new Map();  // row.pid → {crop:"a..b"}
let playheadBeat=null, playheadRaf=null;
const playVoices=$('playVoices');

function parseCrop(s) {
  s = String(s || '').trim();
  if (!s) return null;
  // Accept "0..4", "0-4", "0,4", "0 4", "0 to 4" — anything with two numbers.
  const nums = s.match(/-?\\d+(\\.\\d+)?/g);
  if (!nums || nums.length < 2) return null;
  const a = parseFloat(nums[0]), b = parseFloat(nums[1]);
  if (!isFinite(a) || !isFinite(b) || b <= a) return null;
  return [a, b];
}
let lastVoicesSig = '';
function renderPlayVoices() {
  // Rebuilding wipes inputs (focus, mid-edit text). Only re-render when the
  // row structure actually changes — typing a scale shouldn't reset crops.
  // Source rows aren't played (collectPlayEvents skips them), so they don't
  // appear here; an echo's on/off lives in the echo strip — one source of
  // truth, no redundant mute control.
  const playable = rows.filter(r => r.overlay || r.kind === 'echo');
  const sig = playable.map(r => r.pid+'|'+r.label).join('::');
  if (sig === lastVoicesSig) return;
  lastVoicesSig = sig;
  playVoices.innerHTML = '';
  playable.forEach(row => {
    if (!voiceSettings.has(row.pid)) voiceSettings.set(row.pid, { crop:'' });
    const s = voiceSettings.get(row.pid);
    const div = document.createElement('div');
    div.style.cssText = 'display:flex;gap:8px;align-items:center';
    div.innerHTML =
      '<span style="display:inline-block;width:12px;height:12px;background:'+
        (row.color||'#888')+';border-radius:2px;opacity:'+(row.overlay?0.4:1)+'"></span>'+
      '<span style="min-width:80px;color:'+(row.overlay?'#888':'#ccc')+'">'+
        row.label + (row.overlay?' (global)':'') + '</span>'+
      '<input type="text" data-crop="'+row.pid+'" value="'+s.crop+
        '" placeholder="full · or e.g. 0..8 (beats)" '+
        'style="width:170px;padding:3px 6px;background:#222;color:#eee;'+
        'border:1px solid #444;border-radius:3px;font:inherit">';
    playVoices.appendChild(div);
  });
  playVoices.querySelectorAll('input[data-crop]').forEach(el => {
    el.addEventListener('input', () => {
      const s = voiceSettings.get(el.dataset.crop);
      if (s) { s.crop = el.value; draw(); }
    });
  });
}
function combinedCropRange() {
  const s = voiceSettings.get('combined');
  return s ? parseCrop(s.crop) : null;
}

playSpeedEl.addEventListener('input', () => {
  playSpeedVal.textContent = (+playSpeedEl.value).toFixed(2)+'×';
  if (playState==='playing') {
    const wasPaused = true;
    pausePlayback();
    startPlaybackFrom(playPosBeats);
  }
});
playVolEl.addEventListener('input', () => {
  playVolVal.textContent = playVolEl.value;
});
// Switching the output while monitoring would strand held voices on the old
// device — flush them. (monitorAllOff is defined earlier; playOut now exists.)
playOut.addEventListener('change', monitorAllOff);

function buildOutputList() {
  const prev = playOut.value;
  playOut.innerHTML = '';
  const intOpt = document.createElement('option');
  intOpt.value = INTERNAL_ID;
  intOpt.textContent = '🔊 Internal synth (this tab)';
  playOut.appendChild(intOpt);
  for (const o of midiOuts) {
    const opt=document.createElement('option');
    opt.value=o.id; opt.textContent=o.name;
    playOut.appendChild(opt);
  }
  if (prev && (prev === INTERNAL_ID || midiOuts.some(o=>o.id===prev))) {
    playOut.value = prev;
  }
  playBtn.disabled = false;
}
function refreshOutputs() {
  midiOuts = midiAccess ? [...midiAccess.outputs.values()] : [];
  buildOutputList();
  if (playState === 'stopped' || playState === 'paused') playBtn.disabled = false;
}
function selectedOutput() {
  return midiOuts.find(o => o.id === playOut.value) || null;
}
function usingInternal() { return playOut.value === INTERNAL_ID; }
function ensureAudio() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === 'suspended') audioCtx.resume();
  return audioCtx;
}
function midiToHz(m) { return 440 * Math.pow(2, (m - 69) / 12); }
function internalNoteOn(midi, vel) {
  const ctx = ensureAudio();
  const t = ctx.currentTime;
  // One voice per pitch — retrigger if already on.
  internalNoteOff(midi, true);
  const osc = ctx.createOscillator();
  osc.type = 'triangle';
  osc.frequency.value = midiToHz(midi);
  const gain = ctx.createGain();
  const peak = (vel / 127) * 0.18;  // keep polyphony from clipping
  gain.gain.setValueAtTime(0, t);
  gain.gain.linearRampToValueAtTime(peak, t + 0.005);
  gain.gain.exponentialRampToValueAtTime(peak * 0.6, t + 0.25);
  osc.connect(gain).connect(ctx.destination);
  osc.start(t);
  liveVoices.set(midi, { osc, gain });
}
function internalNoteOff(midi, immediate=false) {
  const v = liveVoices.get(midi);
  if (!v) return;
  liveVoices.delete(midi);
  const ctx = audioCtx;
  if (!ctx) { try{v.osc.stop();}catch(e){} return; }
  const t = ctx.currentTime;
  const rel = immediate ? 0.005 : 0.08;
  try {
    v.gain.gain.cancelScheduledValues(t);
    v.gain.gain.setValueAtTime(Math.max(0.0001, v.gain.gain.value), t);
    v.gain.gain.exponentialRampToValueAtTime(0.0001, t + rel);
    v.osc.stop(t + rel + 0.02);
  } catch (e) {}
}
function internalAllOff() {
  for (const m of [...liveVoices.keys()]) internalNoteOff(m, true);
}
function collectPlayEvents() {
  const evs = [];
  const global = combinedCropRange();
  // Walk echoes/processedVoices directly rather than `rows`, so combined-only
  // mode (which has no per-echo rows) still produces playback events. Each
  // echo's per-voice crop lives in voiceSettings under 'echo:'+echo.id —
  // stable across rebuildRows().
  for (let k = 0; k < echoes.length; k++) {
    const ek = echoes[k];
    if (ek.include === false) continue;
    const pv = processedVoices && processedVoices[k+1];
    if (!pv || !pv.notes) continue;
    const s = voiceSettings.get('echo:' + ek.id);
    const local = s ? parseCrop(s.crop) : null;
    for (const n of pv.notes) {
      let on = n.on, off = n.off;
      for (const r of [local, global]) {
        if (!r) continue;
        if (off <= r[0] || on >= r[1]) { on = off = null; break; }
        on = Math.max(on, r[0]); off = Math.min(off, r[1]);
      }
      if (on == null || off <= on) continue;
      evs.push({t:on, kind:'on', midi:n.midi});
      evs.push({t:off, kind:'off', midi:n.midi});
    }
  }
  evs.sort((a,b)=> a.t-b.t || (a.kind==='off'?-1:1));
  return evs;
}
function silenceAll() {
  if (usingInternal()) { internalAllOff(); heldNotes.clear(); return; }
  const out = selectedOutput();
  if (!out) { heldNotes.clear(); return; }
  for (const m of heldNotes) out.send([0x80, m, 0]);
  out.send([0xb0, 123, 0]);  // all-notes-off (channel 0)
  heldNotes.clear();
}
function clearTimers() {
  for (const id of playTimers) clearTimeout(id);
  playTimers = [];
}
function tickPlayhead() {
  if (playState !== 'playing') {
    playheadBeat = null; playheadRaf = null; draw(); return;
  }
  const elapsed = (performance.now() - playStartWall) / 1000 * (playBpmAtStart / 60);
  playheadBeat = playPosBeats + elapsed;
  draw();
  playheadRaf = requestAnimationFrame(tickPlayhead);
}
function stopPlayhead() {
  if (playheadRaf) cancelAnimationFrame(playheadRaf);
  playheadRaf = null;
  playheadBeat = null;
  draw();
}
function startPlaybackFrom(beatStart) {
  const internal = usingInternal();
  const out = internal ? null : selectedOutput();
  if (!internal && !out) { setStatus('select a MIDI output', true); return; }
  if (internal) ensureAudio();
  const evs = collectPlayEvents();
  if (!evs.length) { setStatus('nothing to play — drop a MIDI first', true); return; }
  const speed = +playSpeedEl.value;
  const bpm = detectedBpm * speed;
  const secPerBeat = 60 / bpm;
  playStartWall = performance.now();
  playBpmAtStart = bpm;
  playPosBeats = beatStart;
  playState = 'playing';
  playBtn.disabled = true; pauseBtn.disabled = false; stopBtn.disabled = false;
  let scheduledEnd = 0;
  for (const e of evs) {
    if (e.t < beatStart) continue;
    const delay = (e.t - beatStart) * secPerBeat * 1000;
    if (delay > scheduledEnd) scheduledEnd = delay;
    const id = setTimeout(() => {
      if (playState !== 'playing') return;
      const vel = Math.max(1, +playVolEl.value);
      if (e.kind === 'on') {
        if (internal) internalNoteOn(e.midi, vel);
        else out.send([0x90, e.midi, vel]);
        heldNotes.add(e.midi);
      } else {
        if (internal) internalNoteOff(e.midi);
        else out.send([0x80, e.midi, 0]);
        heldNotes.delete(e.midi);
      }
    }, delay);
    playTimers.push(id);
  }
  const endId = setTimeout(() => {
    if (playState !== 'playing') return;
    silenceAll();
    if (playLoopEl.checked) {
      clearTimers();
      startPlaybackFrom(0);
    } else {
      stopPlayback();
    }
  }, scheduledEnd + 80);
  playTimers.push(endId);
  const cropSummary = [];
  const labelByPid = new Map(rows.map(r => [r.pid, r.label]));
  voiceSettings.forEach((s, pid) => {
    const r = parseCrop(s.crop);
    if (r) cropSummary.push((labelByPid.get(pid)||'?')+':'+r[0]+'..'+r[1]);
  });
  playStatus.textContent = `playing · ${bpm.toFixed(0)} bpm · vel ${+playVolEl.value}`
    + (cropSummary.length ? ' · '+cropSummary.join(', ') : '');
  if (!playheadRaf) playheadRaf = requestAnimationFrame(tickPlayhead);
}
function pausePlayback() {
  if (playState !== 'playing') return;
  const elapsedBeats = (performance.now() - playStartWall) / 1000 *
                       (playBpmAtStart / 60);
  playPosBeats += elapsedBeats;
  clearTimers();
  silenceAll();
  playState = 'paused';
  stopPlayhead();
  playBtn.disabled = false; pauseBtn.disabled = true; stopBtn.disabled = false;
  playStatus.textContent = `paused at beat ${playPosBeats.toFixed(2)}`;
}
function stopPlayback() {
  clearTimers();
  silenceAll();
  playState = 'stopped';
  playPosBeats = 0;
  stopPlayhead();
  playBtn.disabled = false; pauseBtn.disabled = true; stopBtn.disabled = true;
  playStatus.textContent = 'stopped';
}
playBtn.addEventListener('click', async () => {
  if (!usingInternal() && !midiAccess) {
    try { await ensureMidiAccess(); }
    catch { setStatus('MIDI permission denied — pick "Internal synth"', true); return; }
  }
  if (playState === 'paused') startPlaybackFrom(playPosBeats);
  else { clearTimers(); silenceAll(); startPlaybackFrom(0); }
});
pauseBtn.addEventListener('click', pausePlayback);
stopBtn.addEventListener('click', stopPlayback);
playOut.addEventListener('change', () => {
  if (playState === 'playing') {
    pausePlayback();
    startPlaybackFrom(playPosBeats);
  }
});
window.addEventListener('beforeunload', () => { try { silenceAll(); } catch(e) {} });

// Playback panel is always available (internal synth needs only Web Audio).
$('playBox').style.display='block';
refreshOutputs();
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


def _merge_inputs(items: list[dict], *, respect_include: bool = True) -> Path:
    """Merge multiple input MIDIs into a single Type-1 file, applying each
    item's `offset` (beats). When `respect_include` is True (the default),
    items with `include=False` are dropped before merging.

    The first input's ticks_per_beat is used as the target resolution; later
    inputs' ticks are rescaled into it. Tempo / time-signature meta from the
    first input are kept at tick 0; notes are merged into a single track.
    """
    import mido
    if not items:
        raise ValueError("no inputs to merge")
    used = [it for it in items if (not respect_include or it.get("include", True))]
    if not used:
        # All excluded — emit an empty-but-valid MIDI so downstream doesn't crash.
        used = [items[0]]
    base = mido.MidiFile(str(used[0]["path"]))
    ppq = base.ticks_per_beat
    out_mf = mido.MidiFile(ticks_per_beat=ppq, type=1)
    track = mido.MidiTrack()
    out_mf.tracks.append(track)

    header_meta: list["mido.Message"] = []
    for msg in base.tracks[0] if base.tracks else []:
        if msg.is_meta and msg.type in (
            "set_tempo", "time_signature", "key_signature", "track_name",
        ):
            header_meta.append(msg.copy(time=0))

    events: list[tuple[int, "mido.Message"]] = []  # (abs_tick, msg)
    for it in used:
        path, offset_beats = it["path"], it["offset"]
        mf = mido.MidiFile(str(path))
        scale = ppq / mf.ticks_per_beat
        off_ticks = int(round(offset_beats * ppq))
        for tr in mf.tracks:
            absolute = 0
            for msg in tr:
                absolute += msg.time
                if msg.type in ("note_on", "note_off"):
                    events.append((
                        int(round(absolute * scale)) + max(0, off_ticks),
                        msg,
                    ))
    # note_off before note_on at the same tick (zero-gap legato)
    def _ord(e):
        t, m = e
        if m.type == "note_off" or (m.type == "note_on" and m.velocity == 0):
            return (t, 0)
        return (t, 1)
    events.sort(key=_ord)

    prev = 0
    for m in header_meta:
        track.append(m)
    if not any(m.type == "set_tempo" for m in header_meta):
        track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    for tick, msg in events:
        track.append(msg.copy(time=tick - prev))
        prev = tick
    track.append(mido.MetaMessage("end_of_track", time=0))

    fd, name = tempfile.mkstemp(suffix=".mid")
    os.close(fd)
    out_mf.save(name)
    return Path(name)


def _collect_inputs(fields: dict, meta_key: str = "inputs") -> list[dict]:
    """Pull every mid_N file out of `fields`, attach the `inputs` JSON
    metadata (offset, include, id, name), and write each to a temp file.
    Returns a list of dicts with keys {path, offset, include, id, name}.
    Caller owns cleanup of `path`."""
    raw = (fields.get(meta_key) or b"[]").decode().strip()
    try:
        meta = json.loads(raw)
    except json.JSONDecodeError:
        meta = []
    items: list[dict] = []
    i = 0
    while True:
        f = fields.get(f"mid_{i}")
        if not isinstance(f, tuple):
            break
        _, content = f
        with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
            tmp.write(content)
            p = Path(tmp.name)
        m = meta[i] if i < len(meta) and isinstance(meta[i], dict) else {}
        try: off = float(m.get("offset", 0) or 0)
        except (TypeError, ValueError): off = 0.0
        items.append({
            "path": p,
            "offset": off,
            "include": bool(m.get("include", True)),
            "id": m.get("id", i + 1),
            "name": m.get("name", p.name),
        })
        i += 1
    if not items:
        single = fields.get("mid")
        if isinstance(single, tuple):
            _, content = single
            with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
                tmp.write(content)
                items.append({
                    "path": Path(tmp.name), "offset": 0.0,
                    "include": True, "id": 1, "name": "input",
                })
    if not items:
        raise ValueError("missing MIDI file")
    return items


def _input_beat_length(path: Path) -> float:
    """Return the time, in beats, from tick 0 to the last note_off / event end."""
    import mido
    mf = mido.MidiFile(str(path))
    ppq = mf.ticks_per_beat
    max_tick = 0
    for tr in mf.tracks:
        absolute = 0
        for msg in tr:
            absolute += msg.time
            if msg.type in ("note_on", "note_off"):
                if absolute > max_tick:
                    max_tick = absolute
    return max_tick / ppq if ppq else 0.0


def _parse_output_crop(s: str) -> tuple[float, float] | None:
    """Parse "0..16" / "0-16" / "0,16" → (start, end) beats. None if invalid."""
    import re
    nums = re.findall(r"-?\d+(?:\.\d+)?", s or "")
    if len(nums) < 2:
        return None
    a, b = float(nums[0]), float(nums[1])
    if b <= a:
        return None
    return (a, b)


def _trim_midi(path: Path, start_beats: float, end_beats: float) -> None:
    """Clip every track in `path` to the beat range [start, end] in place.
    Notes straddling either edge are split; meta events that fall before the
    window (tempo, time-sig, track name) are anchored at tick 0 so the trimmed
    file still has a sane header."""
    import mido
    mf = mido.MidiFile(str(path))
    ppq = mf.ticks_per_beat
    cs = int(round(start_beats * ppq))
    ce = int(round(end_beats * ppq))
    new_mf = mido.MidiFile(ticks_per_beat=ppq, type=mf.type)

    for track in mf.tracks:
        # Resolve to absolute time, pair note_on/off so we can clip cleanly.
        absolute = 0
        held: dict[tuple[int, int], tuple[int, int]] = {}  # (ch, note) → (on_tick, vel)
        notes: list[tuple[int, int, int, int, int]] = []   # (on, off, ch, note, vel)
        meta: list[tuple[int, "mido.Message"]] = []
        for msg in track:
            absolute += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                held[(msg.channel, msg.note)] = (absolute, msg.velocity)
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                key = (msg.channel, msg.note)
                if key in held:
                    on_t, vel = held.pop(key)
                    notes.append((on_t, absolute, msg.channel, msg.note, vel))
            elif msg.type == "end_of_track":
                pass  # we'll re-emit our own
            else:
                meta.append((absolute, msg))

        out: list[tuple[int, "mido.Message"]] = []
        for tick, msg in meta:
            if tick < cs:
                # Keep header-defining meta (tempo, time-sig, key, name) anchored at 0.
                if msg.is_meta and msg.type in (
                    "set_tempo", "time_signature", "key_signature", "track_name",
                    "instrument_name",
                ):
                    out.append((0, msg))
            elif tick < ce:
                out.append((tick - cs, msg))
        for on, off, ch, note, vel in notes:
            if off <= cs or on >= ce:
                continue
            new_on = max(on, cs) - cs
            new_off = min(off, ce) - cs
            if new_off <= new_on:
                continue
            out.append((new_on, mido.Message("note_on", note=note, velocity=vel, channel=ch)))
            out.append((new_off, mido.Message("note_off", note=note, velocity=0, channel=ch)))

        # Stable order: meta < note_off < note_on at the same tick.
        def _ord(item):
            t, m = item
            if m.is_meta: return (t, 0)
            if m.type == "note_off" or (m.type == "note_on" and m.velocity == 0):
                return (t, 1)
            if m.type == "note_on": return (t, 2)
            return (t, 3)
        out.sort(key=_ord)

        new_track = mido.MidiTrack()
        prev = 0
        for tick, msg in out:
            new_track.append(msg.copy(time=tick - prev))
            prev = tick
        new_track.append(mido.MetaMessage("end_of_track", time=0))
        new_mf.tracks.append(new_track)

    new_mf.save(str(path))


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
        items = _collect_inputs(fields)
        try:
            from score_io.live.midi_file import load_mido
            from polytime import _flatten_score
            from model.events import Note, Chord
            per_input: list[dict] = []
            all_pitches: list[int] = []
            max_end = 0.0
            global_bpm: float | None = None
            global_ts: TimeSignature | None = None
            detected_any = False

            for it in items:
                detected = detect_time_signature(it["path"])
                detected_bpm = detect_bpm(it["path"])
                if detected:
                    detected_any = True
                ts_in = detected or TimeSignature(4, 4)
                if global_ts is None:
                    global_ts = ts_in
                if global_bpm is None and detected_bpm:
                    global_bpm = float(detected_bpm)

                score = load_mido(str(it["path"]), time_signature=ts_in)
                theme = _flatten_score(score)
                notes_out = []
                off_base = float(it["offset"])
                for e in theme.events:
                    on = float(e.offset) + off_base
                    off = on + float(e.duration.actual_beats)
                    if isinstance(e, Chord):
                        for p in e.pitches:
                            notes_out.append({"midi": p.midi, "on": on, "off": off})
                    elif isinstance(e, Note):
                        notes_out.append({"midi": e.pitch.midi, "on": on, "off": off})
                for n in notes_out:
                    all_pitches.append(n["midi"])
                    if n["off"] > max_end:
                        max_end = n["off"]
                per_input.append({
                    "id": int(it["id"]),
                    "name": it["name"],
                    "offset": off_base,
                    "include": bool(it["include"]),
                    "kind": it.get("kind", "file"),
                    "notes": notes_out,
                })

            ts_final = global_ts or TimeSignature(4, 4)
            if not all_pitches:
                all_pitches = [60, 72]
        finally:
            for it in items:
                try: it["path"].unlink()
                except OSError: pass

        payload = {
            "inputs": per_input,
            "total_beats": max(max_end, 4.0),
            "pitch_lo": min(all_pitches),
            "pitch_hi": max(all_pitches),
            "beats_per_bar": float(ts_final.beats_per_measure),
            "bpm": global_bpm or 120.0,
            "detected_ts": f"{ts_final.numerator}/{ts_final.denominator}" +
                           ("" if detected_any else " (default)"),
        }
        self._send(200, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _handle_process(self):
        fields = self._read_fields()
        items = _collect_inputs(fields)
        filename = items[0]["name"] if items else "input"
        tsig_str = (fields.get("tsig") or b"").decode().strip()
        # Inputs are sources only in the new model — nothing from them goes
        # directly into the output. Every output voice is a polytime (a
        # ratio-1 polytime is just a copy of its source).
        combine = False
        out_crop_str = (fields.get("output_crop") or b"").decode().strip()
        echoes_raw = (fields.get("echoes") or b"[]").decode().strip()
        try:
            echoes_list = json.loads(echoes_raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"bad echoes JSON: {e}")
        if not isinstance(echoes_list, list) or not echoes_list:
            raise ValueError("add at least one polytime (or '+ as copy' an input)")
        # Drop excluded echoes BEFORE the 8-voice check — that gate matters
        # for what actually renders, not for what's in the UI.
        echoes_list = [e for e in echoes_list if e.get("include", True)]
        if not echoes_list:
            raise ValueError("all echoes are switched off")
        if len(echoes_list) > 8:
            raise ValueError("max 8 echo voices")

        base_bpm = 120.0
        stem = Path(filename or "input").stem

        # BPM: first input that actually carries a tempo meta wins (mirrors
        # _handle_meta, so the preview-reported BPM and the rendered BPM
        # agree — otherwise an untempo'd first input made the render
        # silently fall back to 120 while the preview used a later file's
        # tempo). TS still comes from the first input as the reference.
        for it in items:
            bpm_here = detect_bpm(it["path"])
            if bpm_here:
                base_bpm = float(bpm_here)
                break
        first_path = items[0]["path"]
        ts_override = parse_ts(tsig_str)
        detected = detect_time_signature(first_path)
        ts = ts_override or detected or TimeSignature(4, 4)
        ts_label = f"{ts.numerator}/{ts.denominator}"
        if ts_override: ts_label += " (override)"
        elif not detected: ts_label += " (default)"

        out_mid = Path(tempfile.mkstemp(suffix=".mid")[1])
        tmp_paths: list[Path] = []

        try:
            cap = ts.beats_per_measure
            inputs_by_id = {int(it["id"]): it for it in items}
            default_sid = int(items[0]["id"])

            # Group polytimes by source input. Each group runs through polytime()
            # against ONLY its source file, so an echo that picks source=A truly
            # derives from A and never sees B's notes — no more merge bleed.
            groups: dict[int, list[tuple[int, dict]]] = {}
            for idx, e in enumerate(echoes_list):
                sid = e.get("source_id")
                try:
                    sid = int(sid) if sid is not None else default_sid
                except (TypeError, ValueError):
                    sid = default_sid
                if sid not in inputs_by_id:
                    sid = default_sid
                groups.setdefault(sid, []).append((idx, e))

            echo_notes_by_idx: dict[int, list[dict]] = {}
            for sid, group in groups.items():
                inp = inputs_by_id[sid]
                src_offset = Fraction(inp["offset"]).limit_denominator(96)
                g_scales = tuple(
                    parse_scale(str(e.get("scale", "")).strip(), base_bpm)
                    for _, e in group
                )
                g_ats = tuple(
                    _parse_when(str(e.get("start", "")).strip() or "0", cap)
                    for _, e in group
                )
                # Picked ranges are in DISPLAY (output-timeline) beats, so they
                # include `src_offset`. Convert to source-local before polytime.
                g_ranges: list[tuple[Fraction, Fraction] | None] = []
                for _, e in group:
                    explicit = parse_range(
                        str(e.get("source", "")).strip() or "", cap
                    )
                    if explicit is None:
                        g_ranges.append(None)
                    else:
                        lo = explicit[0] - src_offset
                        hi = explicit[1] - src_offset
                        if hi <= lo or hi <= 0:
                            g_ranges.append((Fraction(0), Fraction(0)))
                        else:
                            g_ranges.append((max(Fraction(0), lo), hi))

                g_pitches = tuple(
                    str(e.get("pitch", "") or "").strip() for _, e in group
                )

                # Skip per-polytime ranges that landed on nothing — let the rest
                # still render rather than failing the whole request.
                runnable: list[tuple[int, Fraction, Fraction, tuple[Fraction, Fraction] | None, str]] = []
                for (idx, _), s, a, rng, pop in zip(group, g_scales, g_ats, g_ranges, g_pitches):
                    if rng is not None and rng[1] <= rng[0]:
                        echo_notes_by_idx[idx] = []
                        continue
                    runnable.append((idx, s, a, rng, pop))
                if not runnable:
                    continue

                tmp_out = Path(tempfile.mkstemp(suffix=".mid")[1])
                tmp_paths.append(tmp_out)
                try:
                    polytime(
                        inp["path"], at=runnable[0][2],
                        scales=tuple(r[1] for r in runnable),
                        ats=tuple(r[2] for r in runnable),
                        out=tmp_out, time_signature=ts,
                        combine=False,
                        theme_ranges=tuple(r[3] for r in runnable),
                        pitch_ops=tuple(r[4] for r in runnable),
                    )
                except ValueError as exc:
                    # e.g. "theme range … contains no notes" or a bad pitch
                    # op — surface the message via status, record empties for
                    # affected echoes so the rest still render.
                    for idx, *_ in runnable:
                        echo_notes_by_idx[idx] = []
                    continue
                voices, _, _, _ = _voices_from_midi(tmp_out)
                for r, v in zip(runnable, voices):
                    echo_notes_by_idx[r[0]] = v["notes"]

            # Build the final MIDI from scratch — one track per polytime, in the
            # client-side order. No theme track (sources are not in output).
            import mido
            PPQ = 480
            final_mf = mido.MidiFile(ticks_per_beat=PPQ, type=1)
            meta = mido.MidiTrack()
            meta.append(mido.MetaMessage(
                "set_tempo", tempo=int(round(60_000_000 / base_bpm)), time=0,
            ))
            meta.append(mido.MetaMessage(
                "time_signature",
                numerator=ts.numerator, denominator=ts.denominator,
                clocks_per_click=24, notated_32nd_notes_per_beat=8, time=0,
            ))
            meta.append(mido.MetaMessage("end_of_track", time=0))
            final_mf.tracks.append(meta)

            for idx in range(len(echoes_list)):
                notes = echo_notes_by_idx.get(idx, [])
                if not notes:
                    continue
                tr = mido.MidiTrack()
                tr.append(mido.MetaMessage(
                    "track_name", name=f"polytime {idx+1}", time=0,
                ))
                evs: list[tuple[int, "mido.Message"]] = []
                for n in notes:
                    on_tick = max(0, int(round(n["on"] * PPQ)))
                    off_tick = max(on_tick + 1, int(round(n["off"] * PPQ)))
                    evs.append((on_tick, mido.Message(
                        "note_on", note=n["midi"], velocity=80,
                    )))
                    evs.append((off_tick, mido.Message(
                        "note_off", note=n["midi"], velocity=0,
                    )))
                def _ord(e):
                    t, m = e
                    if m.type == "note_off" or (m.type == "note_on" and m.velocity == 0):
                        return (t, 0)
                    return (t, 1)
                evs.sort(key=_ord)
                prev = 0
                for tick, msg in evs:
                    tr.append(msg.copy(time=tick - prev))
                    prev = tick
                tr.append(mido.MetaMessage("end_of_track", time=0))
                final_mf.tracks.append(tr)

            final_mf.save(str(out_mid))
            mid_path = out_mid
            out_crop = _parse_output_crop(out_crop_str)
            if out_crop:
                _trim_midi(mid_path, out_crop[0], out_crop[1])
            mid_data = mid_path.read_bytes()
            voices_payload, total_beats, pitch_lo, pitch_hi = _voices_from_midi(
                mid_path
            )
        finally:
            for it in items:
                try: it["path"].unlink()
                except OSError: pass
            for p in (*tmp_paths, out_mid):
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
