"""Template substitution: {{param}} -> concrete value, used only at execution
time. Artifacts always store the templated form so raw inputs are never frozen
in."""
from __future__ import annotations

import re

_TMPL = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def find_params(text: str | None) -> list[str]:
    if not text:
        return []
    return _TMPL.findall(text)


def substitute(text: str | None, params: dict) -> str | None:
    if text is None:
        return None
    def repl(m):
        name = m.group(1)
        if name not in params:
            raise KeyError(f"missing param {name!r}")
        return str(params[name])
    return _TMPL.sub(repl, text)
