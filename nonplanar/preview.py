"""Self-contained HTML 3D toolpath viewer (open the file in any browser)."""
import base64
import json

import numpy as np

def _rgb(h):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


# Orca's line-type colours (same as the app preview)
COLORS = {k: _rgb(v) for k, v in {
    "outer_wall": "#ff7d38", "inner_wall": "#ffe64d", "overhang_wall": "#3b5bff", "top_surface": "#f04040",
    "bottom_surface": "#66a3e6", "solid": "#9654cc", "bridge": "#4d80ba", "infill": "#b03029",
    "ironing": "#ff8c69", "skirt": "#00876e", "brim": "#00c2a4", "support": "#2ecc71",
    "support_interface": "#1f8f4a"}.items()}


def _b64(a):
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def write_preview(preview, out_path, title="Non-planar preview", info=""):
    pos, col, idx, layer_end = [], [], [], []
    n = 0
    for lay in preview:
        for kind, pts in lay:
            m = len(pts)
            pos.append(pts.astype(np.float32))
            col.append(np.tile(np.array(COLORS.get(kind, (1, 1, 1)), np.float32), (m, 1)))
            a = np.arange(n, n + m - 1, dtype=np.uint32)
            idx.append(np.column_stack([a, a + 1]).ravel())
            n += m
        layer_end.append(int(sum(len(i) for i in idx)))
    pos = np.vstack(pos)
    data = {
        "pos": _b64(pos), "col": _b64(np.vstack(col)), "idx": _b64(np.concatenate(idx)),
        "layers": layer_end,
        "center": pos.mean(0).tolist(), "size": float(np.ptp(pos, axis=0).max()),
    }
    html = TEMPLATE.replace("__DATA__", json.dumps(data)).replace("__TITLE__", title).replace("__INFO__", info)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#15171c;--panel:#20232b;--text:#e8e8ea;--muted:#9aa0aa}
body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,sans-serif;overflow:hidden}
#ui{position:fixed;left:12px;right:12px;bottom:12px;background:var(--panel);padding:10px 14px;border-radius:10px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
#ui input[type=range]{flex:1;min-width:160px}
.k{display:inline-flex;align-items:center;gap:5px;color:var(--muted)} .k i{width:10px;height:10px;border-radius:2px;display:inline-block}
#info{position:fixed;left:12px;top:10px;color:var(--muted);font-size:12px;white-space:pre}
</style>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
</head><body>
<div id="info">__INFO__</div>
<div id="ui">
  <span>Layer <b id="ln"></b></span>
  <input id="sl" type="range" min="1" step="1">
  <label class="k"><input id="only" type="checkbox"> only this layer</label>
  <span class="k"><i style="background:#ff8c26"></i>outer</span>
  <span class="k"><i style="background:#ffd94d"></i>inner</span>
  <span class="k"><i style="background:#bf59f2"></i>solid</span>
  <span class="k"><i style="background:#4dbfff"></i>infill</span>
</div>
<script type="module">
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
const D = __DATA__;
const dec = (s, T) => { const b = Uint8Array.from(atob(s), c => c.charCodeAt(0)); return new T(b.buffer); };
const g = new THREE.BufferGeometry();
g.setAttribute('position', new THREE.BufferAttribute(dec(D.pos, Float32Array), 3));
g.setAttribute('color', new THREE.BufferAttribute(dec(D.col, Float32Array), 3));
g.setIndex(new THREE.BufferAttribute(dec(D.idx, Uint32Array), 1));
const lines = new THREE.LineSegments(g, new THREE.LineBasicMaterial({vertexColors: true}));
const scene = new THREE.Scene(); scene.background = new THREE.Color(0x15171c);
scene.add(lines);
const bed = new THREE.GridHelper(256, 32, 0x444a55, 0x2a2e36); bed.rotation.x = Math.PI/2; bed.position.set(128,128,0); scene.add(bed);
const cam = new THREE.PerspectiveCamera(45, innerWidth/innerHeight, 0.1, 5000); cam.up.set(0,0,1);
const c = D.center; cam.position.set(c[0] + D.size*1.3, c[1] - D.size*1.6, c[2] + D.size*1.1);
const r = new THREE.WebGLRenderer({antialias: true}); r.setPixelRatio(devicePixelRatio); r.setSize(innerWidth, innerHeight);
document.body.appendChild(r.domElement);
const ctl = new OrbitControls(cam, r.domElement); ctl.target.set(c[0], c[1], c[2]*0.8); ctl.update();
const sl = document.getElementById('sl'), ln = document.getElementById('ln'), only = document.getElementById('only');
sl.max = D.layers.length; sl.value = D.layers.length;
function upd(){ const i = +sl.value; const start = only.checked && i > 1 ? D.layers[i-2] : 0;
  g.setDrawRange(start, D.layers[i-1] - start); ln.textContent = i + ' / ' + D.layers.length; }
sl.oninput = upd; only.onchange = upd; upd();
addEventListener('resize', () => { cam.aspect = innerWidth/innerHeight; cam.updateProjectionMatrix(); r.setSize(innerWidth, innerHeight); });
(function loop(){ requestAnimationFrame(loop); r.render(scene, cam); })();
</script></body></html>
"""
