#!/usr/bin/env python3
"""Render an HTML page headless and report what a linter cannot see.

Static checks (verify-geometry, self_check) read the source. This asks the
browser what actually painted: console errors, page-level horizontal overflow,
and a screenshot you can look at. A diagram can pass every static check and
still have a label sitting on its own arrow.

    python3 diagram_screenshot.py page.html out.png [--viewport]

Default is a full-page capture at device_scale_factor=2; `--viewport` captures
only the fold. Exits non-zero if the page overflowed horizontally or logged a
console error, so it can gate a build.

Requires playwright with chromium installed (`playwright install chromium`).
"""
import asyncio
import pathlib
import sys


async def shoot(src: str, out: str, full_page: bool = True) -> int:
    from playwright.async_api import async_playwright

    src_abs = pathlib.Path(src).resolve()
    errors: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={"width": 1280, "height": 1000}, device_scale_factor=2
        )
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(src_abs.as_uri())
        await page.wait_for_timeout(2500)  # let webfonts settle before capture
        overflow = await page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        await page.screenshot(path=out, full_page=full_page)
        await browser.close()

    print(f"screenshot: {out}")
    print(f"horizontal overflow: {overflow}")
    print(f"console errors: {errors[:5] if errors else 'none'}")
    return 1 if (overflow or errors) else 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(f"usage: {sys.argv[0]} <page.html> <out.png> [--viewport]", file=sys.stderr)
        return 2
    return asyncio.run(shoot(args[0], args[1], full_page="--viewport" not in sys.argv))


if __name__ == "__main__":
    sys.exit(main())
