import asyncio

from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://parabank.parasoft.com/parabank/index.htm", wait_until="domcontentloaded")
        await page.locator("[name=username]").fill("john")
        await page.locator("[name=password]").fill("demo")
        await page.locator("input[type=submit][value='Log In']").click()
        await page.wait_for_url("**/overview.htm", timeout=15000)
        await page.goto("https://parabank.parasoft.com/parabank/activity.htm?id=00000")
        await page.wait_for_timeout(2000)
        print("URL", page.url)
        print((await page.inner_text("body"))[:1500])
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
