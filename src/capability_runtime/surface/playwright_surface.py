"""Playwright surface: observe accessibility-ish controls, act via locator ladder."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeout

from capability_runtime.schema.capability import LocatorStrategy, Target

OBSERVE_JS = """
() => {
  const sel = 'a, button, input, select, textarea, [role="button"], [role="link"], [role="textbox"]';
  const nodes = Array.from(document.querySelectorAll(sel));
  const visible = nodes.filter((n) => {
    const r = n.getBoundingClientRect();
    const s = window.getComputedStyle(n);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    if (r.width < 1 && r.height < 1 && n.type !== 'hidden') {
      // keep named form fields even if oddly sized
      if (!n.getAttribute('name') && !n.getAttribute('id')) return false;
    }
    if (n.type === 'hidden') return false;
    return true;
  });
  return visible.slice(0, 80).map((n, i) => {
    const tag = n.tagName.toLowerCase();
    const role = n.getAttribute('role')
      || (tag === 'a' ? 'link'
        : tag === 'button' || (tag === 'input' && ['submit','button','reset'].includes(n.type)) ? 'button'
        : tag === 'input' || tag === 'textarea' ? 'textbox'
        : tag === 'select' ? 'combobox'
        : tag);
    const name = (n.getAttribute('aria-label')
      || n.getAttribute('name')
      || n.getAttribute('id')
      || n.getAttribute('placeholder')
      || n.getAttribute('value')
      || (n.innerText || '').trim()
      || '').slice(0, 80);
    return {
      index: i + 1,
      tag,
      role,
      name,
      type: n.getAttribute('type'),
      placeholder: n.getAttribute('placeholder'),
      id: n.getAttribute('id'),
      inputName: n.getAttribute('name'),
      href: n.getAttribute('href'),
      disabled: !!(n.disabled),
      value: n.type === 'password' ? '' : (n.value || '').slice(0, 40),
    };
  });
}
"""


@dataclass
class ObservedControl:
    index: int
    tag: str
    role: str
    name: str
    type: str | None = None
    placeholder: str | None = None
    id: str | None = None
    input_name: str | None = None
    href: str | None = None
    disabled: bool = False
    value: str | None = None

    def locators(self) -> list[LocatorStrategy]:
        out: list[LocatorStrategy] = []
        if self.role and self.name:
            out.append(LocatorStrategy(type="role", value=self.name, role=self.role))
        if self.placeholder:
            out.append(LocatorStrategy(type="placeholder", value=self.placeholder))
        if self.input_name:
            out.append(LocatorStrategy(type="name", value=self.input_name))
        if self.id:
            out.append(LocatorStrategy(type="id", value=self.id))
        if self.name and self.role not in {"textbox"}:
            out.append(LocatorStrategy(type="text", value=self.name))
        return out or [LocatorStrategy(type="text", value=self.name or self.tag)]


@dataclass
class Snapshot:
    url: str
    title: str
    controls: list[ObservedControl] = field(default_factory=list)
    text: str = ""

    def prompt_text(self) -> str:
        lines = [f"URL: {self.url}", f"TITLE: {self.title}", "CONTROLS:"]
        for c in self.controls:
            bits = [f"[{c.index}]", c.role, c.name]
            if c.input_name:
                bits.append(f"name={c.input_name}")
            if c.placeholder:
                bits.append(f"placeholder={c.placeholder}")
            if getattr(c, "value", None):
                bits.append(f"value={c.value}")
            if c.disabled:
                bits.append("disabled")
            lines.append(" ".join(bits))
        body = re.sub(r"\s+", " ", self.text)[:2500]
        lines.append("TEXT: " + body)
        return "\n".join(lines)

    def by_index(self, index: int) -> ObservedControl:
        for c in self.controls:
            if c.index == index:
                return c
        raise KeyError(f"no control with index {index}")


class Surface:
    def __init__(self, page: Page, timeout_ms: int = 15000) -> None:
        self.page = page
        self.timeout_ms = timeout_ms

    async def observe(self) -> Snapshot:
        await self.page.wait_for_load_state("domcontentloaded")
        raw = await self.page.evaluate(OBSERVE_JS)
        controls = [
            ObservedControl(
                index=int(item["index"]),
                tag=item.get("tag") or "",
                role=item.get("role") or "",
                name=item.get("name") or "",
                type=item.get("type"),
                placeholder=item.get("placeholder"),
                id=item.get("id"),
                input_name=item.get("inputName"),
                href=item.get("href"),
                disabled=bool(item.get("disabled")),
                value=item.get("value"),
            )
            for item in raw
        ]
        try:
            text = await self.page.inner_text("body")
        except Exception:
            text = ""
        return Snapshot(url=self.page.url, title=await self.page.title(), controls=controls, text=text)

    async def goto(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        await self._settle()

    async def click(self, target: Target) -> str:
        loc = await self.resolve(target)
        await loc.click(timeout=self.timeout_ms)
        await self._settle()
        return f"clicked {target.description or target.locators[0].value}"

    async def type_text(self, target: Target, value: str) -> str:
        loc = await self.resolve(target)
        await loc.fill(value, timeout=self.timeout_ms)
        await self._settle()
        return f"typed into {target.description or target.locators[0].value}"

    async def select_option(self, target: Target, value: str) -> str:
        """Set a <select> value. Used by find-transactions and similar forms."""
        loc = await self.resolve(target)
        tag = (await loc.evaluate("el => el.tagName")).lower()
        if tag != "select":
            raise LookupError(f"select_option requires a <select>, got {tag}")
        await loc.select_option(value, timeout=self.timeout_ms)
        await self._settle()
        return f"selected {value!r} in {target.description or target.locators[0].value}"

    async def extract_text(self, locators: list[LocatorStrategy]) -> str:
        loc = await self.resolve(Target(locators=locators))
        text = ""
        try:
            text = (await loc.inner_text()).strip()
        except Exception:
            text = ""
        if not text:
            try:
                text = (await loc.text_content() or "").strip()
            except Exception:
                text = ""
        if not text:
            try:
                text = (await loc.input_value()).strip()
            except Exception:
                text = ""
        return text

    async def resolve(self, target: Target) -> Locator:
        errors: list[str] = []
        for strategy in target.locators:
            loc = self._locator_for(strategy)
            try:
                await loc.first.wait_for(state="visible", timeout=min(self.timeout_ms, 5000))
                count = await loc.count()
                if count >= 1:
                    return loc.first
                errors.append(f"{strategy.type}={strategy.value!r} matched 0")
            except PlaywrightTimeout:
                errors.append(f"{strategy.type}={strategy.value!r} timeout")
            except Exception as exc:
                errors.append(f"{strategy.type}={strategy.value!r} {exc}")
        raise LookupError("locator ladder exhausted: " + "; ".join(errors))

    def _locator_for(self, strategy: LocatorStrategy) -> Locator:
        t, v = strategy.type, strategy.value
        if t == "role":
            role = strategy.role or "button"
            return self.page.get_by_role(role, name=re.compile(rf"^{re.escape(v)}$", re.I))
        if t == "label":
            return self.page.get_by_label(v, exact=False)
        if t == "placeholder":
            return self.page.get_by_placeholder(v, exact=False)
        if t == "name":
            return self.page.locator(f"[name={_css_attr(v)}]")
        if t == "id":
            return self.page.locator(f"#{v}")
        if t == "text":
            return self.page.get_by_text(v, exact=True)
        if t == "css":
            return self.page.locator(v)
        if t == "xpath":
            return self.page.locator(f"xpath={v}")
        raise ValueError(f"unknown locator type {t}")

    async def screenshot(self, path: str) -> None:
        await self.page.screenshot(path=path, full_page=True)

    async def _settle(self) -> None:
        try:
            await self.page.wait_for_load_state("networkidle", timeout=5000)
        except PlaywrightTimeout:
            await self.page.wait_for_timeout(300)


def _css_attr(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
