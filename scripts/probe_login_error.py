import asyncio

from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://parabank.parasoft.com/parabank/index.htm", wait_until="domcontentloaded")
        await page.locator("[name=username]").fill("no_such_member_99999")
        await page.locator("[name=password]").fill("wrong")
        await page.locator("input[type=submit][value='Log In']").click()
        await page.wait_for_timeout(2500)
        body = await page.inner_text("body")
        print("URL", page.url)
        print(body[:1200])
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
