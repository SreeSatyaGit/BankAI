"""
Shared {{param}} substitution logic, used by both loop.py (discovery) and
replay.py (deterministic execution). Kept in one place so the templating
convention — {{name}} in an Action.value, resolved from a flat params dict —
can't drift between the two call sites.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

_PARAM_REF = re.compile(r"\{\{(\w+)\}\}")


def substitute(value: Optional[str], params: Dict[str, str]) -> Optional[str]:
    """Replace every {{name}} in `value` with params[name]. Raises KeyError if a
    referenced param was never supplied — callers should treat this as a hard
    failure to surface loudly, not paper over."""
    if value is None:
        return None

    def _sub(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in params:
            raise KeyError(f"referenced unbound param '{{{{{name}}}}}'")
        return params[name]

    return _PARAM_REF.sub(_sub, value)
