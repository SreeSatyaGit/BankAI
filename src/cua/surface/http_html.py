"""
HTTP + HTML implementation of Surface.

Why this is the *primary* surface and not a fallback: a large share of legacy
bank back-office apps are server-rendered HTML. For those, driving the app over
HTTP and parsing the returned markup is more deterministic and far cheaper than
a real browser -- there is no JS event loop, no flakey waits, no headless
browser to install. It also runs anywhere.

The tradeoff: it cannot handle client-rendered SPAs or native desktop apps.
Those are exactly what PlaywrightSurface / an accessibility-tree surface cover.
Because both implement the same `Surface` contract, the artifact and replay
engine do not change when we swap the surface. See REPORT.md > Heterogeneity.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

from .base import (
    Surface, Perception, FieldInfo, Action, ActionType, ActResult,
    Locator, Strategy, LocatorNotFound,
)


class HttpHtmlSurface(Surface):
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self._resp: requests.Response | None = None
        self._soup: BeautifulSoup | None = None

    # ----- low-level ------------------------------------------------------
    def _load(self, url: str, method: str = "GET", data: dict | None = None):
        full = urljoin(self.base_url + "/", url)
        self._resp = self.session.request(
            method, full, data=data, timeout=self.timeout, allow_redirects=True)
        self._soup = BeautifulSoup(self._resp.text, "lxml")

    def _ensure(self):
        if self._soup is None:
            raise RuntimeError("navigate before acting")

    # ----- perception -----------------------------------------------------
    def perceive(self) -> Perception:
        self._ensure()
        soup, resp = self._soup, self._resp
        fields: list[FieldInfo] = []
        for inp in soup.select("input, select"):
            kind = inp.name if inp.name == "select" else inp.get("type", "text")
            if kind == "submit":
                continue
            fields.append(FieldInfo(
                label=self._label_for(inp), name=inp.get("name"), kind=kind))
        buttons = [b.get("value") or b.get_text(strip=True)
                   for b in soup.select("input[type=submit], button")]
        links = [a.get_text(strip=True) for a in soup.select("a")
                 if a.get_text(strip=True)]
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:400]
        title = soup.title.get_text(strip=True) if soup.title else ""
        return Perception(
            url=resp.url, title=title, status=resp.status_code,
            fields=fields, links=links, buttons=[b for b in buttons if b],
            text_digest=text)

    def _label_for(self, inp) -> str | None:
        # Legacy table forms: the label is the text of the preceding <td>.
        td = inp.find_parent("td")
        if td:
            prev = td.find_previous_sibling("td")
            if prev and prev.get_text(strip=True):
                return prev.get_text(strip=True)
        lab = inp.find_previous("label")
        return lab.get_text(strip=True) if lab else None

    def snapshot(self) -> str:
        return self._resp.text if self._resp is not None else ""

    # ----- resolution -----------------------------------------------------
    def _resolve(self, loc: Locator):
        """Return a bs4 element for a locator, or raise LocatorNotFound."""
        soup = self._soup
        assert soup is not None
        s, v = loc.strategy, loc.value
        if s == Strategy.NAME:
            return soup.select_one(f"[name={v!r}]") or soup.find(attrs={"name": v})
        if s == Strategy.CSS:
            return soup.select_one(v)
        if s == Strategy.LINK:
            for a in soup.select("a"):
                if a.get_text(strip=True) == v:
                    return a
        if s == Strategy.BUTTON:
            for b in soup.select("input[type=submit], button"):
                if (b.get("value") == v) or (b.get_text(strip=True) == v):
                    return b
        if s == Strategy.LABEL:
            for inp in soup.select("input, select"):
                if self._label_for(inp) == v:
                    return inp
        if s == Strategy.ROW_VALUE:
            # find a <td> whose text == v, return the NEXT td's value
            for td in soup.select("td"):
                if td.get_text(strip=True) == v:
                    nxt = td.find_next_sibling("td")
                    if nxt:
                        return nxt
        if s == Strategy.TEXT_CONTAINS:
            for el in soup.find_all(string=re.compile(re.escape(v))):
                return el.parent
        return None

    def _resolve_chain(self, loc: Locator):
        for candidate in loc.chain():
            el = self._resolve(candidate)
            if el is not None:
                return el, candidate.strategy
        raise LocatorNotFound(loc)

    def _form_action_for(self, el):
        form = el.find_parent("form")
        if form is None:
            return None, {}
        action = form.get("action") or self._resp.url
        method = (form.get("method") or "get").upper()
        # gather existing field values (respecting anything we've filled)
        data = {}
        for inp in form.select("input, select"):
            name = inp.get("name")
            if not name:
                continue
            if inp.name == "select":
                opt = inp.find("option", selected=True)
                data[name] = opt.get("value") if opt else ""
            elif inp.get("type") == "submit":
                continue
            else:
                data[name] = inp.get("value", "")
        return (action, method, data)

    # ----- actions --------------------------------------------------------
    def act(self, action: Action) -> ActResult:
        try:
            if action.type == ActionType.NAVIGATE:
                self._load(action.value or "/")
                return ActResult(ok=True)

            if action.type == ActionType.EXTRACT:
                el, by = self._resolve_chain(action.locator)
                val = el.get_text(strip=True)
                return ActResult(ok=True, resolved_by=by, extracted=val)

            if action.type == ActionType.WAIT_FOR:
                # checkpoint primitive: succeed iff locator resolves
                try:
                    _, by = self._resolve_chain(action.locator)
                    return ActResult(ok=True, resolved_by=by)
                except LocatorNotFound:
                    return ActResult(ok=False, error="condition not met")

            if action.type in (ActionType.FILL, ActionType.SELECT):
                el, by = self._resolve_chain(action.locator)
                # Stash the desired value on the element so form submit picks
                # it up. We store pending fills keyed by field name.
                name = el.get("name")
                if not name:
                    return ActResult(ok=False, error="field has no name attr")
                self._pending = getattr(self, "_pending", {})
                self._pending[name] = action.value or ""
                # reflect into soup so subsequent perceive/submit see it
                if el.name == "select":
                    for opt in el.find_all("option"):
                        if opt.get("value") == action.value:
                            opt["selected"] = "selected"
                else:
                    el["value"] = action.value or ""
                return ActResult(ok=True, resolved_by=by)

            if action.type == ActionType.CLICK:
                el, by = self._resolve_chain(action.locator)
                if el.name == "a":
                    self._load(el.get("href"))
                    return ActResult(ok=True, resolved_by=by)
                # submit button -> post its form with collected data
                res = self._form_action_for(el)
                if res == (None, {}):
                    return ActResult(ok=False, error="button not in a form")
                action_url, method, data = res
                data.update(getattr(self, "_pending", {}))
                self._load(action_url, method=method, data=data)
                self._pending = {}
                return ActResult(ok=True, resolved_by=by)

            return ActResult(ok=False, error=f"unknown action {action.type}")
        except LocatorNotFound as e:
            return ActResult(ok=False, error=str(e))
        except requests.RequestException as e:
            return ActResult(ok=False, error=f"transport: {e}")
