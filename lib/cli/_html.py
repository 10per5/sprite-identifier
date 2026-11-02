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
    context.setdefault("shared_js", BASE_JS)
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
