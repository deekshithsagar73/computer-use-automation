"""Week 1 gate: log into ParaBank with hard-coded locators, no LLM."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from capability_runtime.policy.engine import Policy  # noqa: E402
from capability_runtime.schema.capability import LocatorStrategy, Target  # noqa: E402
from capability_runtime.surface.playwright_surface import Surface  # noqa: E402


async def main() -> int:
    load_dotenv(override=True)
    url = os.getenv("TARGET_URL", "https://parabank.parasoft.com/parabank/index.htm")
    user = os.getenv("PARABANK_USERNAME", "john")
    password = os.getenv("PARABANK_PASSWORD", "demo")
    policy = Policy()
    policy.check_navigate(url)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        surface = Surface(page)
        await surface.goto(url)
        await surface.type_text(Target(locators=[LocatorStrategy(type="name", value="username")]), user)
        await surface.type_text(Target(locators=[LocatorStrategy(type="name", value="password")]), password)
        await surface.click(
            Target(
                locators=[
                    LocatorStrategy(type="role", role="button", value="Log In"),
                    LocatorStrategy(type="css", value="input[type='submit'][value='Log In']"),
                ]
            )
        )
        snap = await surface.observe()
        print("url:", snap.url)
        print("title:", snap.title)
        links = [c for c in snap.controls if c.role == "link" and (c.name or "").isdigit()]
        if links:
            print("first_account:", links[0].name)
        else:
            print("page_text_head:", snap.text[:400])
        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
