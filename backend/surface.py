"""
Surface: the boundary between "a live application" and "the agent loop".

This is the seam the write-up talks about extending to legacy web / desktop later —
today it's backed by a Playwright Page, but the loop and planner only ever talk to
`perceive()` / `act()`, never to Playwright directly. Locators are semantic (label,
role+name, placeholder, visible text) on purpose: CSS/xpath selectors are exactly
the thing that doesn't exist on the legacy surfaces this system is ultimately for.

This is a skeleton: perception is a reasonably robust best-effort DOM read (via a
single page.evaluate), not a full accessibility-tree implementation. No app-specific
logic lives here — it must work against whatever URL the user pastes.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from playwright.async_api import Locator as PWLocator
from playwright.async_api import Page, TimeoutError as PWTimeoutError

from .schema import Action, Locator, Perception

DIGEST_MAX_CHARS = 1500
DEFAULT_TIMEOUT_MS = 8000

# Single page.evaluate() call that inventories visible, interactive elements and
# derives a best-effort accessible name/label for each — deliberately generic,
# no assumptions about any particular site's markup.
_PERCEIVE_JS = r"""
() => {
  function isVisible(el) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    return true;
  }

  // Returns {text, strategy} — `strategy` tells the caller EXACTLY which
  // Locator.strategy will resolve this element, since perceive() already knows
  // (it just tried them in priority order). This removes the guesswork that
  // used to make the planner try strategy="label" against a value that was
  // actually just the HTML `name` attribute (legacy forms very often have no
  // real label/aria-label/placeholder at all — only a name attribute).
  function labelFor(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return { text: aria.trim(), strategy: 'label' };

    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const parts = labelledBy.split(/\s+/)
        .map(id => document.getElementById(id))
        .filter(Boolean)
        .map(n => n.innerText || n.textContent || '');
      const joined = parts.join(' ').trim();
      if (joined) return { text: joined, strategy: 'label' };
    }

    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab && lab.innerText) return { text: lab.innerText.trim(), strategy: 'label' };
    }

    const parentLabel = el.closest('label');
    if (parentLabel && parentLabel.innerText) {
      return { text: parentLabel.innerText.trim(), strategy: 'label' };
    }

    const placeholder = el.getAttribute('placeholder');
    if (placeholder) return { text: placeholder.trim(), strategy: 'placeholder' };

    const name = el.getAttribute('name');
    if (name) return { text: name.trim(), strategy: 'name' };

    return { text: '', strategy: 'label' };
  }

  function controlName(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    const text = (el.innerText || el.value || '').trim();
    if (text) return text.replace(/\s+/g, ' ').slice(0, 80);
    return '';
  }

  const fields = [];
  document.querySelectorAll('input, textarea, select').forEach(el => {
    if (!isVisible(el)) return;
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image'].includes(type)) return;
    if (el.disabled) return;
    const found = labelFor(el);
    if (!found.text) return;
    fields.push({
      label: found.text,
      locator_strategy: found.strategy,
      kind: el.tagName.toLowerCase() === 'select' ? 'select'
            : el.tagName.toLowerCase() === 'textarea' ? 'textarea'
            : type,
      current_value: el.value || null,
    });
  });

  const buttons = [];
  document.querySelectorAll('button, [role="button"], input[type="submit"], input[type="button"]')
    .forEach(el => {
      if (!isVisible(el) || el.disabled) return;
      const name = controlName(el);
      if (name) buttons.push({ name, role: 'button' });
    });

  const links = [];
  document.querySelectorAll('a[href]').forEach(el => {
    if (!isVisible(el)) return;
    const name = controlName(el);
    if (name) links.push({ name, role: 'link' });
  });

  const digest = (document.body.innerText || '').replace(/\s+/g, ' ').trim();

  return {
    url: window.location.href,
    title: document.title || '',
    fields: fields.slice(0, 40),
    buttons: buttons.slice(0, 40),
    links: links.slice(0, 40),
    digest,
  };
}
"""


class ActionResult:
    def __init__(self, ok: bool, extracted_text: Optional[str] = None, error: Optional[str] = None):
        self.ok = ok
        self.extracted_text = extracted_text
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "extracted_text": self.extracted_text, "error": self.error}


class Surface:
    """Wraps a live Playwright page. Only perceive()/act() are used by the loop."""

    def __init__(self, page: Page):
        self._page = page

    async def perceive(self) -> Perception:
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
        except PWTimeoutError:
            pass  # slow/streaming pages are fine to perceive as-is
        raw = await self._page.evaluate(_PERCEIVE_JS)
        raw["digest"] = raw.get("digest", "")[:DIGEST_MAX_CHARS]
        return Perception.model_validate(raw)

    async def act(self, action: Action) -> ActionResult:
        try:
            if action.type == "navigate":
                if not action.value:
                    return ActionResult(False, error="navigate action missing value (URL)")
                await self._page.goto(action.value, timeout=DEFAULT_TIMEOUT_MS * 2)
                return ActionResult(True)

            if action.type == "click":
                loc = await self._resolve(action.locator)
                if loc is None:
                    return ActionResult(False, error=f"could not resolve locator {action.locator}")
                await loc.click(timeout=DEFAULT_TIMEOUT_MS)
                return ActionResult(True)

            if action.type == "type":
                loc = await self._resolve(action.locator)
                if loc is None:
                    return ActionResult(False, error=f"could not resolve locator {action.locator}")
                await loc.fill(action.value or "", timeout=DEFAULT_TIMEOUT_MS)
                return ActionResult(True)

            if action.type == "select":
                loc = await self._resolve(action.locator)
                if loc is None:
                    return ActionResult(False, error=f"could not resolve locator {action.locator}")
                await loc.select_option(label=action.value, timeout=DEFAULT_TIMEOUT_MS)
                return ActionResult(True)

            if action.type == "press_enter":
                loc = await self._resolve(action.locator)
                if loc is None:
                    return ActionResult(False, error=f"could not resolve locator {action.locator}")
                await loc.press("Enter", timeout=DEFAULT_TIMEOUT_MS)
                return ActionResult(True)

            if action.type == "extract":
                loc = await self._resolve(action.locator)
                if loc is None:
                    return ActionResult(False, error=f"could not resolve locator {action.locator}")
                text = await loc.inner_text(timeout=DEFAULT_TIMEOUT_MS)
                return ActionResult(True, extracted_text=text.strip())

            return ActionResult(False, error=f"unknown action type: {action.type}")

        except PWTimeoutError as exc:
            return ActionResult(False, error=f"timeout: {exc}")
        except Exception as exc:  # noqa: BLE001 — surface every failure to the caller
            return ActionResult(False, error=f"{type(exc).__name__}: {exc}")

    async def _resolve(self, locator: Optional[Locator]) -> Optional[PWLocator]:
        """Resolve a semantic Locator to a Playwright locator, walking fallbacks
        until one actually matches a visible element."""
        if locator is None:
            return None
        for candidate in [locator, *locator.fallbacks]:
            pw_loc = self._build(candidate)
            if pw_loc is None:
                continue
            try:
                count = await pw_loc.count()
            except Exception:  # noqa: BLE001
                continue
            if count >= 1:
                return pw_loc.first
        return None

    def _build(self, locator: Locator) -> Optional[PWLocator]:
        if locator.strategy == "label":
            return self._page.get_by_label(locator.value)
        if locator.strategy == "role":
            role = locator.role or "button"
            return self._page.get_by_role(role, name=locator.value)  # type: ignore[arg-type]
        if locator.strategy == "placeholder":
            return self._page.get_by_placeholder(locator.value)
        if locator.strategy == "text":
            return self._page.get_by_text(locator.value, exact=False)
        if locator.strategy == "name":
            # Attribute-based match on the HTML `name` attribute — the stable
            # hook perceive() falls back to reporting when a field has no real
            # label/aria-label/placeholder at all (common on legacy
            # server-rendered forms). Deliberately not a positional CSS
            # selector: `name` is a developer-assigned identifier, closer in
            # spirit to a semantic locator than to fragile structural CSS/xpath.
            escaped = locator.value.replace('"', '\\"')
            return self._page.locator(f'[name="{escaped}"]')
        return None