"""build script for 006-sql-codemirror-runner.

reads _assets/*.{js,css} and emits a single-file sql_codemirror.py with
the entire CodeMirror 5.65.16 bundle embedded as raw-string constants.

run:  python _build.py
output: sql_codemirror.py (next to this file)

This script is only used at build time and is NOT distributed to closed
network — only sql_codemirror.py needs to be reviewed/imported.
"""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).parent
ASSETS = ROOT / "_assets"
OUT = ROOT / "sql_codemirror.py"

# 1) load assets, strip sourceMappingURL comments (each references an
#    external .map file → polluting closed-network audit if left in)
def load(name: str) -> str:
    text = (ASSETS / name).read_text(encoding="utf-8")
    text = re.sub(r"//#\s*sourceMappingURL=[^\n]*\n?", "", text)
    text = re.sub(r"/\*#\s*sourceMappingURL=[^*]*\*/", "", text)
    # raw strings cannot contain `"""` — quick guard
    if '"""' in text:
        raise RuntimeError(f"{name} contains triple-quote — needs escape")
    if text.endswith("\\"):
        text = text + "\n"
    return text

CM_JS         = load("codemirror.min.js")
CM_SQL_JS     = load("sql.min.js")
CM_HINT_JS    = load("show-hint.min.js")
CM_CSS        = load("codemirror.css")
CM_HINT_CSS   = load("show-hint.css")
CM_THEME_CSS  = load("dracula.css")

# 2) read template (the Python wrapper code)
TEMPLATE = (ROOT / "_template.py").read_text(encoding="utf-8")

# 3) substitute placeholders → bundle constants
def emit_raw(name: str, body: str) -> str:
    return f'{name} = r"""{body}"""'

bundle_block = "\n\n".join([
    "# ===== CodeMirror 5.65.16 bundle (MIT, inlined) =====",
    "# Source: https://codemirror.net/5/  (legacy v5 — current; intentionally "
    "not v6 because v6 requires bundler)",
    "# License: see LICENSE next to this file (MIT)",
    emit_raw("_CM_CSS",       CM_CSS),
    emit_raw("_CM_HINT_CSS",  CM_HINT_CSS),
    emit_raw("_CM_THEME_CSS", CM_THEME_CSS),
    emit_raw("_CM_JS",        CM_JS),
    emit_raw("_CM_SQL_JS",    CM_SQL_JS),
    emit_raw("_CM_HINT_JS",   CM_HINT_JS),
])

source = TEMPLATE.replace("# %%BUNDLE%%", bundle_block)

OUT.write_text(source, encoding="utf-8")
print(f"wrote {OUT}  ({len(source):,} bytes)")
