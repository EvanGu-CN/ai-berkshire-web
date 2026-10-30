/* Run only via python -m ops.showcase. Never connect this capture to a real site. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = 'http://127.0.0.1:8766';

(async () => {
  assert.ok(process.env.VC_SHOWCASE_TOKEN && process.env.SHOWCASE_OUTPUT);
  const identity = await (await fetch(base + '/__showcase__')).json();
  assert.equal(identity.token, process.env.VC_SHOWCASE_TOKEN);
  assert.equal(identity.synthetic, true);
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1080 }, deviceScaleFactor: 1, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = [];
  const external = [];
  const modelCalls = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (route.request().method() === 'POST' && /\/api\/(research|pipeline|memory)\//.test(url.pathname)) {
      modelCalls.push(url.pathname);
      return route.abort();
    }
    if (url.origin === base || url.protocol === 'data:') return route.continue();
    external.push(url.origin);
    return route.abort();
  });
  const nav = async name => {
    if (await page.locator('#menu-toggle').isVisible()) await page.locator('#menu-toggle').click();
    await page.locator(`.nav-item[data-page="${name}"]`).click();
    await page.locator(`#page-${name}.active`).waitFor();
    await page.evaluate(() => window.scrollTo(0, 0));
  };
  const screenshot = async name => {
    if (name === 'workbench' || name === 'settings') {
      const height = await page.evaluate(() => document.documentElement.scrollHeight);
      await page.setViewportSize({ width: 1440, height });
    }
    await page.evaluate(() => document.fonts.ready);
    await page.mouse.move(0, 0);
    await page.waitForFunction(() => [...document.images].every(img => img.complete));
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), name + ' overflows');
    await page.screenshot({ path: path.join(process.env.SHOWCASE_OUTPUT, name + '.png'), animations: 'disabled' });
    if (name === 'workbench' || name === 'settings') await page.setViewportSize({ width: 1440, height: 1080 });
  };
  try {
    await page.goto(base);
    await page.locator('#login-username').fill('演示研究员');
    await page.locator('#login-password').fill('showcase-only-password');
    await page.locator('#btn-login').click();
    await page.locator('#login-overlay').waitFor({ state: 'hidden' });
    await page.locator('.skill-item[data-id="investment-research"]').click();
    await page.locator('#research-args').fill('示例科技');
    await page.locator('#dup-summary').filter({ hasText: '找到 4 份' }).waitFor();
    await page.locator('#run-market').selectOption('a');
    await screenshot('workbench');

    await nav('reports');
    await page.locator('#report-list .list-item').first().waitFor();
    await page.locator('#report-dates-period').selectOption('6m');
    await page.locator('#report-count').filter({ hasText: '共 8 份' }).waitFor();
    await screenshot('reports');

    await page.locator('#report-list .list-item').filter({ hasText: '示例科技 · 财务估值分析' }).click();
    await page.locator('#report-modal.open').waitFor();
    await page.locator('#modal-content').filter({ hasText: '虚构公司与合成资料' }).waitFor();
    await screenshot('reader');
    await page.locator('#modal-close').click();

    await page.locator('#btn-report-import').click();
    await page.locator('#report-import-dialog[open]').waitFor();
    const file = (company, topic) => ({ name: `${company}-${topic}.md`, mimeType: 'text/markdown',
      buffer: Buffer.from(`---\ncompany: ${company}\ntype: ${topic}\ndate: ${identity.date}\n---\n# ${company} · ${topic}\n\n合成演示内容。\n`) });
    const first = file('示例科技', '现金流分析');
    const files = [first, file('示例消费', '商业模式'), { ...first, name: '示例科技-现金流分析-副本.md' }];
    await page.locator('#report-import-files-input').setInputFiles(files);
    await page.locator('#report-import-confirm:not([disabled])').waitFor();
    await screenshot('import');
    await page.locator('#report-import-close').click();

    await nav('timeline');
    await page.locator('#tl-company option[value="示例科技"]').waitFor({ state: 'attached' });
    await page.locator('#tl-company').selectOption('示例科技');
    await page.locator('#tl-status').filter({ hasText: '共 4 份报告' }).waitFor();
    await page.locator('#tl-chart polyline').waitFor();
    await screenshot('timeline');

    await nav('settings');
    await page.locator('#cfg-list .cfg-card').first().waitFor();
    await page.locator('#btn-cfg-cancel').click();
    await page.locator('#cfg-name').fill('我的模型连接');
    await page.locator('#cfg-provider').selectOption('custom-openai');
    assert.equal(await page.locator('#cfg-api-key').inputValue(), '');
    await page.locator('#page-settings h1').click();
    await page.evaluate(() => window.scrollTo(0, 0));
    await screenshot('settings');

    await page.setViewportSize({ width: 390, height: 844 });
    await nav('reports');
    await page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().right <= 1);
    await page.locator('#report-list .list-item').filter({ hasText: '示例科技 · 财务估值分析' }).click();
    await page.locator('#report-modal.open').waitFor();
    await screenshot('mobile');
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    assert.deepEqual(modelCalls, []);
    fs.writeFileSync(path.join(process.env.SHOWCASE_OUTPUT, 'capture.json'), JSON.stringify({
      synthetic: true, realModelCalls: modelCalls.length, browserErrors: errors.length, externalRequests: external.length,
      images: ['workbench', 'reports', 'reader', 'import', 'timeline', 'settings', 'mobile'],
      desktop: '1440px wide', mobile: '390x844'
    }, null, 2) + '\n');
    console.log('Captured 7 synthetic screenshots; no browser errors or external requests.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
