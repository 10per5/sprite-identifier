"""Shared offline-page assets + rendering helpers.

Every generated page (label review, approve/reject, catalogue viewer) is a
self-contained offline HTML file meant to be opened directly via ``file://`` —
there is no server, so the common CSS and JS are *inlined* into each page from
these two constants at render time. Edit the styling/helpers once here and all
pages pick them up next time they are regenerated.

``render_template`` also owns the small jobs the pages used to do by hand
(relative asset prefixing, loading ``template/*.j2``), so the CLI command
modules stay thin.
"""
import json
from pathlib import Path

from jinja2 import Template

from lib.core.quality import GENERIC_WORDS
from lib.util.paths import TEMPLATE_DIR

# ---------------------------------------------------------------------------
# Shared design system (inlined as the <style> of every generated page)
# ---------------------------------------------------------------------------
BASE_CSS = """
:root{color-scheme:dark;background:#111318;color:#e7e9ee;font:14px system-ui,sans-serif}
body{margin:0}
header{position:sticky;top:0;background:#1c1f26;padding:12px 18px;border-bottom:1px solid #333;z-index:10;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
h1{margin:0;font-size:16px}
button,input,select,textarea{font:inherit;background:#262a33;border:1px solid #444;color:#e7e9ee;border-radius:6px;padding:8px 12px}
button{cursor:pointer}
button:hover{background:#303540}
button.primary{background:#3d5af1}
button.primary:hover{background:#4c67f5}
button.danger{background:#a33}
button.danger:hover{background:#c44}
.status{color:#9aa1ad;font-size:13px;margin-left:auto}
.summary{margin-top:10px;color:#9aa1ad;font-size:13px}
.hidden{display:none}
.checker{background-color:#333;background-image:linear-gradient(45deg,#555 25%,transparent 25%),linear-gradient(-45deg,#555 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#555 75%),linear-gradient(-45deg,transparent 75%,#555 75%);background-size:12px 12px;background-position:0 0,0 6px,6px -6px,-6px 0}
.taglist{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.tag{background:#262a33;border:1px solid #444;color:#a5b1c4;border-radius:4px;padding:4px 8px;font-size:12px}
.keyhint{font-size:11px;color:#8a919c;margin-top:4px}
.field{background:#1c1f26;border:1px solid #333;border-radius:6px;padding:10px}
.field label{font-size:11px;color:#8a919c;display:block;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.05em}
.field input,.field textarea{width:100%;background:#111318;border:1px solid #333;color:#e7e9ee;border-radius:4px;padding:8px}
.field input:focus,.field textarea:focus{outline:none;border-color:#3d5af1}
.field.modified{border-color:#f5a623;box-shadow:0 0 0 4px rgba(245,166,35,0.22)}
.field.modified label{color:#f5a623}
"""


