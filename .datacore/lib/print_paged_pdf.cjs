/**
 * Print a Paged.js-laid-out HTML file to PDF.
 *
 * Chrome's own `--print-to-pdf` cannot do this: Paged.js keeps timers running
 * while it paginates, so `--virtual-time-budget` never settles and the CLI hangs
 * (observed: 180s timeout, no output). Playwright can wait for the page boxes to
 * exist and for fonts to load, then print. That is the whole reason this file is
 * JavaScript in an otherwise Python lib directory.
 *
 * Resolution of `playwright` comes from NODE_PATH, set by the caller
 * (plur_brand_pdf.py) to whichever install it found.
 *
 * Usage: node print_paged_pdf.cjs <input.html> <output.pdf>
 */
const { chromium } = require('playwright');

const [, , htmlPath, outPath] = process.argv;
if (!htmlPath || !outPath) {
  console.error('usage: print_paged_pdf.cjs <input.html> <output.pdf>');
  process.exit(1);
}

(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  try {
    const page = await browser.newPage();
    page.on('pageerror', (e) => console.error('page error:', e.message));
    await page.goto('file://' + htmlPath, { waitUntil: 'load', timeout: 60000 });
    // Paged.js appends .pagedjs_page per laid-out page and stops adding more when done.
    await page.waitForSelector('.pagedjs_page', { timeout: 90000 });
    await page.waitForFunction(
      () => {
        const n = document.querySelectorAll('.pagedjs_page').length;
        if (window.__lastCount === n) return true;
        window.__lastCount = n;
        return false;
      },
      { timeout: 90000, polling: 500 },
    );
    await page.evaluate(() => document.fonts.ready);
    // Paged.js has already applied @page size and margins inside each page box,
    // so the print itself must add none.
    await page.pdf({
      path: outPath,
      printBackground: true,
      preferCSSPageSize: true,
      margin: { top: '0', bottom: '0', left: '0', right: '0' },
    });
    const pages = await page.evaluate(() => document.querySelectorAll('.pagedjs_page').length);
    console.error(`${pages} pages`);
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e.message);
  process.exit(2);
});
