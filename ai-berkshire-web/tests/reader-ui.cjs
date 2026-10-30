/* Isolated reader regression: serves local assets and synthetic reports only. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const playwright = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assets = path.join(__dirname, "../static");
const output = process.env.UI_TEST_OUTPUT || "playwright-results";
const server = http.createServer((req, res) => {
  if (req.url === "/api/auth/policy") {
    res.setHeader("Content-Type", "application/json");
    return res.end('{"invite_required":false}');
  }
  const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
  const file = pathname === "/" ? "index.html" : pathname.replace(/^\/static\//, "");
  if (!["index.html", "app.js", "workspace.js", "report-import.js", "style.css", "workspace.css"].includes(file)
      && !/^brand\/[a-z0-9-]+\.(svg|png|ico)$/.test(file)) {
    res.writeHead(404);
    return res.end();
  }
  const type = {'.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png','.ico':'image/vnd.microsoft.icon'}[path.extname(file)] || 'text/html';
  res.setHeader("Content-Type", type);
  res.end(fs.readFileSync(path.join(assets, file)));
});
async function reachable(page, selector) {
  const box = await page.locator(selector).boundingBox();
  const viewport = page.viewportSize();
  assert.ok(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height + 1, selector + " stays in the viewport");
  assert.ok(await page.locator(selector).evaluate(el => {
    const r = el.getBoundingClientRect();
    return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
  }), selector + " is not covered or clipped");
}
async function alignedReaderActions(page) {
  assert.ok(await page.locator('.modal-box').evaluate(el => {
    const rect = el.getBoundingClientRect();
    return el.contains(document.elementFromPoint(rect.x + 8, rect.y + 8));
  }), 'dialog is above the application navigation');
  const selectors = ["#reader-copy", "#reader-download", "#reader-focus", "#modal-close"];
  for (const selector of selectors) await reachable(page, selector);
  const boxes = await Promise.all(selectors.map(selector => page.locator(selector).boundingBox()));
  const centers = boxes.slice(0, 3).map(box => box.y + box.height / 2);
  assert.ok(Math.max(...centers) - Math.min(...centers) <= 1, "report actions share a baseline");
  for (let index = 1; index < 3; index++) assert.ok(boxes[index].x >= boxes[index - 1].x + boxes[index - 1].width, "report actions do not overlap");
  if (page.viewportSize().width > 800) {
    assert.ok(Math.abs(boxes[3].y + boxes[3].height / 2 - centers[0]) <= 1, "desktop close aligns with report actions");
    assert.ok(boxes[3].x - boxes[2].x - boxes[2].width >= 16, "close is separated from fullscreen");
  } else {
    assert.ok(boxes[3].y + boxes[3].height <= boxes[0].y, "mobile close stays above the report actions");
    assert.ok(boxes.every(box => box.height >= 44), "mobile controls have touch-sized targets");
  }
}
(async () => {
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  fs.mkdirSync(output, { recursive: true });
  try {
    for (const engine of (process.env.READER_BROWSERS || "chromium").split(",")) {
      const browser = await playwright[engine].launch({ headless: true });
      try {
        for (const viewport of [{ width: 1440, height: 1000 }, { width: 320, height: 568 }, { width: 390, height: 664 }, { width: 430, height: 780 }, { width: 844, height: 390 }]) {
          const page = await browser.newPage({ viewport, hasTouch: viewport.width < 900 });
          const errors = [];
          page.on("pageerror", error => errors.push(error.message));
          await page.goto(`http://127.0.0.1:${server.address().port}`);
          await page.locator("#login-username").waitFor();
          await page.evaluate(() => {
            hideLogin();
            const trigger = document.createElement("button");
            trigger.id = "reader-test-trigger";
            trigger.textContent = "打开测试报告";
            trigger.onclick = () => {
              document.querySelector("#modal-title").textContent = "流水线运行 · 贵州茅台 / 腾讯 · 较长标题换行测试";
              document.querySelector("#modal-meta").textContent = "本地导入 · 导入者 测试研究员 · 2026-10-15 · BYD/BYD-financial-valuation-20261015.md";
              const source = Array.from({ length: 35 }, (_, i) => `## ${i + 1}、运行概览\n\n测试报告正文。\n\n| 阶段 | 输入 | 通过 |\n|---|---|---|\n| 筛选 | 2 | 2 |`).join("\n\n");
              document.querySelector("#modal-content").innerHTML = renderMarkdown(source);
              showReportReader(source);
            };
            document.body.appendChild(trigger);
          });
          const open = async () => {
            // Open by keyboard so the focus-restoration precondition is explicit
            // across engines (pointer activation need not focus a button).
            await page.locator("#reader-test-trigger").focus();
            await page.locator("#reader-test-trigger").press("Enter");
            await page.locator("#report-modal.open").waitFor();
          };
          const scroll = () => page.locator(".reader-body").evaluate(el => { el.scrollTop = el.scrollHeight; });
          await open();
          await alignedReaderActions(page);
          for (const id of ["reader-copy", "reader-download", "reader-focus", "modal-close"]) {
            const button = page.locator(`#${id}`);
            assert.equal((await button.textContent()).trim(), "", "toolbar uses icons without visible text");
            assert.equal(await button.locator("svg").count(), 1);
            assert.ok(await button.getAttribute("aria-label"), "icon has an accessible name");
          }
          await page.locator(".modal-box").focus();
          await page.keyboard.press("Tab");
          assert.equal(await page.evaluate(() => document.activeElement.id), "reader-copy");
          assert.equal(await page.locator("#reader-copy").evaluate(el => getComputedStyle(el, "::after").visibility), "visible", "keyboard focus shows tooltip");
          if (viewport.width >= 900) {
            await page.locator("#reader-download").hover();
            assert.equal(await page.locator("#reader-download").evaluate(el => getComputedStyle(el, "::after").visibility), "visible", "hover shows tooltip");
          }
          await page.locator(".modal-box").focus();
          await page.mouse.move(0, 0);
          await page.screenshot({ path: path.join(output, `reader-toolbar-${engine}-${viewport.width}.png`) });
          const header = await page.locator(".modal-head").boundingBox();
          await scroll();
          assert.ok(await page.locator(".reader-body").evaluate(el => el.scrollTop > 0), "long report scrolls");
          assert.equal((await page.locator(".modal-head").boundingBox()).y, header.y, "header stays visible when reading");
          await reachable(page, "#modal-close");
          await reachable(page, "#reader-focus");
          await page.locator("#reader-focus").click();
          assert.equal(await page.locator("#reader-focus").getAttribute("aria-pressed"), "true");
          assert.equal(await page.locator("#reader-focus").getAttribute("aria-label"), "退出全屏");
          assert.equal(await page.locator(".reader-expand-icon").evaluate(el => getComputedStyle(el).display), "none");
          assert.notEqual(await page.locator(".reader-contract-icon").evaluate(el => getComputedStyle(el).display), "none");
          const fullscreen = await page.locator(".modal-box.fullscreen").boundingBox();
          assert.ok(Math.abs(fullscreen.width - viewport.width) <= 1 && Math.abs(fullscreen.height - viewport.height) <= 1, "reader fills viewport");
          await scroll();
          await alignedReaderActions(page);
          await page.screenshot({ path: path.join(output, `reader-${engine}-${viewport.width}-fullscreen.png`) });
          await page.locator("#reader-focus").click();
          assert.equal(await page.locator(".modal-box.fullscreen").count(), 0);
          assert.equal(await page.locator("#reader-focus").getAttribute("data-tooltip"), "全屏阅读");
          assert.equal(await page.locator("#reader-focus svg").count(), 1, "fullscreen toggle preserves icon");
          await page.locator("#reader-focus").click();
          await page.keyboard.press("Escape");
          assert.equal(await page.locator(".modal-box.fullscreen").count(), 0);
          assert.equal(await page.locator("#report-modal.open").count(), 1, "first Escape only exits fullscreen");
          await page.keyboard.press("Escape");
          assert.equal(await page.locator("#report-modal.open").count(), 0);
          assert.equal(await page.evaluate(() => document.activeElement.id), "reader-test-trigger");
          await open();
          await page.locator("#reader-focus").click();
          await page.locator("#modal-close").click();
          assert.equal(await page.locator(".fullscreen").count(), 0, "closing clears fullscreen");
          assert.equal(await page.locator("body.has-modal,body.has-fullscreen").count(), 0, "closing restores background scrolling");
          await open();
          assert.equal(await page.locator("#reader-focus").getAttribute("aria-pressed"), "false");
          await page.locator("#modal-close").focus();
          await page.keyboard.press("Shift+Tab");
          assert.equal(await page.evaluate(() => document.activeElement.closest("#report-modal")?.id), "report-modal", "focus remains inside reader");
          await page.locator("#modal-close").click();
          await page.evaluate(() => enterFs(document.querySelector("#research-output")));
          await reachable(page, "#fs-exit");
          await page.locator("#fs-exit").click();
          assert.equal(await page.locator(".fullscreen").count(), 0, "standalone report still exits fullscreen");
          assert.deepEqual(errors, []);
          await page.close();
          console.log(`${engine} reader passed: ${viewport.width}x${viewport.height}`);
        }
      } finally { await browser.close(); }
    }
  } finally { await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
