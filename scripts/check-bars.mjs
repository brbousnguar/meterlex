#!/usr/bin/env node
/* The chart's readout must never cover what you are pointing at, and a part must focus its harness.
 *
 *   node scripts/check-bars.mjs http://127.0.0.1:4180/
 *
 * Checks, at 1440×900 and then on a phone:
 *   - pointing at the top of any column leaves that point uncovered, and selects the column;
 *   - pointing at a part focuses its harness (the plot recedes the others);
 *   - at the right-most column the readout flips to its left;
 *   - leaving the plot hides the readout (a desktop pointer only);
 *   - on a phone the readout is a panel above the plot, and a tap selects a bar.
 * Playwright comes from PWA_UX_PLAYWRIGHT, Chromium from PWA_UX_CHROMIUM.
 */
const url = process.argv[2];
if (!url) { console.error("usage: check-bars.mjs <url>"); process.exit(2); }
const pw = process.env.PWA_UX_PLAYWRIGHT || "playwright";
const { chromium } = await import(`${pw}/index.mjs`);
const browser = await chromium.launch({ executablePath: process.env.PWA_UX_CHROMIUM || undefined });
const fail = [];

{
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  await page.evaluate(() => document.querySelector(".bars").scrollIntoView({ block: "center" }));
  const cols = page.locator(".bars-col");
  const n = await cols.count();
  const covers = ([x, y]) => {
    const r = document.querySelector(".readout").getBoundingClientRect();
    return getComputedStyle(document.querySelector(".readout")).visibility === "visible"
      && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  };
  for (let i = 0; i < n; i++) {
    const top = await cols.nth(i).locator("i").last().boundingBox();   // the topmost part of the bar
    if (!top) continue;
    const px = top.x + top.width / 2, py = top.y + Math.min(5, top.height / 2);
    await page.mouse.move(px, py);
    await page.waitForTimeout(220);
    if (await page.evaluate(covers, [px, py])) fail.push(`the readout covers the top of column ${i}`);
    if (await cols.nth(i).getAttribute("data-picked") !== "true") fail.push(`pointing at column ${i} did not select it`);
    // A band thinner than the pointer is not a target; the legend focuses those.
    if (top.height >= 6 && await page.locator(".bars-plot").getAttribute("data-focusing") !== "true") {
      fail.push(`pointing at a part of column ${i} did not focus its harness`);
    }
  }
  const last = await cols.nth(n - 1).boundingBox();
  await page.mouse.move(last.x + last.width / 2, last.y + last.height - 4);
  await page.waitForTimeout(220);
  const box = await page.locator(".readout").boundingBox();
  if (box.x + box.width > last.x + 1) fail.push("the readout did not flip left of the right-most column");
  await page.mouse.move(8, 8);
  await page.waitForTimeout(250);
  if (await page.locator(".readout").evaluate((el) => getComputedStyle(el).visibility) !== "hidden") {
    fail.push("the readout stayed on screen after the pointer left the plot");
  }
  await page.close();
}

{
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  await page.evaluate(() => document.querySelector(".bars-plot").scrollIntoView({ block: "center" }));
  await page.waitForTimeout(200);
  const ro = await page.locator(".readout").boundingBox();
  const plot = await page.locator(".bars-plot").boundingBox();
  if (ro.y + ro.height > plot.y + 1) fail.push("phone: the readout overlaps the plot");
  const col = await page.locator(".bars-col").nth(0).boundingBox();
  await page.touchscreen.tap(col.x + col.width / 2, col.y + col.height - 10);
  await page.waitForTimeout(250);
  if (await page.locator(".bars-col").nth(0).getAttribute("data-picked") !== "true") {
    fail.push("phone: tapping the first bar did not select it");
  }
  await page.close();
}

await browser.close();
if (fail.length) { fail.forEach((f) => console.log("FAIL ", f)); process.exit(1); }
console.log("PASS  readout clear of every column top, focuses its harness, flips at the right edge, leaves with the pointer, a panel above the plot on a phone");
