"""Probe ParaBank Find Transactions flow for capability authoring."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from capability_runtime.schema.capability import LocatorStrategy, Target  # noqa: E402
from capability_runtime.surface.playwright_surface import Surface  # noqa: E402


async def main() -> int:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        s = Surface(page)
        await s.goto("https://parabank.parasoft.com/parabank/index.htm")
        await s.type_text(Target(locators=[LocatorStrategy(type="name", value="username")]), "john")
        await s.type_text(Target(locators=[LocatorStrategy(type="name", value="password")]), "demo")
        await s.click(Target(locators=[LocatorStrategy(type="role", value="Log In", role="button")]))
        await s.click(Target(locators=[LocatorStrategy(type="role", value="Find Transactions", role="link")]))
        print("findtrans url:", page.url)
        html = await page.content()
        for tag in re.findall(r"<select[^>]+>", html):
            print("select:", tag)
        await page.locator("select#accountId").select_option("13344")
        # Date range form uses name attributes on the third search block.
        from_date = page.locator("form").nth(2).locator("input.input").first
        to_date = page.locator("form").nth(2).locator("input.input").nth(1)
        await from_date.fill("01-01-2024")
        await to_date.fill("12-31-2026")
        await page.locator("form").nth(2).locator("input[type='submit']").click()
        await page.wait_for_load_state("domcontentloaded")
        print("result url:", page.url)
        body = await page.inner_text("body")
        print(body[:2000])
        rows = await page.locator("#transactionTable tbody tr").count()
        print("transaction rows:", rows)
        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
