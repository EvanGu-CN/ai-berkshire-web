/* Run against tests/smoke_server.py only. Never use a production URL. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = "http://127.0.0.1:8765";
const output = process.env.UI_TEST_OUTPUT || "playwright-results";
const password = process.env.VC_SMOKE_PASSWORD || "visual-test-password";
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
(async () => {
  fs.mkdirSync(output, { recursive: true });
  for (let i = 0; i < 30; i++) {
    try {
      const r = await fetch(base + "/api/auth/policy");
      if (r.ok) break;
    } catch (_) {}
    if (i === 29) throw Error("Test server did not become ready");
    await pause(500);
  }
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
        viewport: { width: 1440, height: 1000 },
      }),
      errors = [];
    page.on("pageerror", (err) => errors.push(err.message));
    const screenshot = (name) =>
      page.screenshot({
        path: path.join(output, name + ".png"),
        fullPage: true,
        animations: "disabled",
      });
    const noOverflow = async (name) =>
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
        name + " overflows viewport",
      );
    const nav = async (name) => {
      // An active page class does not mean its asynchronous settings render is done.
      const oldConfig = name === "settings" && await page.locator("#cfg-list .cfg-card").count()
        ? await page.locator("#cfg-list .cfg-card").first().elementHandle() : null;
      if (await page.locator("#menu-toggle").isVisible())
        await page.locator("#menu-toggle").click();
      await page.locator(`.nav-item[data-page="${name}"]`).click();
      await page.locator("#page-" + name + ".active").waitFor();
      if (name === "settings") {
        if (oldConfig) await page.waitForFunction(card => !card.isConnected, oldConfig);
        await page.locator("#cfg-list .cfg-card").first().waitFor();
      }
    };
    await page.goto(base);
    await page.waitForFunction(() => [...document.querySelectorAll('.brand-logo img')].every(img => img.complete && img.naturalWidth > 0));
    const brandAssets = await page.evaluate(async () => {
      const icons = [...document.querySelectorAll('link[rel="icon"],link[rel="apple-touch-icon"]')].map(link => link.href);
      await Promise.all(icons.map(async href => {const image = new Image(); image.src = href; await image.decode();}));
      return icons.length;
    });
    assert.equal(brandAssets, 4);
    const favicon = await page.request.get(base + "/favicon.ico");
    assert.equal(favicon.status(), 200);
    assert.match(favicon.headers()["content-type"], /image\//);
    assert.equal((await favicon.body()).readUInt16LE(4), 3);
    await screenshot("login-desktop");
    await page.setViewportSize({width:320,height:700});
    await noOverflow("brand on narrow login");
    await screenshot("brand-login-mobile");
    await page.setViewportSize({width:1440,height:1000});
    await page.locator("#btn-register").click();
    await page.locator("#password-confirm-field:not([hidden])").waitFor();
    assert.equal(await page.locator("#btn-login").textContent(), "创建账户");
    await page.locator("#btn-register").click();
    await page.locator("#password-confirm-field[hidden]").waitFor({state:"attached"});
    await page.locator("#login-username").fill("admin");
    await page.locator("#login-password").fill(password);
    await page.locator("#btn-login").click();
    await page.locator("#login-overlay").waitFor({ state: "hidden" });
    await page.locator(".skill-item").first().waitFor();
    await noOverflow("desktop research");
    await screenshot("research-desktop");
    await page.locator("#skill-search").fill("财报");
    assert.ok((await page.locator(".skill-item").count()) >= 2);
    await page.locator("#skill-search").fill("impossible-unique-query");
    await page.getByText("没有匹配的方法", { exact: true }).waitFor();
    await page.locator("#skill-search").fill("");
    await page.locator('.skill-item[data-id="quality-screen"]').click();
    assert.equal(
      await page
        .locator('.skill-item[data-id="quality-screen"]')
        .getAttribute("aria-pressed"),
      "true",
    );
    // Recent-report reference: real metadata/date filtering, pagination and reading.
    let researchRequests = 0;
    page.on("request", req => { if (req.url().endsWith("/api/research/run")) researchRequests++; });
    await page.locator("#research-args").fill("报告复用验收公司");
    await page.locator("#dup-summary").filter({hasText: "近6个月找到 8 份"}).waitFor();
    assert.equal(await page.locator('.dup-ranges [aria-pressed="true"]').textContent(), "近6个月");
    assert.equal(await page.locator(".dup-row").count(), 3);
    await page.locator("#dup-next").click();
    await page.locator("#dup-page").filter({hasText: "第2页，共3页"}).waitFor();
    assert.equal(await page.locator(".dup-row").count(), 3);
    await page.locator('#dup-page button[data-page="3"]').click();
    await page.locator("#dup-page").filter({hasText: "第3页，共3页"}).waitFor();
    assert.equal(await page.locator('#dup-page [aria-current="page"]').textContent(), "3");
    assert.equal(await page.locator(".dup-row").count(), 2);
    await page.locator(".dup-row").last().click();
    await page.locator("#report-modal.open").waitFor();
    assert.match(await page.locator("#modal-title").textContent(), /合成研究 7/);
    await page.locator("#modal-close").click();
    for (const [period, count] of [["1w",7],["1m",7],["3m",8],["12m",9],["6m",8]]) {
      await page.locator(`.dup-ranges [data-period="${period}"]`).click();
      await page.locator("#dup-summary").filter({hasText: `找到 ${count} 份`}).waitFor();
      assert.equal(await page.locator(`#dedup-hint [data-period="${period}"]`).getAttribute("aria-pressed"), "true");
      assert.match(await page.locator("#dup-page").textContent(), /^第1页，共/);
    }
    assert.equal(researchRequests, 0, "reading and filtering never starts a model request");
    await page.locator("#dedup-hint").scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,"recent-reports-desktop.png")});
    await page.setViewportSize({width:320,height:700});
    await page.waitForFunction(() => document.querySelector(".sidebar").getBoundingClientRect().right <= 1);
    await page.locator("#dedup-hint").scrollIntoViewIfNeeded();
    await noOverflow("recent reports on narrow mobile");
    for (const range of await page.locator(".dup-ranges button").all()) assert.ok((await range.boundingBox()).height >= 44);
    await page.locator("#dedup-hint").screenshot({path:path.join(output,"recent-reports-mobile.png")});
    await page.setViewportSize({width:1440,height:1000});
    await page.locator("#research-args").fill("没有匹配报告的合成企业");
    await page.locator("#dup-summary").filter({hasText:"未找到相关报告"}).waitFor();
    assert.equal(await page.locator(".dup-ranges button").count(), 5, "empty state keeps ranges available");
    // A cancelled older search must not replace a newer keyword's results.
    await page.route("**/api/reports/recent?**", async route => {
      if (new URL(route.request().url()).searchParams.get("keyword") !== "过期查询测试") return route.continue();
      await pause(1500);
      await route.fulfill({json:{period_label:"旧结果",total:99,items:[],offset:0,since:"",until:""}}).catch(() => {});
    });
    const older = page.waitForRequest(req => req.url().includes(encodeURIComponent("过期查询测试")));
    await page.locator("#research-args").fill("过期查询测试");
    await older;
    await page.locator("#research-args").fill("报告复用验收公司");
    await page.locator("#dup-summary").filter({hasText:"近6个月找到 8 份"}).waitFor();
    await pause(1600);
    assert.doesNotMatch(await page.locator("#dup-summary").textContent(), /旧结果/);
    await page.unroute("**/api/reports/recent?**");
    // A stuck request times out; retry and creating new research remain available.
    await page.route("**/api/reports/recent?**", route => {
      if (new URL(route.request().url()).searchParams.get("keyword") === "超时查询测试") return;
      return route.continue();
    });
    await page.locator("#research-args").fill("超时查询测试");
    await page.locator("#dup-summary").filter({hasText:"查询超时"}).waitFor({timeout:12000});
    assert.ok(await page.locator("#btn-run").isEnabled());
    await page.unroute("**/api/reports/recent?**");
    await page.locator("#dup-retry").click();
    await page.locator("#dup-summary").filter({hasText:"未找到相关报告"}).waitFor();
    // The archive link opens all sources and clears stale report-library filters.
    await page.locator("#research-args").fill("报告复用验收公司");
    await page.locator("#dup-summary").filter({hasText:"近6个月找到 8 份"}).waitFor();
    await page.locator("#dup-library").click();
    await page.locator("#page-reports.active").waitFor();
    assert.equal(await page.locator("#report-search").inputValue(), "报告复用验收公司");
    await page.locator("#report-count").filter({hasText:"11"}).waitFor();
    await page.locator("#report-dates-period").selectOption("1m");
    await page.locator("#report-count").filter({hasText:"共 7 份"}).waitFor();
    await page.locator("#report-dates-period").selectOption("12m");
    await page.locator("#report-count").filter({hasText:"共 9 份"}).waitFor();
    await page.locator("#report-dates-period").selectOption("undated");
    await page.locator("#report-count").filter({hasText:"共 1 份"}).waitFor();
    assert.match(await page.locator("#report-list").textContent(), /日期未知/);
    await page.locator("#report-dates-period").selectOption("custom");
    await page.locator("#report-dates-from").fill("2020-01-01");
    await page.locator("#report-dates-to").fill("2020-02-01");
    await page.locator("#report-count").filter({hasText:"共 0 份"}).waitFor();
    assert.ok(await page.locator("#report-next").isDisabled());
    await page.locator("#report-dates-from").fill("2021-01-01");
    await page.locator("#report-dates .date-filter-status").filter({hasText:"开始日期不能晚于结束日期"}).waitFor();
    await page.locator("#report-dates-period").selectOption("all");
    await page.locator("#report-count").filter({hasText:"共 11 份"}).waitFor();
    await page.locator("#report-search").fill("");
    await page.locator('#report-page-info button[data-page="2"]').waitFor();
    await page.locator('#report-page-info button[data-page="2"]').click();
    await page.locator("#report-page-info .page-summary").filter({hasText:"第2页"}).waitFor();
    await page.locator("#report-dates-period").selectOption("1w");
    await page.locator("#report-count").filter({hasText:"共 7 份"}).waitFor();
    assert.match(await page.locator("#report-page-info").textContent(), /^第1页/);
    await screenshot("report-date-filter-desktop");
    await page.setViewportSize({width:320,height:700});
    await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right <= 1);
    await page.locator("#report-dates").scrollIntoViewIfNeeded();
    await noOverflow("report date filters on mobile");
    await page.locator("#report-dates").screenshot({path:path.join(output,"report-date-filter-mobile.png")});
    await page.setViewportSize({width:1440,height:1000});
    await page.locator("#report-dates-period").selectOption("all");
    await nav("research");
    await page.locator("#research-args").fill("界面测试示例公司");
    await page.locator("#btn-run").click();
    await page
      .locator("#output-status")
      .filter({ hasText: "研究完成" })
      .waitFor({ timeout: 30000 });
    const content = page.locator("#research-output");
    await content.click();
    assert.equal(await content.getAttribute("class"), "markdown");
    await nav("history");
    await page.locator(".list-item").first().waitFor();
    await page.locator("#history-dates-period").selectOption("custom");
    await page.locator("#history-dates-from").fill("2020-01-01");
    await page.locator("#history-dates-to").fill("2020-02-01");
    await page.locator("#history-list").filter({hasText:"当前条件下暂无"}).waitFor();
    await page.locator("#history-dates-period").selectOption("all");
    await page.locator("#history-list .list-item").first().waitFor();
    assert.match(await page.locator("#history-page-info").textContent(), /^第1页，共1页/);
    await page.locator(".list-item").first().click();
    await page.locator("#report-modal.open").waitFor();
    assert.ok((await page.locator(".reader-toc a").count()) > 1);
    await page.locator("#reader-focus").click();
    await page.locator("#report-modal .modal-box.fullscreen").waitFor();
    await page.keyboard.press("Escape");
    assert.ok(
      await page
        .locator("#report-modal")
        .evaluate((el) => el.classList.contains("open")),
    );
    await page.locator("#modal-close").click();
    await nav("research");
    await page.route("**/api/research/run", route => route.abort("connectionreset"));
    await page.locator("#btn-run").click();
    await page.locator("#output-status").filter({hasText: "无法连接研究服务"}).waitFor();
    assert.match(await page.locator("#research-output").textContent(), /研究历史/);
    assert.doesNotMatch(await page.locator("#research-output").textContent(), /检查 API Key/);
    assert.ok(await page.locator("#btn-run").isVisible());
    assert.ok(await page.locator("#btn-stop").isHidden());
    await screenshot("research-connection-error-desktop");
    await page.setViewportSize({width:390,height:844});
    await noOverflow("research network failure on mobile");
    await screenshot("research-connection-error-mobile");
    await page.setViewportSize({width:1440,height:1000});
    await page.unroute("**/api/research/run");
    await nav("memory");
    await page.locator("#conclusion-dates-period").selectOption("undated");
    await page.locator("#concl-count").filter({hasText:"共 0 条"}).waitFor();
    await page.locator("#conclusion-dates-period").selectOption("all");
    await page.locator("#memory-dates-period").selectOption("1m");
    await page.locator("#mem-question").fill("合成日期筛选验收问题");
    const askRequest = page.waitForRequest(req=>req.url().endsWith("/api/memory/ask"));
    await page.locator("#btn-ask").click();
    assert.equal((await askRequest).postDataJSON().period, "1m");
    await page.locator("#mem-ask-status").filter({hasText:"完成"}).waitFor();
    assert.match(await page.locator("#mem-answer").textContent(), /当前条件下未找到/);
    await nav("account");
    await page.locator("#account-overview .stat-card").first().waitFor();
    await screenshot("account-desktop");
    await page.locator("#btn-invite").click();
    await page.locator("[data-invite]").first().waitFor();
    await nav("users");
    await page.locator("#users-body tr").filter({ hasText: "admin" }).waitFor();
    await screenshot("admin-desktop");
    await page.locator("#user-search").fill("测试VIP");
    assert.equal(await page.locator("#users-body tr").count(), 1);
    await page.locator("#user-search").fill("");
    // Run the real pipeline endpoint with the isolated server's demo model.
    await nav("pipeline");
    let pipelineRequests = 0;
    page.on("request", req => {
      if (req.url().endsWith("/api/pipeline/run")) pipelineRequests++;
    });
    await page.locator("#btn-pipe-run").click();
    assert.match(await page.locator("#pipe-status").textContent(), /请输入 1–30/);
    await page.locator("#pipe-targets").fill("腾讯");
    for (const stage of ["screen", "checklist", "deep"]) await page.locator("#stage-" + stage).uncheck();
    await page.locator("#btn-pipe-run").click();
    assert.match(await page.locator("#pipe-status").textContent(), /至少选择/);
    await page.locator("#stage-deep").check();
    await page.locator("#pipe-deep-count").fill("6");
    await page.locator("#btn-pipe-run").click();
    assert.match(await page.locator("#pipe-status").textContent(), /1–5/);
    assert.equal(pipelineRequests, 0);
    await page.locator("#pipe-deep-count").fill("1");
    await page.locator("#pipe-targets").press("Enter");
    await page.locator("#pipe-status").filter({ hasText: "流水线完成" }).waitFor();
    assert.equal(pipelineRequests, 1);
    assert.ok(await page.locator("#btn-pipe-run").isEnabled());
    assert.ok(await page.locator("#btn-pipe-stop").isHidden());
    assert.match(await page.locator("#pipe-deep-output").textContent(), /演示模式/);
    // Rejections must show an error and release the button for retry.
    await page.route("**/api/pipeline/run", route => route.fulfill({
      status: 503, contentType: "application/json", body: JSON.stringify({detail: "测试服务暂不可用"}),
    }));
    await page.locator("#btn-pipe-run").click();
    await page.locator("#pipe-status").filter({ hasText: "运行失败：测试服务暂不可用" }).waitFor();
    assert.ok(await page.locator("#btn-pipe-run").isEnabled());
    assert.ok(await page.locator("#btn-pipe-stop").isHidden());
    await page.unroute("**/api/pipeline/run");
    // Run all three stages via the button; this demo target passes both screens.
    await page.locator("#pipe-targets").fill("贵州茅台");
    await page.locator("#stage-screen").check();
    await page.locator("#stage-checklist").check();
    await page.locator("#btn-pipe-run").click();
    await page.locator("#pipe-status").filter({ hasText: "流水线完成" }).waitFor();
    assert.equal(pipelineRequests, 3);
    assert.equal(await page.locator("#pipe-results tbody tr").count(), 2);
    assert.equal(await page.locator("#pipe-stages .done").count(), 3);
    await screenshot("pipeline-desktop");
    await nav("settings");
    await page.locator("#btn-cfg-cancel").click();
    const providerPresets = await page.locator("#cfg-provider option[data-base-url]").evaluateAll(
      options => Object.fromEntries(options.map(option => [option.value, option.dataset.baseUrl])),
    );
    assert.equal(Object.keys(providerPresets).length, 15);
    assert.deepEqual(providerPresets, {
      deepseek: "https://api.deepseek.com",
      qwen: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      doubao: "https://ark.cn-beijing.volces.com/api/v3",
      zhipu: "https://open.bigmodel.cn/api/paas/v4",
      moonshot: "https://api.moonshot.cn/v1",
      qianfan: "https://qianfan.baidubce.com/v2",
      minimax: "https://api.minimax.cn/v1",
      hunyuan: "https://api.hunyuan.cloud.tencent.com/v1",
      "opencode-go": "https://opencode.ai/zen/go/v1",
      openai: "https://api.openai.com/v1",
      anthropic: "https://api.anthropic.com",
      gemini: "https://generativelanguage.googleapis.com/v1beta/openai",
      xai: "https://api.x.ai/v1",
      mistral: "https://api.mistral.ai/v1",
      openrouter: "https://openrouter.ai/api/v1",
    });
    for (const [provider, baseUrl] of Object.entries(providerPresets)) {
      await page.locator("#cfg-provider").selectOption(provider);
      assert.equal(await page.locator("#cfg-base-url").inputValue(), baseUrl);
    }
    await page.locator("#cfg-provider").selectOption("opencode-go");
    assert.equal(await page.locator("#cfg-max-in").evaluate(el => el.tagName), "SELECT");
    assert.equal(await page.locator("#cfg-max-out").evaluate(el => el.tagName), "SELECT");
    assert.equal(await page.locator("#cfg-max-in").inputValue(), "不限");
    assert.equal(await page.locator("#cfg-max-out").inputValue(), "不限");
    await page.locator("#cfg-name").fill("Token 下拉回归");
    await page.locator("#cfg-max-in").selectOption("1048576");
    await page.locator("#cfg-max-out").selectOption("393216");
    const savedResponse = page.waitForResponse(r => r.url().endsWith("/api/settings") && r.request().method() === "POST");
    await page.locator("#btn-save-settings").click();
    const savedConfig = await (await savedResponse).json();
    assert.equal(savedConfig.provider, "openai");
    assert.equal(savedConfig.provider_preset, "opencode-go");
    assert.equal(savedConfig.base_url, "https://opencode.ai/zen/go/v1");
    assert.equal(savedConfig.max_input_tokens, "1048576");
    assert.equal(savedConfig.max_output_tokens, "393216");
    await page.reload();
    await page.locator("#login-overlay").waitFor({ state: "hidden" });
    await nav("settings");
    await page.locator(".cfg-card").filter({ hasText: "Token 下拉回归" }).locator(".edit").click();
    assert.equal(await page.locator("#cfg-provider").inputValue(), "opencode-go");
    assert.equal(await page.locator("#cfg-max-in").inputValue(), "1048576");
    assert.equal(await page.locator("#cfg-max-out").inputValue(), "393216");
    // 已有配置的数字、k 后缀和非预设值均不可因控件切换而丢失。
    await page.evaluate(() => fillCfgForm({
      provider: "openai", base_url: "https://legacy.example/v1",
      max_input_tokens: 12500, max_output_tokens: "64k",
    }));
    assert.equal(await page.locator("#cfg-provider").inputValue(), "custom-openai");
    assert.equal(await page.locator("#cfg-base-url").inputValue(), "https://legacy.example/v1");
    assert.equal(await page.locator("#cfg-max-in").inputValue(), "12500");
    assert.equal(await page.locator("#cfg-max-out").inputValue(), "64k");
    const updatedResponse = page.waitForResponse(r => r.url().endsWith(`/api/settings/${savedConfig.id}`) && r.request().method() === "PUT");
    await page.locator("#btn-save-settings").click();
    const updatedConfig = await (await updatedResponse).json();
    assert.equal(updatedConfig.provider, "openai");
    assert.equal(updatedConfig.provider_preset, "custom-openai");
    assert.equal(updatedConfig.base_url, "https://legacy.example/v1");
    assert.equal(updatedConfig.max_input_tokens, "12500");
    assert.equal(updatedConfig.max_output_tokens, "64k");
    await page.locator("#btn-cfg-cancel").click();
    assert.equal(await page.locator("#cfg-max-in").inputValue(), "不限");
    assert.equal(await page.locator("#cfg-max-out").inputValue(), "不限");
    assert.equal(await page.locator("[data-saved-value]").count(), 0);
    assert.equal(await page.locator("#cfg-provider").inputValue(), "custom-openai");
    assert.equal(await page.locator("#cfg-base-url").inputValue(), "");
    await screenshot("settings-desktop");
    // Check the form system at desktop, tablet and narrow-phone widths.
    // Geometry catches intrinsic-width select overflow and wrapped action collisions.
    for (const width of [1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({width, height: 1000});
      for (const name of ["research", "pipeline", "memory", "timeline", "history", "reports", "account", "users", "settings"]) {
        await nav(name);
        await page.waitForLoadState("networkidle");
        const problems = await page.locator(".page.active").evaluate(root => {
          const controls = [...root.querySelectorAll('input:not([type="checkbox"]):not([type="radio"]):not([type="file"]),select,textarea')]
            .filter(el => el.getClientRects().length && !el.closest('table'));
          return controls.flatMap(el => {
            const r = el.getBoundingClientRect();
            const label = el.id || el.className;
            const issues = [];
            if (r.height < 43 || r.left < -1 || r.right > innerWidth + 1) issues.push(label + ': size/bounds');
            if (!(el.labels?.length || el.getAttribute('aria-label'))) issues.push(label + ': missing label');
            for (const other of controls) {
              if (el === other) continue;
              const b = other.getBoundingClientRect();
              if (Math.min(r.right,b.right)-Math.max(r.left,b.left)>1 && Math.min(r.bottom,b.bottom)-Math.max(r.top,b.top)>1) issues.push(label + ': overlaps ' + other.id);
            }
            return issues;
          });
        });
        assert.deepEqual(problems, [], `form layout ${name} at ${width}px`);
        await noOverflow(`forms ${name} at ${width}px`);
        if ([1440,390].includes(width)) await screenshot(`forms-${name}-${width}`);
      }
      await nav("reports");
      await page.waitForLoadState("networkidle");
      if (width === 1440) {
        const tops = await page.locator('#report-search,#report-group,#report-type,#report-dates-period').evaluateAll(els => els.map(el => el.getBoundingClientRect().top));
        assert.ok(Math.max(...tops)-Math.min(...tops)<1, 'desktop report filters share one baseline');
      }
      await page.locator('#report-dates-period').selectOption('custom');
      await page.locator('#report-dates-from').fill('2026-01-01');
      await page.locator('#report-dates-to').fill('2026-12-31');
      await page.waitForLoadState("networkidle");
      await noOverflow(`custom dates at ${width}px`);
      if ([1440,320].includes(width)) await screenshot(`forms-custom-dates-${width}`);
      await page.locator('#report-dates-period').selectOption('all');
      await nav('settings');
      await page.waitForLoadState("networkidle");
      await page.locator('#btn-add-header').click();
      await page.locator('.header-row').last().locator('.hk').fill('X-Example-Header');
      await noOverflow(`custom header at ${width}px`);
      await page.locator('.header-row').last().locator('.del-header').click();
    }
    await page.setViewportSize({ width: 430, height: 932 });
    for (const name of [
      "research",
      "pipeline",
      "memory",
      "timeline",
      "history",
      "reports",
      "account",
      "users",
      "settings",
    ]) {
      await nav(name);
      await pause(300);
      await noOverflow("mobile " + name);
      if (["research", "account", "users", "settings"].includes(name))
        await screenshot(name + "-mobile");
    }
    await page.evaluate(() => scrollTo(0, document.body.scrollHeight));
    const menuRect = await page.locator("#menu-toggle").boundingBox();
    assert.ok(menuRect.y >= 0 && menuRect.y < 932, "mobile navigation stays reachable while reading");
    await page.locator("#menu-toggle").click();
    await page.waitForFunction(() => document.querySelector(".sidebar").getBoundingClientRect().left >= -1);
    await page.screenshot({ path: path.join(output, "navigation-mobile.png"), fullPage: false });
    await page.keyboard.press("Escape");
    await nav("history");
    const historyItem = page.locator("#history-list .list-item").first();
    await historyItem.waitFor({ state: "visible" });
    await historyItem.click();
    await page.locator("#report-modal.open").waitFor();
    await screenshot("reader-mobile");
    await noOverflow("mobile reader");
    await page.locator("#modal-close").click();
    await page.locator("#btn-logout").evaluate((el) => el.click());
    await page.locator("#login-overlay:not(.hidden)").waitFor();
    await page.locator("#login-username").fill("演示研究员");
    await page.locator("#login-password").fill("visual-test-password");
    await page.locator("#btn-login").click();
    await page.locator("#login-overlay").waitFor({ state: "hidden" });
    assert.ok(await page.locator("#nav-users").isHidden());
    const forbidden = await page.evaluate(async () => {
      const res = await fetch("/api/admin/users", {
        headers: {
          Authorization: "Bearer " + localStorage.getItem("vc_token"),
        },
      });
      return res.status;
    });
    assert.equal(forbidden, 403);
    assert.deepEqual(errors, []);
    console.log(
      "Browser regression passed: desktop/mobile, generation, reader, account and admin access.",
    );
  } catch (error) {
    const failedPage = browser.contexts()[0]?.pages()[0];
    const diagnostic = { error: error.stack };
    if (failedPage) {
      diagnostic.page = await failedPage.evaluate(() => ({
        title: document.title,
        loginMessage: document.querySelector("#login-msg")?.textContent,
        loginTitle: document.querySelector("#login-title")?.textContent,
        activePage: document.querySelector(".page.active")?.id,
        outputStatus: document.querySelector("#output-status")?.textContent,
        buttons: [...document.querySelectorAll("#auth-form button")].map(el => ({text: el.textContent, disabled: el.disabled})),
        overflow: [...document.querySelectorAll("body *")].filter(el => {const r=el.getBoundingClientRect();return r.left>=0&&r.right>innerWidth+1;}).slice(0,20).map(el=>({tag:el.tagName,id:el.id,class:el.className,width:el.getBoundingClientRect().width})),
      }));
      await failedPage.screenshot({ path: path.join(output, "failure.png"), fullPage: true }).catch(()=>{});
    }
    fs.writeFileSync(path.join(output, "diagnostic.json"), JSON.stringify(diagnostic,null,2));
    throw error;
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