# ---------------------------------------------------------------------------
# Shared JS helpers (inlined as a <script> in every generated page)
# ---------------------------------------------------------------------------
BASE_JS = """
'use strict';
// ---- shared helpers (sprite pipeline offline pages) ----
function assetPath(prefix, p){ return prefix + p.split('/').map(encodeURIComponent).join('/'); }
function loadJSON(key){ try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch(e){ return null; } }
function saveJSON(key, value){ try { localStorage.setItem(key, JSON.stringify(value)); } catch(e){} }
function downloadFile(name, obj){
  const blob = new Blob([JSON.stringify(obj, null, 2) + '\\n'], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name; a.click(); URL.revokeObjectURL(a.href);
}
// ---- browser-cache reconciliation ----
// The browser cache holds a person's real work, so a regenerated page never
// overwrites it. Per record: the cache wins unless the freshly generated data
// carries a newer human `edited_at`; those are reported as conflicts (cache
// kept) so the person can choose to take them. A cached record with no stamp
// (older export) keeps its values except fields that are blank or placeholder
// junk while the fresh data has something real.
function isJunk(field, v){
  if (Array.isArray(v)) return v.length === 0;
  const s = String(v == null ? '' : v).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  if (!s) return true;
  if (field !== 'name') return false;
  if (/^(unnamed|unknown|sprite|icon|image|img)?_?[0-9]+$/.test(s)) return true;
  const words = s.split('_').map(w => w.replace(/[0-9]+$/, '')).filter(Boolean);
  return words.every(w => GENERIC_WORDS.includes(w));
}
function mergeCache(fresh, cached, fields){
  const merged = {}, conflicts = {};
  for (const id of new Set([...Object.keys(fresh), ...Object.keys(cached)])) {
    const f = fresh[id], c = cached[id];
    if (c === undefined) { merged[id] = f; continue; }
    if (f === undefined || typeof c !== 'object' || typeof f !== 'object' || !c || !f) { merged[id] = c; continue; }
    // A cached card nobody touched holds no work; the fresh data replaces it.
    if (!(c.edited_at || c.edited || c.decision)) { merged[id] = f; continue; }
    const cts = c.edited_at || '', fts = f.edited_at || '';
    if (fts && fts > cts) { merged[id] = c; conflicts[id] = f; continue; }
    if (cts) { merged[id] = c; continue; }
    const m = { ...f, ...c };
    for (const k of fields) if (isJunk(k, c[k]) && !isJunk(k, f[k])) m[k] = f[k];
    merged[id] = m;
  }
  return { merged, conflicts };
}
// Offer to replace cached records with the newer generated ones. The cache is
// copied to `<key>:backup` first, so taking them is undoable by hand.
function offerConflicts(conflicts, store, key, save, rerender){
  const ids = Object.keys(conflicts);
  if (!ids.length) return;
  const bar = document.createElement('div');
  bar.style.cssText = 'background:#3a2f12;color:#f5d58a;padding:8px 14px;font-size:13px;display:flex;gap:12px;align-items:center';
  const msg = document.createElement('span');
  msg.textContent = ids.length + ' card(s) have a newer stored edit than your browser cache. Your cached values are kept.';
  const take = document.createElement('button');
  take.textContent = 'Take stored edits';
  take.onclick = () => {
    try { localStorage.setItem(key + ':backup', localStorage.getItem(key) || ''); } catch(e){}
    for (const id of ids) store[id] = conflicts[id];
    save(); bar.remove(); rerender();
  };
  const keep = document.createElement('button');
  keep.textContent = 'Keep mine';
  keep.onclick = () => bar.remove();
  bar.append(msg, take, keep);
  document.body.prepend(bar);
}
function fuzzyScore(term, opt){
  const t = term.toLowerCase(), o = opt.toLowerCase();
  let ti = 0, score = 0, consec = 0, marks = [];
  for (let ci = 0; ci < o.length; ci++) {
    if (o[ci] === t[ti]) {
      score += 10 + consec * 2; consec++; marks.push(ci); ti++;
      if (ti === t.length) break;
    } else { consec = 0; }
  }
  if (ti < t.length) return null;
  return { score, marks };
}
function fuzzyFilter(term, options){
  if (!term) return options.map(o => { return { opt: o, score: 0, marks: [] } });
  const res = options.map(o => { const s = fuzzyScore(term, o); return s ? { opt: o, score: s.score, marks: s.marks } : null; }).filter(Boolean);
  res.sort((a, b) => b.score - a.score || a.opt.localeCompare(b.opt));
  return res;
}
function markup(opt, marks){
  let out = '';
  const mset = new Set(marks);
  for (let ci = 0; ci < opt.length; ci++) {
    if (mset.has(ci)) out += '<em>' + opt[ci] + '</em>';
    else out += opt[ci];
  }
  return out;
}
"""


def render_template(name, **context):
    """Render ``template/<name>`` with the shared CSS/JS injected.

    Pages only have to supply their page-specific <style>/<script> content;
    ``shared_css`` and ``shared_js`` are available in the template context.
    """
    template = Template((TEMPLATE_DIR / name).read_text(encoding="utf-8"))
    context.setdefault("shared_css", BASE_CSS)
    context.setdefault(
        "shared_js",
        BASE_JS + f"const GENERIC_WORDS = {json.dumps(sorted(GENERIC_WORDS))};\n",
    )
    return template.render(**context)


def relative_prefix(output_path):
    """``"../"`` repeated so page assets resolve from *output_path* to CWD."""
    try:
        rel = output_path.parent.relative_to(Path.cwd())
    except ValueError:
        rel = Path(".")
    depth = len([p for p in rel.parts if p not in ("", ".")])
    return "../" * depth


def dump_json(data):
    return json.dumps(data, ensure_ascii=False)
