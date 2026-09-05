"""
PlaywrightSurface -- the browser / SPA / accessibility-tree seam (DESIGN STUB).

This is intentionally NOT wired into the demo (browser binaries are not
available in the eval sandbox, and the HTTP/HTML surface already exercises the
whole pipeline). It exists to make the surface seam concrete and to show that
the artifact schema and replay engine need ZERO changes to target a browser.

Mapping the same Locator strategies onto Playwright:

    Strategy.LABEL         -> page.get_by_label(value)
    Strategy.BUTTON        -> page.get_by_role("button", name=value)
    Strategy.LINK          -> page.get_by_role("link", name=value)
    Strategy.ROW_VALUE     -> page.get_by_role("row", name=value).get_by_role("cell")
    Strategy.TEXT_CONTAINS -> page.get_by_text(value)
    Strategy.NAME / CSS    -> page.locator(f"[name={value}]") / page.locator(value)

Note every strategy above maps onto ARIA roles / accessible names, which is
also what an OS-level accessibility-tree surface (for native desktop apps)
exposes. That is the reason locators are semantic rather than CSS/pixel based.

For a screenshot+coordinates CUA, `perceive()` would return the same
`Perception` derived from the a11y tree, and `act()` would resolve a locator to
a bounding box and click its centre. The layers above never learn the
difference.
"""
from __future__ import annotations

from .base import Surface, Perception, Action, ActResult


class PlaywrightSurface(Surface):  # pragma: no cover - design stub
    def __init__(self, *_, **__):
        raise NotImplementedError(
            "PlaywrightSurface is a design stub. Install playwright and its "
            "browsers, then implement perceive()/act() per the docstring. The "
            "HTTP/HTML surface is used for the runnable demo.")

    def perceive(self) -> Perception: ...
    def act(self, action: Action) -> ActResult: ...
    def snapshot(self) -> str: ...
