/* Synthetic reports only; run through ops.check's isolated test server. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const output = process.env.UI_TEST_OUTPUT || "playwright-results";

(async () => {
  fs.mkdirSync(output, {recursive:true});
  const fixture = path.join(fs.mkdtempSync(path.resolve(output, "import-fixtures-")), "reports");
  fs.mkdirSync(path.join(fixture, "云杉科技"), {recursive:true});
  fs.mkdirSync(path.join(fixture, "橡树公司"), {recursive:true});
  const first = "# 云杉科技本地研究\n\n这是合成的外部 Agent 报告，用于测试目录导入。";
  for (const agent of ["agent-a", "agent-b", "agent-c"]) fs.mkdirSync(path.join(fixture, "云杉科技", agent), {recursive:true});
  const reportPath = agent => `云杉科技/${agent}/investment-team-20261024.md`;
  const firstPath = path.join(fixture, reportPath("agent-a"));
  fs.writeFileSync(firstPath, first);
  fs.writeFileSync(path.join(fixture, reportPath("agent-b")), first);
  fs.writeFileSync(path.join(fixture, reportPath("agent-c")), first + "\n\n另一个 Agent 的不同判断。");
  fs.writeFileSync(path.join(fixture, "橡树公司/checklist-20261020.md"), "# 橡树公司买入清单\n\n合成的 Markdown 报告。 ");
  fs.writeFileSync(path.join(fixture, "README.md"), "# 目录说明\n不应成为报告。");
  fs.writeFileSync(path.join(fixture, "index.json"), JSON.stringify({reports:["agent-a", "agent-b", "agent-c"].map(agent =>
    ({path:"reports/" + reportPath(agent), title:"云杉科技索引标题", group:"云杉科技", type:"investment-team", date:"2026-10-24"}))}));
  const browser = await chromium.launch({headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const errors = [];
  page.on("pageerror", err => errors.push(err.message));
  const screenshot = name => page.screenshot({path:path.join(output, name + ".png"), fullPage:true});
  const summary = page.locator("#report-import-summary");
  const progress = page.locator("#report-import-progress");
  const selectFolder = async () => {
    const chooser = page.waitForEvent("filechooser");
    await page.locator("#report-import-folder").click();
    await (await chooser).setFiles(fixture);
    await progress.filter({hasText:"预览完成"}).waitFor();
  };
  try {
    await page.goto("http://127.0.0.1:8765");
    await page.locator("#login-username").fill("admin");
    await page.locator("#login-password").fill(process.env.VC_SMOKE_PASSWORD || "visual-test-password");
    await page.locator("#btn-login").click();
    await page.locator("#login-overlay").waitFor({state:"hidden"});
    await page.locator('.nav-item[data-page="reports"]').click();
    await page.locator("#btn-report-import").click();
    await selectFolder();
    assert.match(await summary.textContent(), /待导入 3.*重复 1.*跳过 1.*问题 0/);
    assert.match(await page.locator("#report-import-list").textContent(), /云杉科技索引标题/);
    assert.equal(await page.locator("#report-import-list input:enabled").count(), 3);
    const checkbox = page.locator("#report-import-list input:enabled").first();
    await checkbox.uncheck();
    assert.match(await summary.textContent(), /待导入 2/);
    await checkbox.check();
    await screenshot("report-import-preview-desktop");
    await page.setViewportSize({width:320,height:568});
    await screenshot("report-import-preview-small-mobile");
    assert.ok(await page.evaluate(() => document.querySelector("#report-import-dialog").scrollWidth <= innerWidth));
    assert.ok(await page.locator("#report-import-confirm").evaluate(el => {
      const rect = el.getBoundingClientRect();
      return rect.top >= 0 && rect.bottom <= innerHeight;
    }), "confirmation must stay visible on a small phone");
    await page.setViewportSize({width:430,height:932});
    assert.ok(await page.evaluate(() => document.querySelector("#report-import-dialog").scrollWidth <= innerWidth));
    await screenshot("report-import-preview-mobile");
    await page.locator("#report-import-confirm").click();
    await progress.filter({hasText:"导入完成"}).waitFor();
    assert.match(await summary.textContent(), /待导入 0.*已导入 3/);
    await screenshot("report-import-result-mobile");
    await page.locator("#report-import-close").click();
    await page.waitForFunction(() => document.querySelectorAll("#report-list .list-item").length === 3);
    assert.equal(await page.locator('.chip[data-source="imported"]').getAttribute("class"), "chip active");
    assert.equal(await page.locator("#report-group option").filter({hasText:"云杉科技"}).count(), 1);
    const cloudItems = page.locator('#report-list .list-item').filter({hasText:"云杉科技索引标题"});
    assert.equal(await cloudItems.count(), 2, 'same metadata with different content stays separate; identical copies collapse');
    for (const item of await cloudItems.all()) {
      const sourcePath = await item.locator('.import-source-path').textContent();
      await item.click();
      await page.locator('#report-modal.open').waitFor();
      assert.match(await page.locator('#modal-content').textContent(), /合成的外部 Agent 报告/);
      assert.ok((await page.locator('#modal-meta').textContent()).includes(sourcePath));
      await page.locator('#modal-close').click();
    }
    await screenshot("report-import-library-mobile");
    await page.locator("#report-list .list-item").filter({hasText:"橡树公司"}).click();
    await page.locator("#report-modal.open").waitFor();
    assert.match(await page.locator("#modal-meta").textContent(), /本地导入.*橡树公司/);
    assert.match(await page.locator("#modal-content").textContent(), /合成的 Markdown 报告/);
    const downloadEvent = page.waitForEvent("download");
    await page.locator("#reader-download").click();
    assert.match((await downloadEvent).suggestedFilename(), /\.md$/);
    await page.locator("#modal-close").click();
    await page.locator("#btn-report-import").click();
    // Clear previous successful progress so the next selection waits for its own preview.
    await page.evaluate(() => {document.querySelector("#report-import-progress").textContent = "";});
    await selectFolder();
    assert.match(await summary.textContent(), /待导入 0.*重复 4.*跳过 1/);
    assert.ok(await page.locator("#report-import-confirm").isDisabled());
    await screenshot("report-import-duplicates-mobile");
    fs.writeFileSync(firstPath, first + "\n\n修订版本，保留旧报告。");
    await page.evaluate(() => {document.querySelector("#report-import-progress").textContent = "";});
    await selectFolder();
    assert.match(await summary.textContent(), /待导入 1.*重复 3/);
    await page.locator("#report-import-confirm").click();
    await progress.filter({hasText:"导入完成"}).waitFor();
    await page.locator("#report-import-close").click();
    await page.waitForFunction(() => document.querySelectorAll("#report-list .list-item").length === 4);
    await page.setViewportSize({width:1440,height:1000});
    await screenshot("report-import-library-desktop");
    await page.locator("#report-list .import-remove").first().click();
    await page.locator("#dialog-accept").click();
    await page.waitForFunction(() => document.querySelectorAll("#report-list .list-item").length === 3);
    // Cross a 64-file preview boundary: retain different content, but detect a
    // renamed copy from the earlier batch even though no rows have been saved.
    await page.locator('#btn-report-import').click();
    let previewBatches = 0;
    page.on('request', req => {if (req.url().endsWith('/api/reports/import/preview')) previewBatches++;});
    await page.evaluate(async () => {
      const files = Array.from({length:65}, (_, i) => new File([`# 跨批次研究 ${i}\n合成报告。`], `batch-${i}.md`, {type:'text/markdown'}));
      const renamedCopy = new File([await files[0].text()], 'renamed-copy.md', {type:'text/markdown'});
      await previewReportImport([...files, renamedCopy]);
    });
    assert.equal(previewBatches, 2);
    assert.match(await summary.textContent(), /待导入 65.*重复 1/);
    await page.locator('#report-import-close').click();
    assert.deepEqual(errors, []);
    console.log("Report import UI passed: different content with identical metadata, same-body cross-path deduplication, cross-batch renamed copies, retry/versioning, desktop/mobile, reading/download and owner deletion.");
  } catch (error) {
    await screenshot("report-import-failure").catch(() => {});
    throw error;
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
