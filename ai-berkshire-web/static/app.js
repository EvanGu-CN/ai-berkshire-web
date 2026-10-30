/* AI Berkshire Web — 前端逻辑 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/* ================= Markdown 渲染器（轻量、自包含） ================= */
function renderMarkdown(src) {
  const esc = (s) => s
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

  // 抽出代码块占位，避免被内联规则破坏
  const codeBlocks = [];
  let text = src.replace(/```([\w-]*)\n([\s\S]*?)```/g, (_, lang, code) => {
    codeBlocks.push(`<pre><code>${esc(code.replace(/\n$/, ""))}</code></pre>`);
    return `\x00CB${codeBlocks.length - 1}\x00`;
  });

  const lines = text.split("\n");
  const out = [];
  let inTable = false;
  let inList = null;
  let inQuote = false;

  const closeAll = () => {
    if (inTable) { out.push("</tbody></table>"); inTable = false; }
    if (inList) { out.push(inList === "ul" ? "</ul>" : "</ol>"); inList = null; }
    if (inQuote) { out.push("</blockquote>"); inQuote = false; }
  };

  const inline = (s) => s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => /^(https?:\/\/|mailto:)/i.test(url) ? `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>` : label)
    .replace(/~~([^~]+)~~/g, "<del>$1</del>");

  for (const raw of lines) {
    const line = raw;

    // 代码块占位行
    const cb = line.match(/^\s*\x00CB(\d+)\x00\s*$/);
    if (cb) { closeAll(); out.push(codeBlocks[+cb[1]]); continue; }

    if (!line.trim()) { closeAll(); continue; }

    // 分隔线
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { closeAll(); out.push("<hr>"); continue; }

    // 引用
    if (/^\s*>/.test(line)) {
      if (inList) { out.push(inList === "ul" ? "</ul>" : "</ol>"); inList = null; }
      if (inTable) { out.push("</tbody></table>"); inTable = false; }
      if (!inQuote) { out.push("<blockquote>"); inQuote = true; }
      out.push("<p>" + inline(esc(line.replace(/^\s*>\s?/, ""))) + "</p>");
      continue;
    } else if (inQuote) {
      out.push("</blockquote>"); inQuote = false;
    }

    // 标题
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      closeAll();
      const lv = h[1].length;
      out.push(`<h${lv}>${inline(esc(h[2]))}</h${lv}>`);
      continue;
    }

    // 表格
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const cells = line.trim().slice(1, -1).split("|").map((c) => c.trim());
      if (/^\s*\|[\s:|-]+\|\s*$/.test(line)) {
        continue; // 表头分隔行
      }
      if (inList) { out.push(inList === "ul" ? "</ul>" : "</ol>"); inList = null; }
      if (!inTable) {
        out.push("<table><thead><tr>" +
          cells.map((c) => `<th>${inline(esc(c))}</th>`).join("") +
          "</tr></thead><tbody>");
        inTable = true;
      } else {
        out.push("<tr>" + cells.map((c) => `<td>${inline(esc(c))}</td>`).join("") + "</tr>");
      }
      continue;
    } else if (inTable) {
      out.push("</tbody></table>"); inTable = false;
    }

    // 列表
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      const kind = ul ? "ul" : "ol";
      if (inList && inList !== kind) {
        out.push(inList === "ul" ? "</ul>" : "</ol>"); inList = null;
      }
      if (!inList) { out.push(kind === "ul" ? "<ul>" : "<ol>"); inList = kind; }
      out.push(`<li>${inline(esc((ul || ol)[1]))}</li>`);
      continue;
    } else if (inList) {
      out.push(inList === "ul" ? "</ul>" : "</ol>"); inList = null;
    }

    // 普通段落
    out.push("<p>" + inline(esc(line)) + "</p>");
  }
  closeAll();
  return out.join("\n").replace(/\x00CB(\d+)\x00/g, (_, i) => codeBlocks[+i]);
}

/* ================= 全局状态 ================= */
let selectedSkill = null;
let abortCtrl = null;
let currentOutput = "";
let reportOffset = 0;
const REPORT_PAGE = 30;

/* ================= 认证 ================= */
const TOKEN_KEY = "vc_token";
let currentUser = null;

function authHeaders(extra = {}) {
  const token = localStorage.getItem(TOKEN_KEY) || "";
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extra,
  };
}

function showLogin(msg = "") {
  $("#login-overlay").classList.remove("hidden");
  $(".layout").inert = true;
  $("#login-username").focus();
  $("#login-msg").textContent = msg;
}

function hideLogin() {
  $("#login-overlay").classList.add("hidden");
  $(".layout").inert = false;
}

async function checkAuth() {
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) return false;
  try {
    const res = await fetch("/api/auth/me", {
      headers: authHeaders(),
    });
    if (!res.ok) { if (res.status === 401) localStorage.removeItem(TOKEN_KEY); return false; }
    currentUser = await res.json();
    setCurrentUserUI();
    return true;
  } catch (_) {
    return false;
  }
}

function setCurrentUserUI() {
  const tierBadge = currentUser
    ? `<span class="tier-chip tier-${escapeHtml(currentUser.tier || "free")}">${
        escapeHtml(currentUser.quota?.label || TIER_LABELS[currentUser.tier] || "会员")}</span>`
    : "";
  $("#current-user").innerHTML = currentUser
    ? `${escapeHtml(currentUser.username)} ${tierBadge}`
    : "";
  $("#btn-logout").style.display = currentUser ? "" : "none";
  // 管理员专属入口
  const quota = currentUser?.quota;
  const pill = $("#quota-pill");
  pill.textContent = currentUser ? `${currentUser.is_admin ? "管理员" : (quota?.label || TIER_LABELS[currentUser.tier] || "会员")} · 今日 ${quota?.used ?? 0} / ${currentUser.is_admin || quota?.quota === 0 ? "不限" : quota?.quota ?? "—"}` : "我的账户";
  pill.classList.toggle("warn", !!quota && !quota.ok);
  $$(".root-only").forEach(el => { el.hidden = !currentUser?.is_root_admin; });
  $$(".admin-only").forEach(el => { el.style.display = currentUser?.is_admin ? "" : "none"; });
  const navUsers = $("#nav-users");
  if (navUsers) {
    navUsers.style.display = (currentUser && currentUser.is_admin) ? "" : "none";
  }
}

let registerMode = false, authBusy = false;
let registrationPolicy = {invite_required:false};
async function loadRegistrationPolicy() {
  try { const response = await fetch("/api/auth/policy"); if (response.ok) {registrationPolicy = await response.json(); applyMembershipTiers(registrationPolicy.platform?.membership_tiers);} } catch (_) {}
}
function setAuthMode(register) {
  registerMode = register;
  $("#login-title").textContent = register ? "创建你的研究账户" : "回到你的研究空间";
  $("#btn-login").textContent = register ? "创建账户" : "登录";
  $("#btn-register").textContent = register ? "已有账号？返回登录" : "注册新账号";
  $("#password-confirm-field").hidden = !register;
  $("#invite-field").hidden = !register || (!registrationPolicy.invite_required && !$("#login-invite").value);
  $("#login-password").autocomplete = register ? "new-password" : "current-password";
  $("#login-password").placeholder = register ? "至少 8 位" : "输入密码";
  $("#login-msg").textContent = "";
}
async function doLogin() {
  if (authBusy) return;
  const username = $("#login-username").value.trim(), password = $("#login-password").value;
  const msgEl = $("#login-msg");
  if (!username || !password) {msgEl.textContent = "请填写用户名和密码"; return;}
  if (registerMode && password.length < 8) {msgEl.textContent = "新密码至少 8 位"; return;}
  if (registerMode && password !== $("#login-password-confirm").value) {msgEl.textContent = "两次输入的密码不一致"; return;}
  authBusy = true;
  $("#btn-login").disabled = true; $("#btn-register").disabled = true;
  msgEl.textContent = registerMode ? "正在创建账户…" : "正在登录…";
  try {
    const res = await fetch(registerMode ? "/api/auth/register" : "/api/auth/login", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({username, password, ...(registerMode ? {invite_code:$("#login-invite").value.trim()} : {})})
    });
    await checkedResponse(res);
    const data = await res.json();
    localStorage.setItem(TOKEN_KEY, data.token); currentUser = data.user;
    $("#login-password").value = ""; $("#login-password-confirm").value = "";
    setCurrentUserUI(); hideLogin(); await startApp();
  } catch (err) {msgEl.textContent = err.message || "无法连接服务，请稍后重试";}
  finally {authBusy = false; $("#btn-login").disabled = false; $("#btn-register").disabled = false;}
}
$("#auth-form").addEventListener("submit", e => {e.preventDefault(); doLogin();});
$("#btn-register").addEventListener("click", () => setAuthMode(!registerMode));

$("#btn-logout").addEventListener("click", async () => {
  try {
    await fetch("/api/auth/logout", {
      method: "POST",
      headers: authHeaders(),
    });
  } catch (_) {}
  localStorage.removeItem(TOKEN_KEY);
  currentUser = null;
  location.reload();
});

/* ================= 导航 ================= */
$$(".nav-item").forEach(btn => btn.addEventListener("click", async () => {
  const page = btn.dataset.page;
  if (page === "users" && !currentUser?.is_admin) return;
  if (abortCtrl || pipeAbort || askAbort) { /* Keep streaming views alive while navigating. */ }
  $$(".nav-item").forEach(b => {b.classList.toggle("active", b===btn); b.setAttribute("aria-current", b===btn ? "page" : "false");});
  $$(".page").forEach(p => p.classList.toggle("active", p.id === "page-"+page));
  $("#page-location").textContent = btn.querySelector("span").textContent;
  closeMobileNav();
  if (page !== "timeline") cancelTimelineRequests();
  history.replaceState(null, "", "#"+page);
  try {
    if(page==="account") await loadAccount();
    if(page==="memory") await loadMemory();
    if(page==="users") await loadUsers();
    if(page==="timeline") await loadTimelineCompanies();
    if(page==="history") await loadHistory();
    if(page==="reports") {await loadReports();await loadSyncStatus();}
    if(page==="settings") await loadSettings();
    if(page==="research") {await refreshApiStatus();await loadRecentWork();}
  } catch(err) {toast(err.message,"bad");}
}));

/* ================= API 帮助 ================= */
async function api(path, opts = {}) {
  const res = await fetch(path, {...opts, headers:{"Content-Type":"application/json",...authHeaders(),...(opts.headers||{})}});
  await checkedResponse(res);
  return res.json();
}

/* ================= API 状态栏 ================= */
async function refreshApiStatus() {
  const el = $("#api-status");
  try {
    const data = await api("/api/settings");
    const active = (data.configs || []).find((c) => c.id === data.active_id)
      || (data.configs || [])[0];
    if (!active || active.provider === "demo" || !active.has_api_key) {
      el.textContent = "● 演示模式";
      const notice = $("#setup-notice"); notice.hidden = false;
      notice.innerHTML = '<span>当前使用演示模式，生成内容仅用于体验。连接模型后可开始真实研究。</span><button class="text-button" data-go="settings">设置模型 →</button>';
    } else {
      $("#setup-notice").hidden = true;
      el.textContent = `● ${active.name || active.provider} / ${active.model || "默认模型"}`;
    }
  } catch (_) {
    el.textContent = "● 服务异常";
  }
}

/* ================= 技能目录 ================= */
let skillCategories = [], skillCategory = "";
let skillExamples = {};   // skill_id -> example

async function loadSkills() {
  const data = await api("/api/skills");
  skillCategories = data.categories || [];
  $("#skill-count").textContent = `${data.total || 0} 种方法`;
  const filters = [{category:"全部"}, ...skillCategories];
  $("#skill-filters").innerHTML = filters.map((cat,i) => `<button type="button" class="${i===0?"active":""}" data-category="${escapeHtml(i===0?"":cat.category)}">${escapeHtml(cat.category)}</button>`).join("");
  $("#skill-filters").querySelectorAll("button").forEach(btn => btn.onclick = () => {skillCategory=btn.dataset.category;$("#skill-filters").querySelectorAll("button").forEach(b=>b.classList.toggle("active",b===btn));renderSkillCatalog(skillCategories);});
  renderSkillCatalog(skillCategories);
}

function renderSkillCatalog(categories) {
  const root = $("#skill-catalog");
  root.innerHTML = "";
  const query = $("#skill-search").value.trim().toLowerCase();
  for (const cat of categories) {
    if (skillCategory && cat.category !== skillCategory) continue;
    const priority = ["investment-research", "investment-team", "earnings-review", "quality-screen", "investment-checklist", "portfolio-review"];
    const rank = it => priority.includes(it.id) ? priority.indexOf(it.id) : 100;
    const items = cat.items.filter(it => !query || [it.title,it.purpose,it.scenario,it.id].join(" ").toLowerCase().includes(query)).sort((a,b) => rank(a)-rank(b));
    if (!items.length) continue;
    const title = document.createElement("div");
    title.className = "skill-group-title";
    title.textContent = cat.category;
    root.appendChild(title);
    for (const it of items) {
      if (it.example) skillExamples[it.id] = it.example;
      const div = document.createElement("button");
      div.type = "button";
      div.setAttribute("aria-pressed", String(selectedSkill === it.id));
      div.className = "skill-item" + (selectedSkill === it.id ? " selected" : "");
      div.dataset.id = it.id;
      div.innerHTML = `<div class="st">${escapeHtml(it.purpose || it.title)}</div>
        <div class="sp">${escapeHtml(it.scenario || "")}</div>`;
      div.addEventListener("click", () => selectSkill(it));
      root.appendChild(div);
    }
  }
  if (!root.children.length) root.innerHTML = makeEmpty(skillCategories.length ? "没有匹配的方法" : "研究框架尚未就绪", skillCategories.length ? "试试其他关键词，或切换到全部分类。" : "请联系管理员在报告库同步研究框架。");
}
$("#skill-search").addEventListener("input", () => renderSkillCatalog(skillCategories));

function selectSkill(it) {
  selectedSkill = it.id;
  if (!abortCtrl) {$("#btn-run").disabled = false;$("#btn-run").textContent = "开始研究";}
  $$(".skill-item").forEach(el => el.setAttribute("aria-pressed", String(el.dataset.id === it.id)));
  $$(".skill-item").forEach((el) =>
    el.classList.toggle("selected", el.dataset.id === it.id));

  // 右侧：已选技能卡片
  const panel = $("#selected-skill");
  panel.innerHTML = `
    <div class="ss-tag">${escapeHtml(it.category)}</div>
    <div class="ss-name">${escapeHtml(it.purpose || it.title)}</div>
    <div class="ss-sp">${escapeHtml(it.scenario || "")}</div>`;

  // 参数输入框：选中后才启用；无示例时不显示任何提示
  const input = $("#research-args");
  input.disabled = false;
  input.placeholder = "输入公司、行业或你想研究的问题";

  // 示例行：点击填入输入框
  const row = $("#example-row");
  const code = $("#example-text");
  if (it.example) {
    row.style.display = "flex";
    code.textContent = it.example;
  } else {
    row.style.display = "none";
  }
  if (matchMedia("(max-width: 800px)").matches) $(".run-panel").scrollIntoView({behavior:"smooth",block:"start"});
  input.focus({preventScroll:true});
  loadSkillParams(it.id);
}

/* ---- 动态参数面板 ---- */
async function loadSkillParams(skillId) {
  const box = $("#skill-params");
  box.innerHTML = "";
  try {
    const data = await api(`/api/skills/${skillId}/params`);
    if (selectedSkill !== skillId) return;   // 用户已切走
    for (const p of data.params || []) {
      const field = document.createElement("div");
      field.className = "field";
      if (p.type === "select") {
        const opts = (p.options || []).map((o) =>
          `<option value="${escapeHtml(o.value)}" ${o.value == p.default ? "selected" : ""}>${escapeHtml(o.label)}</option>`
        ).join("");
        field.innerHTML = `<label for="skill-param-${escapeHtml(p.id)}">${escapeHtml(p.label)}</label>
          <select id="skill-param-${escapeHtml(p.id)}" data-param="${escapeHtml(p.id)}">${opts}</select>`;
      } else {
        field.innerHTML = `<label for="skill-param-${escapeHtml(p.id)}">${escapeHtml(p.label)}</label>
          <input type="number" id="skill-param-${escapeHtml(p.id)}" data-param="${escapeHtml(p.id)}"
                 value="${p.default}" min="${p.min ?? 1}" max="${p.max ?? 100}">`;
      }
      box.appendChild(field);
    }
  } catch (_) { /* 技能无参数接口时静默 */ }
}

function collectSkillParams() {
  const params = {};
  document.querySelectorAll("#skill-params [data-param]").forEach((el) => {
    let v = el.value;
    if (el.type === "number") {
      const n = +v;
      const min = +el.min || 1, max = +el.max || 100;
      v = Math.max(min, Math.min(max, isNaN(n) ? min : n));
    }
    params[el.dataset.param] = v;
  });
  return params;
}

// 示例：点击填入输入框
$("#example-text").addEventListener("click", () => {
  const input = $("#research-args");
  input.value = $("#example-text").textContent;
  input.focus();
  checkDupReports();   // 填入后顺便查重
});

/* ================= 已有研究参考（不拦截新研究） ================= */
let dupTimer = null, dupSequence = 0, dupController = null;
const dupState = {period: "6m", keyword: "", offset: 0};
const DUP_PAGE = 3;
function invalidateDupReports() {
  ++dupSequence;
  dupController?.abort();
  clearTimeout(dupTimer);
}
async function checkDupReports() {
  invalidateDupReports();
  const sequence = dupSequence;
  const keyword = $("#research-args").value.trim();
  const box = $("#dedup-hint");
  if (keyword.length < 2) { box.style.display = "none"; return; }
  if (keyword !== dupState.keyword) { dupState.keyword = keyword; dupState.offset = 0; }
  box.style.display = "block";
  box.setAttribute("aria-busy", "true");
  $("#dup-summary").textContent = "正在查找已有报告…";
  $("#dup-list").replaceChildren();
  $("#dup-pager").hidden = true;
  $("#dup-retry").hidden = true;
  const controller = new AbortController();
  dupController = controller;
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const params = new URLSearchParams({keyword, period: dupState.period, offset: String(dupState.offset), limit: String(DUP_PAGE)});
    const d = await api(`/api/reports/recent?${params}`, {signal: controller.signal});
    if (sequence !== dupSequence || keyword !== $("#research-args").value.trim()) return;
    $("#dup-summary").textContent = d.total
      ? `${d.period_label}找到 ${d.total} 份相关报告 · ${d.since} 至 ${d.until}`
      : `${d.period_label}未找到相关报告，可扩大时间范围或直接开始研究。`;
    const sources = {official: ["official", "官方"], generated: ["gen", "共享"], imported: ["imported", "导入"]};
    $("#dup-list").innerHTML = (d.items || []).map(it => {
      const [badge, label] = sources[it.source] || ["gen", "共享"];
      return `<button type="button" class="dup-row" data-path="${escapeHtml(it.path)}"><span class="src-badge src-${badge}">${label}</span><span class="dup-title">${escapeHtml(it.title)}</span><time class="dup-meta" datetime="${escapeHtml(it.date)}">${escapeHtml(it.date)}</time></button>`;
    }).join("");
    $("#dup-pager").hidden = d.total <= DUP_PAGE;
    $("#dup-prev").disabled = d.offset === 0;
    $("#dup-next").disabled = !d.has_next;
    renderPageNumbers("dup-page", d.offset, DUP_PAGE, d.total, page=>{dupState.offset=(page-1)*DUP_PAGE;checkDupReports();});
  } catch (err) {
    if (sequence !== dupSequence) return;
    $("#dup-summary").textContent = err.name === "AbortError"
      ? "已有报告查询超时，可重试或直接开始研究。"
      : "暂时无法查询已有报告，可重试或直接开始研究。";
    $("#dup-retry").hidden = false;
  } finally {
    clearTimeout(timeout);
    if (sequence === dupSequence) { dupController = null; box.setAttribute("aria-busy", "false"); }
  }
}
$("#dedup-hint").addEventListener("click", event => {
  const report = event.target.closest(".dup-row");
  if (report) openReport(report.dataset.path);
  const range = event.target.closest("[data-period]");
  if (range) {
    dupState.period = range.dataset.period;
    dupState.offset = 0;
    $$(".dup-ranges button").forEach(button => button.setAttribute("aria-pressed", String(button === range)));
    checkDupReports();
  }
});
$("#dup-prev").onclick = () => { dupState.offset = Math.max(0, dupState.offset - DUP_PAGE); checkDupReports(); };
$("#dup-next").onclick = () => { dupState.offset += DUP_PAGE; checkDupReports(); };
$("#dup-retry").onclick = () => checkDupReports();
$("#dup-library").onclick = () => {
  $("#report-search").value = $("#research-args").value.trim();
  $("#report-group").value = "";
  $("#report-type").value = "";
  reportSource = "all"; reportScope = "all"; reportOffset = 0;
  reportDateControls.get("report-dates")?.reset();
  $$("#report-source-chips .chip").forEach(chip => chip.classList.toggle("active", chip.dataset.source === "all"));
  goToPage("reports");
};
$("#research-args").addEventListener("input", () => {
  invalidateDupReports();
  $("#dedup-hint").style.display = "none";
  dupTimer = setTimeout(checkDupReports, 600);
});

/* ================= 新建研究 ================= */
$("#btn-run").addEventListener("click", startResearch);
$("#research-args").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !btnRunDisabled()) startResearch();
});
function btnRunDisabled() {
  return !selectedSkill || $("#btn-run").disabled || $("#btn-run").style.display === "none";   // 研究进行中
}
$("#btn-stop").addEventListener("click", () => {
  if (abortCtrl) abortCtrl.abort();
});
$("#btn-copy").addEventListener("click", () => {
  if (!currentOutput) { toast("没有可复制的内容", "bad"); return; }
  navigator.clipboard.writeText(currentOutput).then(
    () => toast("已复制到剪贴板", "ok"),
    () => toast("复制失败，请手动选择文本", "bad"));
});
$("#btn-save-md").addEventListener("click", () => {
  if (!currentOutput) { toast("没有可下载的内容", "bad"); return; }
  downloadMarkdown(currentOutput, $("#output-title").textContent);
  toast("已下载 Markdown", "ok");
});

async function startResearch() {
  if (abortCtrl) return;
  if (!selectedSkill) {
    const panel = $(".skill-panel");
    panel.style.borderColor = "var(--accent)";
    setTimeout(() => { panel.style.borderColor = ""; }, 800);
    return;
  }
  const args = $("#research-args").value.trim();
  const mode = document.querySelector('input[name="run-mode"]:checked')?.value || "auto";
  const market = $("#run-market").value || "all";
  const card = $("#research-output-card");
  const out = $("#research-output");
  const status = $("#output-status");
  const btnRun = $("#btn-run");
  const btnStop = $("#btn-stop");
  const teamTrack = $("#team-track");

  card.style.display = "block";
  card.scrollIntoView({ behavior: "smooth", block: "start" });  // 自动滚到输出区
  out.innerHTML = '<p class="hint">正在生成…</p>';
  out.classList.add("cursor");
  status.textContent = "研究中…推理型模型（如 MiniMax）可能需要 3-10 分钟，出字前请耐心等待";
  btnRun.style.display = "none";
  btnStop.style.display = "";
  currentOutput = "";
  teamTrack.style.display = "none";
  teamTrack.innerHTML = "";
  const skillName = $(".skill-item.selected .st")?.textContent || selectedSkill;
  const marketLabel = $("#run-market").selectedOptions[0]?.textContent || "";
  const skillParams = collectSkillParams();
  const HORIZON_LABEL = { "3y": "3年", "5y": "5年", "10y": "10年" };
  $("#output-title").textContent = `研究报告 · ${skillName}`;
  $("#output-meta").textContent = [
    args ? `参数：${args}` : "",
    market !== "all" ? `市场：${marketLabel}` : "",
    skillParams.final_count ? `终选 ${skillParams.final_count} 家` : "",
    skillParams.top_n ? `排名榜前 ${skillParams.top_n}` : "",
    skillParams.horizon ? `估值年限：${HORIZON_LABEL[skillParams.horizon] || skillParams.horizon}` : "",
  ].filter(Boolean).join(" · ");

  // 耗时器
  const t0 = Date.now();
  const timer = setInterval(() => {
    const s = Math.round((Date.now() - t0) / 1000);
    if (status.textContent.includes("生成中") || status.textContent.includes("研究中")) {
      const m = Math.floor(s / 60);
      status.innerHTML = `<span class="ok">生成中…</span> 已用 ${m ? m + "分" : ""}${s % 60}秒`;
    }
  }, 1000);

  abortCtrl = new AbortController();
  try {
    const res = await fetch("/api/research/run", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ skill_id: selectedSkill, arguments: args, mode, market, params: skillParams }),
      signal: abortCtrl.signal,
    });
    await checkedResponse(res);
    await consumeResearchStream(res, out, status, teamTrack);
    out.classList.remove("cursor");
    status.innerHTML = `<span class="ok">✔ 研究完成</span> · 共 ${currentOutput.length} 字 · 已自动保存到研究历史`;
  } catch (err) {
    out.classList.remove("cursor");
    if (err.name === "AbortError") {
      status.innerHTML = `<span class="bad">已停止</span> · 已有内容将保存到研究历史`;
    } else {
      const message = err.name === "TypeError"
        ? "无法连接研究服务，请检查网络，并到「研究历史」确认是否已有结果。"
        : err.message;
      status.innerHTML = `<span class="bad">出错：${escapeHtml(message)}</span>`;
      out.innerHTML = renderMarkdown(currentOutput || `**生成未完成**：${escapeHtml(message)}`);
    }
  } finally {
    clearInterval(timer);
    btnRun.style.display = "";
    btnStop.style.display = "none";
    abortCtrl = null;
    await refreshAccount();
    await loadRecentWork();
  }
}

async function consumeResearchStream(res, out, status, teamTrack) {
  let text = "", sections = [], complete = false;
  const chips = {};
  await readEvents(res, ev => {
    if (ev.type === "role_start") {
      teamTrack.style.display = "flex";
      const chip = document.createElement("div"); chip.className = "team-chip running";
      chip.textContent = ev.label; teamTrack.appendChild(chip); chips[ev.role_id] = chip;
    } else if (ev.type === "role_error") {
      if (chips[ev.role_id]) {chips[ev.role_id].dataset.failed="true";chips[ev.role_id].className="team-chip bad";chips[ev.role_id].textContent=ev.label+" · 未完成";}
    } else if (ev.type === "role_done") {
      if (chips[ev.role_id] && !chips[ev.role_id].dataset.failed) {chips[ev.role_id].className="team-chip done";}
      sections.push({label:ev.label,text:ev.text});
      currentOutput = renderTeamProgress(sections,text);
      out.innerHTML = renderMarkdown(currentOutput);
    } else if (ev.type === "delta") {
      text += ev.text; currentOutput = sections.length ? renderTeamProgress(sections,text) : text;
      out.innerHTML = renderMarkdown(currentOutput);
      status.textContent = `生成中 · ${text.length} 字`;
    } else if (ev.type === "done") {
      complete = true; currentOutput = ev.full_text || text;
      out.innerHTML = renderMarkdown(currentOutput);
    } else if (ev.type === "synthesis_start") {status.textContent="正在整合各视角的结论…";}
  });
  if (!complete) throw new Error("报告传输中断，请在研究历史中查看已有内容");
}

function renderTeamProgress(sections, synth) {
  const parts = sections.map((s) =>
    `## ${s.label}\n\n${s.text}\n`).join("\n");
  if (synth) {
    return `${synth}\n\n---\n\n# 附：各角色原始产出\n\n${parts}`;
  }
  return `# 多Agent 研究进行中\n\n> 各角色并行研究完成后，Team Lead 将综合为完整报告。\n\n${parts}`;
}

/* ================= 研究流水线 ================= */
let pipeAbort = null;

$("#btn-pipe-run").addEventListener("click", runPipeline);
$("#pipe-targets").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.isComposing) {
    e.preventDefault();
    runPipeline();
  }
});
$("#btn-pipe-stop").addEventListener("click", () => { if (pipeAbort) pipeAbort.abort(); });

async function runPipeline() {
  if (pipeAbort) return;
  const status = $("#pipe-status");
  const targets = $("#pipe-targets").value.trim();
  const targetCount = targets.split(/[,，、\n]+/).filter(t => t.trim()).length;
  if (!targetCount || targetCount > 30) {
    status.textContent = "请输入 1–30 个候选标的，用逗号分隔。";
    $("#pipe-targets").focus();
    return;
  }
  const stages = [];
  if ($("#stage-screen").checked) stages.push("screen");
  if ($("#stage-checklist").checked) stages.push("checklist");
  if ($("#stage-deep").checked) stages.push("deep");
  if (!stages.length) {
    status.textContent = "请至少选择一个流水线阶段。";
    $("#stage-screen").focus();
    return;
  }
  const deepCount = Number($("#pipe-deep-count").value);
  if (stages.includes("deep") && (!Number.isInteger(deepCount) || deepCount < 1 || deepCount > 5)) {
    status.textContent = "深度研究家数请输入 1–5 的整数。";
    $("#pipe-deep-count").focus();
    return;
  }

  const btnRun = $("#btn-pipe-run");
  const btnStop = $("#btn-pipe-stop");
  btnRun.disabled = true;              // 不隐藏，避免布局跳动
  btnRun.textContent = "运行中…";
  status.textContent = "正在提交流水线，请稍候…";
  btnStop.style.display = "";
  $("#pipe-progress-card").style.display = "block";
  $("#pipe-progress-card").scrollIntoView({ behavior: "smooth", block: "start" });
  $("#pipe-results-card").style.display = "none";
  $("#pipe-deep-card").style.display = "none";
  $("#pipe-stages").innerHTML = "";
  $("#pipe-results").innerHTML = "";
  pipeAbort = new AbortController();

  const STAGE_META = {
    screen: "去劣筛选", checklist: "买入前 Checklist", deep: "深度研究",
  };
  // 预渲染全部选中阶段（pending = 待执行），全程可见全局进度
  const stageEls = {};
  stages.forEach((sid, i) => {
    const div = document.createElement("div");
    div.className = "pipe-stage pending";
    div.innerHTML = `<div class="ps-dot">${i + 1}</div>
      <div class="ps-label">${STAGE_META[sid] || sid}</div>
      <div class="ps-meta">待执行</div>`;
    $("#pipe-stages").appendChild(div);
    stageEls[sid] = div;
  });
  const ensureStage = (sid) => {
    const el = stageEls[sid];
    if (el) { el.classList.remove("pending"); el.classList.add("running"); }
    return el;
  };

  try {
    const res = await fetch("/api/pipeline/run", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        targets,
        stages,
        deep_count: stages.includes("deep") ? deepCount : 1,
      }),
      signal: pipeAbort.signal,
    });
    await checkedResponse(res);
    let complete = false;
    await readEvents(res, ev => {handlePipeEvent(ev, stageEls, ensureStage); if(ev.type==="pipeline_end")complete=true;});
    if (!complete) throw new Error("流水线传输中断，已完成阶段可在研究历史查看");
  } catch (err) {
    if (err.name === "AbortError") {
      status.textContent = "已停止，可修改条件后重新运行。";
      $("#pipe-stages").insertAdjacentHTML("beforeend",
        '<div class="hint">已停止</div>');
    } else {
      status.textContent = `运行失败：${err.message}`;
      $("#pipe-stages").insertAdjacentHTML("beforeend",
        `<div class="hint" style="color:var(--red)">出错：${escapeHtml(err.message)}</div>`);
    }
    Object.values(stageEls).forEach(el => {
      if (el.classList.contains("done")) return;
      const wasRunning = el.classList.contains("running");
      el.classList.remove("running");
      el.classList.add("pending");
      el.querySelector(".ps-meta").textContent = wasRunning ? "已中断" : "未执行";
    });
  } finally {
    btnRun.disabled = false;
    btnRun.textContent = "运行流水线";
    btnStop.style.display = "none";
    pipeAbort = null;
    await refreshAccount();
    await loadRecentWork();
  }
}

function handlePipeEvent(ev, stageEls, ensureStage) {
  const STAGE_META = {
    screen: "① 去劣筛选", checklist: "② 买入前 Checklist", deep: "③ 深度研究",
  };
  if (ev.type === "pipeline_start") {
    $("#pipe-status").textContent = `已启动 · ${ev.companies.length} 个候选标的${ev.demo ? " · 演示模式（未调用真实模型）" : ""}`;
  } else if (ev.type === "stage_start") {
    const el = ensureStage(ev.stage);
    if (el) el.querySelector(".ps-meta").textContent =
      `进行中 · 输入 ${ev.input?.length ?? 0} 个标的 · 等待模型响应…`;
  } else if (ev.type === "stage_progress") {
    const el = stageEls[ev.stage];
    if (el) el.querySelector(".ps-meta").textContent = `模型正在分析 · 已接收 ${ev.chars} 字，完成后显示筛选结果`;
  } else if (ev.type === "stage_retry") {
    const el = stageEls[ev.stage];
    if (el) el.querySelector(".ps-meta").textContent = "结果格式未通过校验 · 正在自动重试一次…";
  } else if (ev.type === "stage_item") {
    $("#pipe-results-card").style.display = "block";
    const key = "stage-block-" + ev.stage;
    let block = document.getElementById(key);
    if (!block) {
      block = document.createElement("div");
      block.id = key;
      block.className = "pipe-block";
      block.innerHTML = `<h3>${STAGE_META[ev.stage] || ev.stage}</h3>
        <table class="pipe-table"><thead><tr>
          <th>标的</th><th>判定</th><th>理由</th></tr></thead><tbody></tbody></table>`;
      $("#pipe-results").appendChild(block);
    }
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><b>${escapeHtml(ev.company)}</b></td>
      <td><span class="pass-chip ${ev.pass ? "pass-yes" : "pass-no"}">
        ${ev.pass ? "通过" : "淘汰"}</span></td>
      <td>${escapeHtml(ev.reason || "")}</td>`;
    block.querySelector("tbody").appendChild(tr);
  } else if (ev.type === "stage_end") {
    const el = stageEls[ev.stage];
    if (el) {
      el.classList.remove("running");
      el.classList.add("done");
      el.querySelector(".ps-meta").textContent = `✔ 通过 ${ev.survivors.length} 个`;
    }
  } else if (ev.type === "deep_start") {
    const el = ensureStage("deep");
    if (el) el.querySelector(".ps-meta").textContent =
      `进行中 · ${ev.company}（${ev.index}/${ev.total}）`;
    $("#pipe-deep-card").style.display = "block";
    $("#pipe-deep-title").textContent = `深度研究 · ${ev.company}`;
    $("#pipe-deep-output").dataset.acc = "";
    $("#pipe-deep-output").innerHTML = '<p class="hint">生成中…</p>';
    $("#pipe-deep-status").textContent = "";
  } else if (ev.type === "deep_delta") {
    const out = $("#pipe-deep-output");
    out.innerHTML = renderMarkdown(
      (out.dataset.acc || "") + ev.text);
    out.dataset.acc = (out.dataset.acc || "") + ev.text;
    $("#pipe-deep-status").innerHTML =
      `<span class="ok">生成中…</span> ${(out.dataset.acc || "").length} 字`;
  } else if (ev.type === "deep_end") {
    $("#pipe-deep-status").innerHTML =
      `<span class="ok">✔ ${escapeHtml(ev.company)} 完成</span> · ${ev.chars} 字 · 已存入研究历史`;
    $("#pipe-deep-output").dataset.acc = "";
  } else if (ev.type === "pipeline_end") {
    $("#pipe-status").textContent = "流水线完成" + (ev.message ? ` · ${ev.message}` : "，结果已保存到研究历史。");
    Object.values(stageEls).forEach(stage => {
      if (stage.classList.contains("pending")) stage.querySelector(".ps-meta").textContent = "已跳过（无入围标的）";
    });
    const el = stageEls["deep"];
    if (el && el.classList.contains("running")) {
      el.classList.remove("running");
      el.classList.add("done");
      el.querySelector(".ps-meta").textContent = "✔ 完成";
    }
    if (ev.message) {
      $("#pipe-stages").insertAdjacentHTML("beforeend",
        `<div class="hint">${escapeHtml(ev.message)}</div>`);
    } else {
      $("#pipe-stages").insertAdjacentHTML("beforeend",
        `<div class="hint" style="color:var(--green)">✔ 流水线完成 · 最终入围：${ev.survivors.map(escapeHtml).join("、")}</div>`);
    }
  } else if (ev.type === "error") {
    $("#pipe-stages").insertAdjacentHTML("beforeend",
      `<div class="hint" style="color:var(--red)">${escapeHtml(ev.message)}</div>`);
  }
}

/* ================= 观点时间线 ================= */
let timelineCompaniesSequence = 0;
let timelineRequestSequence = 0;
let timelineCompaniesController = null;
let timelineController = null;
let timelineCompanyOffset = 0;
let timelineCompanyTotal = 0;
let timelineOffset = 0;
let timelineTotal = 0;
const TIMELINE_PAGE_SIZE = 20;
const TIMELINE_COMPANY_PAGE_SIZE = 30;

function cancelTimelineRequests() {
  ++timelineCompaniesSequence; ++timelineRequestSequence;
  timelineCompaniesController?.abort(); timelineController?.abort();
}

async function timelineApi(path, controller, timeoutMs) {
  let timedOut = false;
  const timer = setTimeout(() => {timedOut = true; controller.abort();}, timeoutMs);
  try { return await api(path, {signal:controller.signal}); }
  catch (error) { if (timedOut) throw Error("请求超时，请重试"); throw error; }
  finally { clearTimeout(timer); }
}

function hideTimeline() {
  for (const id of ["tl-stats-card", "tl-chart-card", "tl-list-card"]) $("#" + id).style.display = "none";
}

async function loadTimelineCompanies() {
  const sel = $("#tl-company");
  const previous = sel.value;
  const sequence = ++timelineCompaniesSequence;
  ++timelineRequestSequence;
  timelineCompaniesController?.abort(); timelineController?.abort();
  const controller = timelineCompaniesController = new AbortController();
  sel.disabled = true;
  if (sel.options.length <= 1) sel.replaceChildren(new Option("正在加载公司 / 专题…", ""));
  $("#btn-tl-load").disabled = true;
  $("#tl-company-prev").disabled = true; $("#tl-company-next").disabled = true;
  $("#tl-status").textContent = "正在读取报告库中的公司与专题…";
  hideTimeline();
  try {
    const params = new URLSearchParams({q:$("#tl-company-search").value.trim(), limit:String(TIMELINE_COMPANY_PAGE_SIZE), offset:String(timelineCompanyOffset), ...reportDateParams("timeline-dates")});
    const data = await timelineApi(`/api/timeline/companies?${params}`, controller, 8000);
    if (sequence !== timelineCompaniesSequence) return;
    timelineCompanyTotal = data.total ?? data.items.length;
    if (timelineCompanyTotal && !data.items.length && timelineCompanyOffset) {timelineCompanyOffset = 0; await loadTimelineCompanies(); return;}
    sel.replaceChildren(new Option(data.items.length ? "请选择公司 / 专题" : $("#tl-company-search").value.trim() ? "暂无匹配的公司 / 专题" : "暂无可用报告", ""));
    const options = document.createDocumentFragment();
    for (const it of data.items) {
      const o = document.createElement("option");
      o.value = it.grp;
      o.textContent = `${it.grp}（${it.n} 份${it.score_cnt ? " · 有评分" : ""}）`;
      options.appendChild(o);
    }
    sel.appendChild(options);
    sel.disabled = !data.items.length;
    if (data.items.some(it => it.grp === previous)) sel.value = previous;
    renderPageNumbers("tl-company-page-info",timelineCompanyOffset,TIMELINE_COMPANY_PAGE_SIZE,timelineCompanyTotal,page=>{timelineCompanyOffset=(page-1)*TIMELINE_COMPANY_PAGE_SIZE;loadTimelineCompanies();});
    $("#tl-status").textContent = data.items.length
      ? `共 ${timelineCompanyTotal} 个匹配公司 / 专题。可输入名称快速查找，选择后每页查看 ${TIMELINE_PAGE_SIZE} 份报告。`
      : "所选条件下暂无公司或专题，可扩大报告日期范围、清空搜索或导入报告后刷新。";
  } catch (err) {
    if (sequence !== timelineCompaniesSequence) return;
    sel.replaceChildren(new Option("加载失败，请刷新重试", ""));
    $("#tl-status").textContent = "公司列表加载失败：" + err.message + "。可点击「刷新公司列表」重试。";
  } finally {
    if (sequence === timelineCompaniesSequence) {
      $("#btn-tl-load").disabled = sel.disabled || !sel.value;
      $("#tl-company-prev").disabled = sel.disabled || timelineCompanyOffset <= 0;
      $("#tl-company-next").disabled = sel.disabled || timelineCompanyOffset + TIMELINE_COMPANY_PAGE_SIZE >= timelineCompanyTotal;
    }
  }
}

$("#btn-tl-refresh").addEventListener("click", loadTimelineCompanies);
$("#tl-company-search").addEventListener("input", debounce(() => {timelineCompanyOffset = 0; loadTimelineCompanies();}, 250));
$("#tl-company-prev").addEventListener("click", () => {timelineCompanyOffset = Math.max(0, timelineCompanyOffset - TIMELINE_COMPANY_PAGE_SIZE); loadTimelineCompanies();});
$("#tl-company-next").addEventListener("click", () => {timelineCompanyOffset += TIMELINE_COMPANY_PAGE_SIZE; loadTimelineCompanies();});
$("#btn-tl-load").addEventListener("click", () => {timelineOffset = 0; loadTimeline();});
$("#tl-company").addEventListener("change", () => {timelineOffset = 0; loadTimeline();});
$("#tl-prev").addEventListener("click", () => {timelineOffset = Math.max(0, timelineOffset - TIMELINE_PAGE_SIZE); loadTimeline();});
$("#tl-next").addEventListener("click", () => {timelineOffset += TIMELINE_PAGE_SIZE; loadTimeline();});

async function loadTimeline() {
  const sequence = ++timelineRequestSequence;
  timelineController?.abort();
  const controller = timelineController = new AbortController();
  const grp = $("#tl-company").value;
  hideTimeline();
  $("#btn-tl-load").disabled = !grp;
  if (!grp) return;
  $("#btn-tl-load").disabled = true;
  $("#tl-prev").disabled = true; $("#tl-next").disabled = true;
  $("#tl-status").textContent = `正在整理「${grp}」的历次报告…`;
  try {
    const params = new URLSearchParams({grp, limit:String(TIMELINE_PAGE_SIZE), offset:String(timelineOffset), ...reportDateParams("timeline-dates")});
    const data = await timelineApi(`/api/timeline/company?${params}`, controller, 12000);
    if (sequence !== timelineRequestSequence) return;
    if (data.total && !data.items.length && timelineOffset) {timelineOffset = 0; await loadTimeline(); return;}
    renderTimeline(data);
    timelineTotal = data.total ?? data.items.length;
    const start = data.items.length ? timelineOffset + 1 : 0;
    $("#tl-status").textContent = `「${grp}」共 ${timelineTotal} 份报告，当前显示 ${start}–${timelineOffset + data.items.length} 份。仅整理本页，不调用模型。` + (data.conclusion_index_busy ? " 深度抽取暂不可用，当前按规则展示，可稍后重试。" : "");
  } catch (err) {
    if (sequence !== timelineRequestSequence) return;
    $("#tl-status").textContent = "时间线加载失败：" + err.message + "。可重试或刷新公司列表。";
  } finally {
    if (sequence === timelineRequestSequence) $("#btn-tl-load").disabled = !$("#tl-company").value;
  }
}

function renderTimeline(data) {
  const s = data.stats;
  const total = data.total ?? data.items.length;
  renderPageNumbers("tl-page-info",timelineOffset,TIMELINE_PAGE_SIZE,total,page=>{timelineOffset=(page-1)*TIMELINE_PAGE_SIZE;loadTimeline();});
  $("#tl-prev").disabled = timelineOffset <= 0;
  $("#tl-next").disabled = timelineOffset + TIMELINE_PAGE_SIZE >= total;
  const scoreText = (value, maximum) => value == null ? "—" : `${value}${maximum ? "/" + maximum : ""}`;
  $("#tl-stats-card").style.display = "block";
  $("#tl-chart-card").style.display = "block";
  $("#tl-list-card").style.display = "block";

  // 复盘摘要
  $("#tl-stats").innerHTML = `
    <div class="mem-stat"><b>${total}</b><span>报告总数</span></div>
    <div class="mem-stat"><b>${s.report_count}</b><span>本页报告</span></div>
    <div class="mem-stat"><b>${s.score_count}</b><span>本页评分</span></div>
    <div class="mem-stat"><b>${s.drift_count}</b><span>本页结论变化</span></div>
    <div class="mem-stat"><b>${escapeHtml(scoreText(s.score_first, s.score_first_max))} → ${escapeHtml(scoreText(s.score_latest, s.score_latest_max))}</b><span>本页有日期评分首末</span></div>
    <div class="mem-stat mem-stat-wide"><span>
      本页 ${escapeHtml(s.first_date || "日期未知")} ~ ${escapeHtml(s.last_date || "日期未知")} · 本页最新结论：${escapeHtml(s.verdict_latest || "—")}${s.undated_count ? ` · ${s.undated_count} 份日期未知，单独展示` : ""}
    </span></div>`;

  // 评分走势 SVG
  const scores = data.scores || [];
  const chartBox = $("#tl-chart");
  if ((s.score_scales || []).length > 1) {
    chartBox.innerHTML = '<p class="hint">报告评分的满分口径不同，请查看各报告的原始评分，暂不合并绘制走势。</p>';
  } else if (scores.length >= 2) {
    const W = 720, H = 180, PAD = 28;
    const xs = scores.map((_, i) =>
      PAD + (W - PAD * 2) * i / (scores.length - 1));
    const vals = scores.map(([, v]) => v);
    const mn = Math.min(...vals), mx = Math.max(...vals);
    const span = (mx - mn) || 1;
    const ys = vals.map(v =>
      H - PAD - (H - PAD * 2) * (v - mn) / span);
    const pts = xs.map((x, i) => `${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
    const dots = xs.map((x, i) =>
      `<circle cx="${x.toFixed(1)}" cy="${ys[i].toFixed(1)}" r="3.5" fill="#1d4ed8">
        <title>${scores[i][0]}：${vals[i]}</title></circle>`).join("");
    const labels = xs.map((x, i) => {
      if (scores.length > 8 && i % Math.ceil(scores.length / 8)) return "";
      return `<text x="${x.toFixed(1)}" y="${H - 8}" font-size="9"
        fill="#5b6472" text-anchor="middle">${escapeHtml(scores[i][0].slice(2))}</text>`;
    }).join("");
    chartBox.innerHTML = `<svg viewBox="0 0 ${W} ${H}">
      <line x1="${PAD}" y1="${H - PAD}" x2="${W - PAD}" y2="${H - PAD}" stroke="#e5e8ee"/>
      <line x1="${PAD}" y1="${PAD}" x2="${PAD}" y2="${H - PAD}" stroke="#e5e8ee"/>
      <text x="4" y="${PAD + 4}" font-size="10" fill="#5b6472">${mx}</text>
      <text x="4" y="${H - PAD}" font-size="10" fill="#5b6472">${mn}</text>
      <polyline points="${pts}" fill="none" stroke="#1d4ed8" stroke-width="2"/>
      ${dots}${labels}
    </svg>`;
  } else {
    chartBox.innerHTML = '<p class="hint">评分数据不足（需至少 2 次）</p>';
  }

  // 时间轴列表（倒序：最新在上）
  const list = $("#tl-list");
  list.innerHTML = "";
  const driftByPath = {};
  for (const d of data.drifts || []) {
    (driftByPath[d.path] = driftByPath[d.path] || []).push(d);
  }
  const items = [...data.items].reverse();
  for (const it of items) {
    const div = document.createElement("div");
    div.className = "tl-item";
    const drifts = driftByPath[it.path] || [];
    const driftHtml = drifts.map(d =>
      `<div class="tl-drift">⚠ 结论漂移：${escapeHtml(d.from)} → ${escapeHtml(d.to)}</div>`).join("");
    div.innerHTML = `
      <div class="tl-date">${escapeHtml(it.date || "日期未知")}</div>
      <div class="tl-title" role="button" tabindex="0">${escapeHtml(it.title)}</div>
      <div class="tl-meta">
        ${it.verdict ? `<span class="verdict-chip ${
          it.verdict.includes("不通过") ? "verdict-fail" :
          it.verdict.includes("灰") ? "verdict-grey" : "verdict-pass"}">
          ${escapeHtml(it.verdict)}</span>` : ""}
        ${it.score != null ? `<span>评分 ${it.score}${it.score_max ? "/" + it.score_max : ""}</span>` : ""}
        ${it.price_range ? `<span>${escapeHtml(it.price_range)}</span>` : ""}
        <span>${escapeHtml({official:"官方研报",generated:"站内生成",imported:"本地导入"}[it.report_source] || "报告")}</span>
        <span>${it.source === "llm" ? "深度抽取" : it.source === "unavailable" ? "原文暂不可用" : it.source === "none" ? "暂无结构化结论" : `规则整理${it.limited_text ? "（摘要区段）" : ""}`}</span>
      </div>
      ${driftHtml}
      ${it.summary ? `<div class="tl-sum">${escapeHtml(it.summary)}</div>` : ""}`;
    if (it.source !== "unavailable") {
      div.querySelector(".tl-title").addEventListener("click", () => openReport(it.path));
      div.querySelector(".tl-title").addEventListener("keydown", e => {if (e.key === "Enter" || e.key === " ") {e.preventDefault(); openReport(it.path);}});
    } else {div.querySelector(".tl-title").setAttribute("aria-disabled", "true"); div.querySelector(".tl-title").tabIndex = -1;}
    list.appendChild(div);
  }
}

/* ================= 研究记忆 ================= */
let conclOffset = 0;
const CONCL_PAGE = 20;
let askAbort = null;
let conclusionSequence = 0;

async function loadMemory() {
  loadMemoryStatus();
  loadConclusions();
}

async function loadMemoryStatus() {
  try {
    const s = await api("/api/memory/status");
    $("#mem-docs").textContent = s.docs;
    $("#mem-conclusions").textContent = s.conclusions;
    $("#mem-terms").textContent = s.terms;
    $("#mem-last").textContent = s.last_indexed
      ? `最近索引：${s.last_indexed}` : "尚未索引——点击「重建索引」启用研究记忆";
  } catch (_) {
    $("#mem-last").textContent = "状态获取失败";
  }
}

$("#btn-reindex").addEventListener("click", async () => {
  const btn = $("#btn-reindex");
  btn.disabled = true;
  btn.textContent = "索引中…（约 1 分钟）";
  try {
    const res = await api("/api/memory/reindex", { method: "POST" });
    $("#mem-last").textContent = `索引完成：${res.indexed}/${res.total} 份`;
    await loadMemoryStatus();
  } catch (err) {
    $("#mem-last").textContent = "索引失败：" + err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "重建索引";
  }
});

// 问答：NDJSON 流式
$("#btn-ask").addEventListener("click", askMemory);
$("#mem-question").addEventListener("keydown", (e) => {
  if (e.key === "Enter") askMemory();
});

async function askMemory() {
  const q = $("#mem-question").value.trim();
  if (!q) return;
  const status = $("#mem-ask-status");
  const out = $("#mem-answer");
  const hitsBox = $("#mem-hits");
  out.innerHTML = "";
  hitsBox.innerHTML = "";
  status.textContent = "检索记忆库中…";
  out.scrollIntoView({ behavior: "smooth", block: "nearest" });
  if (askAbort) askAbort.abort();
  askAbort = new AbortController();

  try {
    const res = await fetch("/api/memory/ask", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ question: q, ...reportDateParams("memory-dates") }),
      signal: askAbort.signal,
    });
    await checkedResponse(res);
    let acc = "", complete = false;
    await readEvents(res, obj => {
      if (obj.type === "sources") {
        for (const h of obj.hits || []) {
          const div = document.createElement("button");div.className="mem-hit";
          div.innerHTML=`<div class="mh-title">${escapeHtml(h.title)}</div><div class="mh-meta">${escapeHtml(h.grp||"")} · ${escapeHtml(h.date||"")}</div>`;
          div.onclick=()=>openReport(h.path);hitsBox.appendChild(div);
        }
      } else if (obj.type === "delta") {acc+=obj.text;out.innerHTML=renderMarkdown(acc);status.textContent=`回答中 · ${acc.length} 字`;}
      else if(obj.type === "done")complete=true;
    });
    if(!complete)throw new Error("回答传输中断，请重试");
    status.innerHTML = `<span class="ok">✔ 完成</span> · 点击上方来源卡片可打开原报告`;
  } catch (err) {
    status.innerHTML = err.name === "AbortError"
      ? "已停止"
      : `<span class="bad">出错：${escapeHtml(err.message)}</span>`;
  } finally { askAbort = null; await refreshAccount(); }
}

// 结论库
async function loadConclusions() {
  const sequence = ++conclusionSequence;
  const q = $("#concl-search").value.trim();
  const grp = $("#concl-group").value;
  const verdict = $("#concl-verdict").value;
  const params = new URLSearchParams({
    q, grp, verdict, limit: String(CONCL_PAGE), offset: String(conclOffset), ...reportDateParams("conclusion-dates"),
  });
  $("#concl-body").innerHTML =
    `<tr><td colspan="6">${loadingLine("加载结论库…")}</td></tr>`;
  const data = await api(`/api/memory/conclusions?${params}`);
  if (sequence !== conclusionSequence) return;
  if (data.total && !data.items.length && conclOffset) {conclOffset=0;return loadConclusions();}

  const gSel = $("#concl-group");
  if (gSel.options.length <= 1 && data.groups) {
    for (const g of data.groups) {
      const o = document.createElement("option");
      o.value = g; o.textContent = g;
      gSel.appendChild(o);
    }
  }

  $("#concl-count").innerHTML = `共 <b>${data.total}</b> 条结论`;
  const body = $("#concl-body");
  body.innerHTML = "";
  if (!data.items.length) {
    body.innerHTML = `<tr><td colspan="6" class="hint" style="padding:16px">
      当前条件下暂无结论，可扩大报告日期范围或调整搜索条件。</td></tr>`;
  }
  for (const it of data.items) {
    const v = it.verdict || "";
    let cls = "verdict-other";
    if (v.includes("通过") && !v.includes("不通过")) cls = "verdict-pass";
    else if (v.includes("不通过")) cls = "verdict-fail";
    else if (v.includes("灰")) cls = "verdict-grey";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="ct-title">${escapeHtml(it.title || it.path)}
        <div class="mh-meta">${escapeHtml(it.date || "")}</div></td>
      <td>${v ? `<span class="verdict-chip ${cls}">${escapeHtml(v)}</span>` : "—"}</td>
      <td>${it.score != null ? `${it.score}${it.score_max ? "/" + it.score_max : ""}` : "—"}</td>
      <td>${escapeHtml(it.price_range || "—")}</td>
      <td class="ct-sum">${escapeHtml(it.summary || "—")}</td>
      <td>${it.source === "llm" ? "深度" : "规则"}</td>`;
    tr.querySelector(".ct-title").addEventListener("click", () => openReport(it.path));
    body.appendChild(tr);
  }

  renderPageNumbers("concl-page-info",conclOffset,CONCL_PAGE,data.total,page=>{conclOffset=(page-1)*CONCL_PAGE;loadConclusions();});
  $("#concl-prev").disabled = conclOffset <= 0;
  $("#concl-next").disabled = conclOffset + CONCL_PAGE >= data.total;
}

$("#concl-search").addEventListener("input", debounce(() => { conclOffset = 0; loadConclusions(); }, 300));
$("#concl-group").addEventListener("change", () => { conclOffset = 0; loadConclusions(); });
$("#concl-verdict").addEventListener("change", () => { conclOffset = 0; loadConclusions(); });
$("#concl-prev").addEventListener("click", () => { conclOffset = Math.max(0, conclOffset - CONCL_PAGE); loadConclusions(); });
$("#concl-next").addEventListener("click", () => { conclOffset += CONCL_PAGE; loadConclusions(); });

/* ================= 研究历史 ================= */
let historyOffset = 0, historySequence = 0;
const HISTORY_PAGE = 20;
async function loadHistory() {
  const sequence = ++historySequence;
  const q = $("#history-search").value.trim();
  const root = $("#history-list");
  root.innerHTML = loadingLine("加载研究历史…");
  const params = new URLSearchParams({q, limit:String(HISTORY_PAGE),offset:String(historyOffset),...reportDateParams("history-dates")});
  const data = await api(`/api/history?${params}`);
  if (sequence !== historySequence) return;
  if (data.total && !data.items.length && historyOffset) {historyOffset=0;return loadHistory();}
  renderPageNumbers("history-page-info",historyOffset,HISTORY_PAGE,data.total,page=>{historyOffset=(page-1)*HISTORY_PAGE;loadHistory();});
  $("#history-prev").disabled = historyOffset === 0;
  $("#history-next").disabled = historyOffset + HISTORY_PAGE >= data.total;
  if (!data.items.length) {
    root.innerHTML = '<div class="card"><p class="hint">当前条件下暂无历史研究，可扩大日期范围或调整搜索。</p></div>';
    return;
  }
  root.innerHTML = "";
  for (const it of data.items) {
    const div = document.createElement("div");
    div.className = "list-item";
    div.tabIndex = 0;
    div.setAttribute("role", "button");
    div.addEventListener("keydown", e => {if(e.target===div && (e.key==="Enter"||e.key===" ")){e.preventDefault();div.click();}});
    div.innerHTML = `
      <div class="li-title">${escapeHtml(it.skill_title || it.skill_id)}
        ${it.arguments ? `<span class="tag">${escapeHtml(it.arguments)}</span>` : ""}
      </div>
      <div class="li-meta">
        <span>${escapeHtml(it.date || "日期未知")}</span>
        <span>${escapeHtml(it.model || "")}</span>
        <span>${it.chars || 0} 字</span>
        <span class="li-actions"><button class="btn small del" data-id="${it.id}">删除</button></span>
      </div>
      <div class="li-preview">${escapeHtml(it.preview || "")}</div>`;
    div.addEventListener("click", (e) => {
      if (e.target.classList.contains("del")) return;
      openHistory(it.id);
    });
    div.querySelector(".del").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!await uiConfirm("删除这条研究记录？")) return;
      await api(`/api/history/${it.id}`, { method: "DELETE" });
      loadHistory();
    });
    root.appendChild(div);
  }
}

async function openHistory(id) {
  const rec = await api(`/api/history/${id}`);
  $("#modal-meta").textContent = `${formatDate(rec.created_at)} · ${rec.model || ""}`;
  $("#modal-title").textContent = rec.skill_title || rec.skill_id;
  $("#modal-content").innerHTML = renderMarkdown(rec.content || "");
  showReportReader(rec.content || "");
}

$("#history-search").addEventListener("input", debounce(()=>{historyOffset=0;loadHistory();}, 300));
$("#btn-history-refresh").addEventListener("click", loadHistory);
$("#history-prev").onclick=()=>{historyOffset=Math.max(0,historyOffset-HISTORY_PAGE);loadHistory();};
$("#history-next").onclick=()=>{historyOffset+=HISTORY_PAGE;loadHistory();};

/* ================= 报告库（官方 + 站内共享） ================= */
let reportSource = "all";   // all | official | generated | imported
let reportScope = "all";    // all | mine
let reportLoadSequence = 0;

async function loadReports() {
  const sequence = ++reportLoadSequence;
  const q = $("#report-search").value.trim();
  const group = $("#report-group").value;
  const rtype = $("#report-type").value;
  const params = new URLSearchParams({
    q, group, rtype, source: reportSource, scope: reportScope,
    limit: String(REPORT_PAGE), offset: String(reportOffset), ...reportDateParams("report-dates"),
  });
  $("#report-list").innerHTML = loadingLine("检索报告库…");
  const data = await api(`/api/reports?${params}`);
  if (sequence !== reportLoadSequence) return;
  if (data.total && !data.items.length && reportOffset) {reportOffset=0;return loadReports();}

  // 填充筛选器（仅首次）
  const gSel = $("#report-group");
  const tSel = $("#report-type");
  if (!group && !rtype && data.facets) {
    gSel.replaceChildren(new Option("全部公司/专题", ""));
    tSel.replaceChildren(new Option("全部类型", ""));
    for (const g of data.facets.groups) {
      const o = document.createElement("option");
      o.value = g; o.textContent = g;
      gSel.appendChild(o);
    }
    for (const t of data.facets.types) {
      const o = document.createElement("option");
      o.value = t; o.textContent = t;
      tSel.appendChild(o);
    }
  }

  const c = data.counts || {};
  $("#report-count").innerHTML =
    `共 <b>${data.total}</b> 份` +
    `（官方 ${c.official ?? "-"} · 站内共享 ${c.generated ?? "-"} · 本地导入 ${c.imported ?? 0}）` +
    `${data.index_count ? " · 官方索引 " + data.index_count : ""}`;
  const root = $("#report-list");
  root.innerHTML = "";
  if (!data.items.length) {
    root.innerHTML = '<div class="card"><p class="hint">没有匹配的报告。</p></div>';
  }
  for (const it of data.items) {
    const div = document.createElement("div");
    div.className = "list-item";
    div.tabIndex = 0;
    div.setAttribute("role", "button");
    div.addEventListener("keydown", e => {if(e.target===div && (e.key==="Enter"||e.key===" ")){e.preventDefault();div.click();}});
    const isGen = it.source === "generated";
    const isImported = it.source === "imported";
    div.innerHTML = `
      <div class="li-title">
        ${isImported ? '<span class="src-badge src-imported">导入</span>' : isGen ? '<span class="src-badge src-gen">共享</span>' : '<span class="src-badge src-official">官方</span>'}
        ${escapeHtml(it.title)}
      </div>
      <div class="li-meta">
        <span class="tag">${escapeHtml(it.group || "-")}</span>
        ${it.type ? `<span>${escapeHtml(it.type)}</span>` : ""}
        ${it.author ? `<span class="li-author">${isImported ? "导入者" : "by"} ${escapeHtml(it.author)}</span>` : ""}
        <span>${escapeHtml(it.date || "日期未知")}</span>
        ${it.arguments ? `<span class="li-args">${escapeHtml(it.arguments)}</span>` : ""}
      </div>${isImported ? `<div class="li-meta import-source-path">${escapeHtml(it.source_path)}</div>` : ""}`;
    div.addEventListener("click", () => openReport(it.path));
    if (isImported && it.author_id === currentUser?.id) {
      const remove = document.createElement("button");
      remove.className = "btn small import-remove";
      remove.textContent = "删除导入";
      remove.addEventListener("click", async e => {
        e.stopPropagation();
        if (!await uiConfirm(`从共享报告库删除「${it.title}」？本机原文件不受影响。`)) return;
        try { await api(`/api/reports/imported/${encodeURIComponent(it.id)}`, {method:"DELETE"}); await loadReports(); }
        catch (err) { toast("删除失败：" + err.message, "bad"); }
      });
      div.appendChild(remove);
    }
    root.appendChild(div);
  }

  renderPageNumbers("report-page-info",reportOffset,REPORT_PAGE,data.total,page=>{reportOffset=(page-1)*REPORT_PAGE;loadReports();});
  $("#report-prev").disabled = reportOffset <= 0;
  $("#report-next").disabled = reportOffset + REPORT_PAGE >= data.total;
}

async function openReport(path) {
  try {
    const doc = await api(`/api/reports/content?path=${encodeURIComponent(path)}`);
    $("#modal-title").textContent = doc.title;
    let meta = "";
    if (doc.source === "imported") {
      meta = `本地导入 · 导入者 ${doc.author || "匿名"} · ${doc.date || "日期未知"} · ${doc.source_path}`;
    } else if (doc.source === "generated") {
      meta = `共享研报 · ${doc.author || "匿名"} · ${doc.created_at || ""}` +
        (doc.arguments ? ` · ${doc.arguments}` : "");
    } else {
      meta = `官方研报 · ${doc.group || ""}`;
    }
    $("#modal-meta").textContent = meta;
    $("#modal-content").innerHTML = renderMarkdown(doc.content || "");
    showReportReader(doc.content || "");
  } catch (err) {
    toast("打开报告失败：" + err.message, "bad");
  }
}

// 来源/范围筛选 chips
$$("#report-source-chips .chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    if (chip.dataset.scope) {
      // 只看我的：开关式
      const on = chip.classList.toggle("active");
      reportScope = on ? "mine" : "all";
    } else {
      $$("#report-source-chips .chip").forEach((c) => {
        if (!c.dataset.scope) c.classList.remove("active");
      });
      chip.classList.add("active");
      reportSource = chip.dataset.source || "all";
      $("#report-group").value = "";
      $("#report-type").value = "";
    }
    reportOffset = 0;
    loadReports();
  });
});

$("#btn-report-search").addEventListener("click", () => { reportOffset = 0; loadReports(); });
$("#btn-report-refresh").addEventListener("click", async () => {
  const btn = $("#btn-report-refresh");
  btn.disabled = true;
  btn.textContent = "⟳ 更新中…";
  try {
    const res = await api("/api/reports/refresh", { method: "POST" });
    $("#report-count").innerHTML =
      `索引已更新：<b>${res.count}</b> 份报告 · ${escapeHtml(res.built_at)}`;
    reportOffset = 0;
    await loadReports();
  } catch (err) {
    $("#report-count").innerHTML = `<span class="bad">更新失败：${escapeHtml(err.message)}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "⟳ 更新索引";
  }
});

// 同步官方仓库（仅管理员）
async function loadSyncStatus() {
  if (!currentUser?.is_admin) return;
  $$(".admin-only").forEach((el) => { el.style.display = ""; });
  try {
    const st = await api("/api/admin/sync-status");
    const el = $("#sync-status");
    if (st.state === "running") {
      el.innerHTML = `<span class="ok">⟳ ${escapeHtml(st.message)}…</span>`;
      setTimeout(loadSyncStatus, 2500);
    } else if (st.state === "done") {
      el.innerHTML = `<span class="ok">✔ ${escapeHtml(st.message)}</span>`;
    } else if (st.state === "error") {
      el.innerHTML = `<span class="bad">✘ ${escapeHtml(st.message)}</span>`;
    } else {
      el.textContent = st.message || "";
    }
  } catch (_) { /* 忽略 */ }
}

$("#btn-sync-upstream").addEventListener("click", async () => {
  const btn = $("#btn-sync-upstream");
  btn.disabled = true;
  btn.textContent = "⟳ 同步中…";
  try {
    const st = await api("/api/admin/sync-ai-berkshire", { method: "POST" });
    toast(st.state === "running" ? "已开始同步官方仓库…" : (st.message || "同步完成"), "ok");
    loadSyncStatus();
  } catch (err) {
    toast("同步失败：" + err.message, "bad");
  } finally {
    btn.disabled = false;
    btn.textContent = "⟳ 同步官方仓库";
  }
});

$("#report-search").addEventListener("input", debounce(() => { reportOffset = 0; loadReports(); }, 350));
$("#report-group").addEventListener("change", () => { reportOffset = 0; loadReports(); });
$("#report-type").addEventListener("change", () => { reportOffset = 0; loadReports(); });
$("#report-prev").addEventListener("click", () => { reportOffset = Math.max(0, reportOffset - REPORT_PAGE); loadReports(); });
$("#report-next").addEventListener("click", () => { reportOffset += REPORT_PAGE; loadReports(); });

/* ================= 弹层 ================= */
$("#modal-close").addEventListener("click", closeReportReader);
$("#report-modal").addEventListener("click", (e) => {
  if (e.target.id === "report-modal") closeReportReader();
});

/* ================= 用户管理（管理员） ================= */
let TIER_LABELS = { free: "基础会员", vip: "标准会员", svip: "高级会员" };

function applyMembershipTiers(tiers) {
  if (!tiers || !Object.keys(tiers).length) return;
  TIER_LABELS = Object.fromEntries(Object.entries(tiers).map(([id, tier]) => [id, tier.label]));
  const filter = $("#user-filter"), selected = filter.value;
  filter.innerHTML = '<option value="">全部等级</option>' + Object.entries(TIER_LABELS).map(([id,label])=>`<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`).join("");
  filter.value = selected in TIER_LABELS ? selected : "";
  $("#policy-form .policy-grid").innerHTML = Object.entries(tiers).map(([id,tier])=>`<div class="field"><label for="policy-${escapeHtml(id)}">${escapeHtml(tier.label)} / 天</label><input type="number" id="policy-${escapeHtml(id)}" data-tier-quota="${escapeHtml(id)}" min="0" max="10000" value="${tier.quota}" required></div>`).join("");
}

/* ---- 数据迁移（导出/导入 app.db） ---- */
async function loadDataStatus() {
  try {
    const d = await api("/api/admin/data/status");
    const c = d.counts || {};
    $("#data-counts").innerHTML =
      `用户 <b>${c.users ?? "-"}</b> · 共享报告 <b>${c.history ?? "-"}</b> · ` +
      `本地导入 <b>${c.imported_reports ?? 0}</b> · ` +
      `模型配置 <b>${c.user_settings ?? "-"}</b> · 会话 <b>${c.sessions ?? "-"}</b>`;
  } catch (_) {
    $("#data-counts").textContent = "无法读取数据概况";
  }
}

$("#btn-data-export").addEventListener("click", async () => {
  const msg = $("#data-msg");
  msg.textContent = "正在打包…";
  try {
    const res = await fetch("/api/admin/data/export", {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error("导出失败 " + res.status);
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `ai-berkshire-web-backup-${Date.now()}.zip`;
    a.click();
    msg.innerHTML = '<span class="ok">已下载备份（含用户与全部共享报告）</span>';
  } catch (err) {
    msg.innerHTML = `<span class="bad">${escapeHtml(err.message)}</span>`;
  }
});

$("#data-import-file").addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  const msg = $("#data-msg");
  if (!await uiConfirm(`导入「${file.name}」将覆盖当前全部用户与共享报告（旧库自动备份）。继续？`)) {
    e.target.value = "";
    return;
  }
  msg.textContent = "正在导入…";
  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/admin/data/import", {
      method: "POST",
      headers: authHeaders(),   // 不设 Content-Type，让浏览器带 multipart boundary
      body: fd,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "导入失败");
    msg.innerHTML = `<span class="ok">✔ 导入成功</span> · 当前 ${data.counts?.history ?? 0} 份共享报告` +
      (data.backup_path ? " · 旧库已自动备份" : "");
    toast("数据导入成功，请重新登录", "ok");
    localStorage.removeItem(TOKEN_KEY);
    setTimeout(() => location.reload(), 800);
  } catch (err) {
    msg.innerHTML = `<span class="bad">${escapeHtml(err.message)}</span>`;
    toast("导入失败：" + err.message, "bad");
  } finally {
    e.target.value = "";
  }
});

let adminUsers = [], adminRootId = "", canManageRoles = false;
async function loadUsers() {
  if(currentUser.is_root_admin) loadDataStatus();
  const body = $("#users-body");body.innerHTML=`<tr><td colspan="6">${loadingLine("加载用户列表…")}</td></tr>`;
  try {
    const data = await api("/api/admin/users");
    adminUsers=data.items;adminRootId=data.root_admin_id;canManageRoles=data.can_manage_roles;
    $("#admin-summary").innerHTML=`<div class="stat-card"><span class="stat-label">全部用户</span><strong>${data.items.length}</strong><small>每位用户的数据独立保存</small></div><div class="stat-card"><span class="stat-label">管理员</span><strong>${data.items.filter(u=>u.is_admin).length}</strong><small>管理员研究额度不限</small></div><div class="stat-card"><span class="stat-label">正常使用</span><strong>${data.items.filter(u=>!u.is_disabled).length}</strong><small>禁用后立即撤销登录会话</small></div>`;
    applyMembershipTiers(data.tiers);
    $$('[data-tier-quota]').forEach(input=>{input.value=data.policy[input.dataset.tierQuota + "_quota"] ?? data.tiers[input.dataset.tierQuota].quota;});
    $("#policy-invite").checked=data.policy.invite_required;
    renderUsers();
  }catch(err){body.innerHTML=`<tr><td colspan="6">${escapeHtml(err.message)}</td></tr>`;}
}
function renderUsers() {
  const query=$("#user-search").value.trim().toLowerCase(),tier=$("#user-filter").value;
  const items=adminUsers.filter(u=>(!query||u.username.toLowerCase().includes(query))&&(!tier||u.tier===tier));
  const body=$("#users-body");body.innerHTML="";
  if(!items.length){body.innerHTML='<tr><td colspan="6" class="users-empty">没有符合条件的用户，请调整搜索或筛选。</td></tr>';return;}
  for(const u of items){
    const root=u.id===adminRootId,self=u.id===currentUser.id,protectedUser=u.is_admin&&!currentUser.is_root_admin;
    const tr=document.createElement("tr");
    tr.innerHTML=`<td><b>${escapeHtml(u.username)}</b>${self?' <span class="cfg-tag">我</span>':''}${root?'<span class="root-label">初始管理员</span>':''}</td>
      <td><select class="tier-select" data-id="${u.id}" aria-label="${escapeHtml(u.username)}的会员等级" ${protectedUser?'disabled':''}>${Object.entries(TIER_LABELS).map(([v,l])=>`<option value="${escapeHtml(v)}" ${u.tier===v?'selected':''}>${escapeHtml(l)}</option>`).join('')}</select></td>
      <td><label class="chk"><input type="checkbox" class="admin-chk" aria-label="${escapeHtml(u.username)}的管理员权限" data-id="${u.id}" ${u.is_admin?'checked':''} ${!canManageRoles||root||self?'disabled':''}>管理员</label></td>
      <td><span class="verdict-chip ${u.is_disabled?'verdict-fail':'verdict-pass'}">${u.is_disabled?'已禁用':'正常'}</span></td>
      <td>${escapeHtml((u.created_at||'').slice(0,10))}</td><td class="li-actions">
      <button class="btn small reset-pwd" data-id="${u.id}" ${protectedUser||self?'disabled':''}>重置密码</button>
      <button class="btn small toggle-dis" data-id="${u.id}" ${root||self||protectedUser?'disabled':''}>${u.is_disabled?'启用':'禁用'}</button>
      ${canManageRoles?`<button class="btn small danger del-user" data-id="${u.id}" ${root||self?'disabled':''}>删除</button>`:''}</td>`;
    body.appendChild(tr);
  }
  body.querySelectorAll('.tier-select').forEach(sel=>sel.onchange=async()=>{
    sel.disabled=true;
    try{await api(`/api/admin/users/${sel.dataset.id}`,{method:'PUT',body:JSON.stringify({tier:sel.value})});toast('会员等级已更新','ok');await loadUsers();await refreshAccount();}catch(err){toast(err.message,'bad');renderUsers();}
  });
  body.querySelectorAll('.admin-chk').forEach(chk=>chk.onchange=async()=>{
    if(!await uiConfirm(chk.checked?'授予此用户管理员权限？管理员可管理会员并使用不限额研究。':'取消此用户的管理员权限？')){chk.checked=!chk.checked;return;}
    chk.disabled=true;
    try{await api(`/api/admin/users/${chk.dataset.id}`,{method:'PUT',body:JSON.stringify({is_admin:chk.checked})});toast('管理权限已更新','ok');await loadUsers();}catch(err){toast(err.message,'bad');renderUsers();}
  });
  body.querySelectorAll('.toggle-dis').forEach(btn=>btn.onclick=async()=>{
    const user=adminUsers.find(u=>u.id===btn.dataset.id);
    if(!await uiConfirm(user.is_disabled?`恢复 ${user.username} 的访问权限？`:`禁用 ${user.username}？该用户所有设备将立即退出登录。`))return;
    btn.disabled=true;
    try{await api(`/api/admin/users/${user.id}`,{method:'PUT',body:JSON.stringify({is_disabled:!user.is_disabled})});await loadUsers();toast('用户状态已更新','ok');}catch(err){toast(err.message,'bad');renderUsers();}
  });
  body.querySelectorAll('.reset-pwd').forEach(btn=>btn.onclick=async()=>{
    const pwd=await uiPrompt('重置后该用户所有设备需要重新登录，请设置至少 8 位的新密码。');if(!pwd)return;
    btn.disabled=true;
    try{await api(`/api/admin/users/${btn.dataset.id}/reset-password`,{method:'POST',body:JSON.stringify({new_password:pwd})});toast('密码已重置，用户已退出登录','ok');}catch(err){toast(err.message,'bad');}finally{btn.disabled=false;}
  });
  body.querySelectorAll('.del-user').forEach(btn=>btn.onclick=async()=>{
    const user=adminUsers.find(u=>u.id===btn.dataset.id);
    if(!await uiConfirm(`永久删除 ${user.username}？该用户的研究历史、用量、邀请和模型配置将一并删除，无法撤销。`))return;
    btn.disabled=true;
    try{await api(`/api/admin/users/${user.id}`,{method:'DELETE'});await loadUsers();toast('用户已删除','ok');}catch(err){toast(err.message,'bad');btn.disabled=false;}
  });
}
$("#user-search").addEventListener("input",renderUsers);
$("#user-filter").addEventListener("change",renderUsers);
$("#btn-users-refresh").addEventListener("click",loadUsers);

/* ================= 设置（多模型配置） ================= */
let editingCfgId = null;   // null = 新建

async function loadSettings() {
  const data = await api("/api/settings");
  renderCfgList(data);
  if (!editingCfgId) {
    // 首次进入：默认编辑启用中的配置
    const active = data.configs.find((c) => c.id === data.active_id);
    if (active) fillCfgForm(active);
  }
}

function renderCfgList(data) {
  const root = $("#cfg-list");
  root.innerHTML = "";
  for (const c of data.configs) {
    const isActive = c.id === data.active_id;
    const div = document.createElement("div");
    div.className = "cfg-card" + (isActive ? " active-config" : "");
    const tags = [];
    if (c.enable_thinking) tags.push("深度思考");
    if (c.enable_websearch || c.enable_web_search) tags.push("网页搜索");
    div.innerHTML = `
      <div class="cfg-main">
        <div class="cfg-line1">
          <span class="cfg-name">${escapeHtml(c.name)}</span>
          <span>${escapeHtml(c.model || "-")}</span>
          <span>${escapeHtml(c.base_url || "")}</span>
          <span>Key：${escapeHtml(c.api_key_masked || "未配置")}</span>
          <span class="cfg-tags">${tags.map((t) => `<span class="cfg-tag">${escapeHtml(t)}</span>`).join("")}</span>
          ${isActive ? '<span class="cfg-tag">启用中</span>' : ""}
        </div>
        <div class="cfg-line2">最大输入 ${escapeHtml(c.max_input_tokens || "不限")} · 最大输出 ${escapeHtml(c.max_output_tokens || "不限")} · ${escapeHtml(c.web_search_mode || "")}</div>
      </div>
      <div class="cfg-actions">
        <button class="btn small edit">编辑</button>
        ${isActive
          ? `<button class="btn small cancel-use">取消启用</button>`
          : `<button class="btn small success use">启用</button>`}
        <button class="btn small danger del">删除</button>
      </div>`;
    div.querySelector(".edit").addEventListener("click", () => {
      editingCfgId = c.id;
      fillCfgForm(c);
      $("#cfg-editor-title").textContent = `编辑模型配置 · ${c.name}`;
      document.querySelector(".cfg-editor").scrollIntoView({ behavior: "smooth" });
    });
    const useBtn = div.querySelector(".use");
    if (useBtn) {
      useBtn.addEventListener("click", async () => {
        await api(`/api/settings/${c.id}/activate`, { method: "POST" });
        loadSettings();
        refreshApiStatus();
      });
    }
    const cancelBtn = div.querySelector(".cancel-use");
    if (cancelBtn) {
      cancelBtn.addEventListener("click", async () => {
        // "取消启用" = 切回演示模式（新建一个 demo 配置并启用，语义最贴合）
        let demo = data.configs.find((x) => x.provider === "demo");
        if (!demo) {
          demo = await api("/api/settings", {
            method: "POST",
            body: JSON.stringify({
              name: "演示模式", provider: "demo", model: "",
              max_input_tokens: "不限", max_output_tokens: "不限",
            }),
          });
        }
        await api(`/api/settings/${demo.id}/activate`, { method: "POST" });
        loadSettings();
        refreshApiStatus();
      });
    }
    div.querySelector(".del").addEventListener("click", async () => {
      if (!await uiConfirm(`删除配置「${c.name}」？`)) return;
      await api(`/api/settings/${c.id}`, { method: "DELETE" });
      if (editingCfgId === c.id) editingCfgId = null;
      loadSettings();
      refreshApiStatus();
    });
    root.appendChild(div);
  }
}

function setCfgTokenLimit(selector, value) {
  const select = $(selector);
  // 旧配置可能使用数字、k 后缀或非预设值，编辑时原样保留。
  select.querySelectorAll("[data-saved-value]").forEach((option) => option.remove());
  const saved = value == null || String(value).trim() === "" ? "不限" : String(value);
  if (!Array.from(select.options).some((option) => option.value === saved)) {
    const option = new Option(`已保存：${saved} Token`, saved);
    option.dataset.savedValue = "true";
    select.add(option);
  }
  select.value = saved;
}

function cfgProviderOptions() {
  return Array.from($("#cfg-provider").options);
}

function normalizeBaseUrl(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

function resolveProviderPreset(c) {
  const options = cfgProviderOptions();
  if (c.provider_preset && options.some((option) => option.value === c.provider_preset)) {
    return c.provider_preset;
  }
  if (c.provider === "demo") return "demo";
  const baseUrl = normalizeBaseUrl(c.base_url);
  const matched = options.find((option) =>
    option.dataset.baseUrl && normalizeBaseUrl(option.dataset.baseUrl) === baseUrl);
  if (matched) return matched.value;
  if (!baseUrl) return c.provider === "anthropic" ? "anthropic" : "openai";
  return c.provider === "anthropic" ? "custom-anthropic" : "custom-openai";
}

function applyProviderPreset(replaceUrl = false) {
  const option = $("#cfg-provider").selectedOptions[0];
  if (!option) return;
  const baseUrl = option.dataset.baseUrl || "";
  if (replaceUrl) $("#cfg-base-url").value = baseUrl;
  $("#cfg-base-url").placeholder = baseUrl || "https://api.example.com/v1";
  $("#cfg-model").placeholder = option.dataset.modelPlaceholder || "填写模型 ID";
}

function fillCfgForm(c) {
  $("#cfg-name").value = c.name || "";
  $("#cfg-provider").value = resolveProviderPreset(c);
  const option = $("#cfg-provider").selectedOptions[0];
  $("#cfg-base-url").value = c.base_url || option?.dataset.baseUrl || "";
  $("#cfg-api-key").value = "";
  $("#cfg-model").value = c.model || "";
  applyProviderPreset();
  $("#cfg-thinking").value = c.thinking || "高（默认）";
  $("#cfg-websearch").value = c.web_search_mode || "模型侧联网优先（失败自动回退自建搜索）";
  setCfgTokenLimit("#cfg-max-in", c.max_input_tokens);
  setCfgTokenLimit("#cfg-max-out", c.max_output_tokens);
  $("#cfg-enable-thinking").checked = c.enable_thinking !== false;
  $("#cfg-enable-websearch").checked = (c.enable_websearch ?? c.enable_web_search) !== false;
  $("#cfg-clear-key").checked = false;
  $("#cfg-key-status").textContent = c.has_api_key
    ? `当前：${c.api_key_masked}，留空表示保留`
    : "当前：未配置";
  // 自定义请求头
  const rows = $("#headers-rows");
  rows.innerHTML = "";
  for (const [k, v] of Object.entries(c.headers || {})) addHeaderRow(k, v);
}

function addHeaderRow(k = "", v = "") {
  const rows = $("#headers-rows");
  if (rows.children.length >= 8) return;
  const div = document.createElement("div");
  div.className = "header-row";
  div.innerHTML = `
    <input type="text" class="hk" aria-label="请求头名称" placeholder="请求头名称" value="${escapeHtml(k)}">
    <input type="text" class="hv" aria-label="请求头值" placeholder="值" value="${escapeHtml(v)}">
    <button class="btn small del-header" aria-label="删除请求头" title="删除请求头">✕</button>`;
  div.querySelector(".del-header").addEventListener("click", () => div.remove());
  rows.appendChild(div);
}

$("#btn-add-header").addEventListener("click", () => addHeaderRow());

// 切换服务商自动带入官方 Base URL，仍允许用户手动覆盖。
$("#cfg-provider").addEventListener("change", () => applyProviderPreset(true));

$("#btn-cfg-cancel").addEventListener("click", () => {
  editingCfgId = null;
  $("#cfg-editor-title").textContent = "新建模型配置";
  fillCfgForm({
    name: "", provider: "openai",
    base_url: "", model: "", provider_preset: "custom-openai",
    max_input_tokens: "不限", max_output_tokens: "不限", headers: {},
  });
  $("#cfg-key-status").textContent = "当前：未配置";
});

$("#btn-save-settings").addEventListener("click", async () => {
  const headers = {};
  document.querySelectorAll("#headers-rows .header-row").forEach((row) => {
    const k = row.querySelector(".hk").value.trim();
    const v = row.querySelector(".hv").value.trim();
    if (k) headers[k] = v;
  });
  const providerOption = $("#cfg-provider").selectedOptions[0];
  const payload = {
    name: $("#cfg-name").value.trim() || "未命名配置",
    provider: providerOption?.dataset.protocol || "openai",
    provider_preset: $("#cfg-provider").value,
    base_url: $("#cfg-base-url").value.trim(),
    model: $("#cfg-model").value.trim(),
    temperature: 0.7,
    max_input_tokens: $("#cfg-max-in").value.trim() || "不限",
    max_output_tokens: $("#cfg-max-out").value.trim() || "不限",
    thinking: $("#cfg-thinking").value,
    web_search_mode: $("#cfg-websearch").value,
    enable_thinking: $("#cfg-enable-thinking").checked,
    enable_web_search: $("#cfg-enable-websearch").checked,
    headers,
    clear_key: $("#cfg-clear-key").checked,
  };
  const key = $("#cfg-api-key").value.trim();
  if (key) payload.api_key = key;
  try {
    let saved;
    if (editingCfgId) {
      saved = await api(`/api/settings/${editingCfgId}`, {
        method: "PUT", body: JSON.stringify(payload),
      });
    } else {
      saved = await api("/api/settings", {
        method: "POST", body: JSON.stringify(payload),
      });
      editingCfgId = saved.id;
    }
    $("#settings-msg").innerHTML = '<span class="ok">已保存</span>';
    toast("模型配置已保存", "ok");
    loadSettings();
    refreshApiStatus();
  } catch (err) {
    $("#settings-msg").innerHTML = `<span class="bad">保存失败：${escapeHtml(err.message)}</span>`;
  }
});

$("#btn-test-conn").addEventListener("click", async () => {
  $("#settings-msg").textContent = "测试中…（当前启用的配置）";
  try {
    const res = await api("/api/settings/test", { method: "POST" });
    $("#settings-msg").innerHTML = res.ok
      ? `<span class="ok">${escapeHtml(res.message)}</span>`
      : `<span class="bad">${escapeHtml(res.message)}</span>`;
  } catch (err) {
    $("#settings-msg").innerHTML = `<span class="bad">${escapeHtml(err.message)}</span>`;
  }
});

/* ================= Toast 通知（替代打断式 alert） ================= */
const toastBox = document.createElement("div");
toastBox.id = "toast-box";
document.body.appendChild(toastBox);

function toast(msg, type = "") {
  const div = document.createElement("div");
  div.className = "toast" + (type ? " " + type : "");
  div.textContent = msg;
  toastBox.appendChild(div);
  setTimeout(() => {
    div.style.opacity = "0";
    div.style.transition = "opacity .25s";
    setTimeout(() => div.remove(), 260);
  }, 2600);
}

function loadingLine(text) {
  return `<div class="loading-line"><div class="spinner"></div>${escapeHtml(text)}</div>`;
}

/* ================= Markdown 全屏切换 ================= */
let fsEl = null;
let fsPrevious = null;
const fsExit = document.createElement("button");
fsExit.type = "button";
fsExit.id = "fs-exit";
fsExit.textContent = "⛶ 退出全屏（Esc）";
document.body.appendChild(fsExit);

function setReaderFullscreenState(active) {
  const button = $("#reader-focus");
  button.setAttribute("aria-pressed", String(active));
  button.setAttribute("aria-label", active ? "退出全屏" : "全屏阅读");
  button.dataset.tooltip = active ? "退出全屏（Esc）" : "全屏阅读";
}

function enterFs(el) {
  if (!el) return;
  exitFs();
  fsPrevious = document.activeElement;
  const reader = el.closest("#report-modal");
  if (reader) el = reader.querySelector(".modal-box");
  el.classList.add("fullscreen");
  fsEl = el;
  document.body.classList.add("has-fullscreen");
  fsExit.style.display = reader ? "none" : "block";
  setReaderFullscreenState(!!reader);
  (reader ? $("#reader-focus") : fsExit).focus({ preventScroll: true });
}

function exitFs() {
  if (fsEl) fsEl.classList.remove("fullscreen");
  fsEl = null;
  document.body.classList.remove("has-fullscreen");
  fsExit.style.display = "none";
  setReaderFullscreenState(false);
  fsPrevious?.focus({ preventScroll: true });
  fsPrevious = null;
}

fsExit.addEventListener("click", exitFs);
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if ($("#action-dialog").open || $("#report-import-dialog").open) return;
  if (fsEl) { exitFs(); return; }
  const modal = $("#report-modal");
  if (modal && modal.classList.contains("open")) closeReportReader();
});

// Reading is explicit; selecting text or following a link never opens fullscreen.
const focusButton = document.createElement("button");
focusButton.className = "btn small"; focusButton.textContent = "专注阅读";
focusButton.onclick = () => enterFs($("#research-output"));
$("#research-output-card .output-actions").appendChild(focusButton);

/* ================= 工具函数 ================= */
function escapeHtml(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

/* ================= 启动 ================= */
createReportDateFilter("report-dates",()=>{reportOffset=0;loadReports().catch(err=>toast(err.message,"bad"));});
createReportDateFilter("history-dates",()=>{historyOffset=0;loadHistory().catch(err=>toast(err.message,"bad"));});
createReportDateFilter("conclusion-dates",()=>{conclOffset=0;loadConclusions().catch(err=>toast(err.message,"bad"));});
createReportDateFilter("timeline-dates",async()=>{timelineCompanyOffset=0;timelineOffset=0;await loadTimelineCompanies();if($("#tl-company").value)loadTimeline();});
createReportDateFilter("memory-dates",()=>{$("#mem-ask-status").textContent="日期范围已更新，下次提问将使用新范围。";});
async function startApp() {
  await refreshAccount();
  const results = await Promise.allSettled([refreshApiStatus(), loadSkills(), loadRecentWork()]);
  if(results[1].status === "rejected") $("#skill-catalog").innerHTML = makeEmpty("研究方法加载失败", "请检查连接后刷新页面重试。");
  const page = location.hash.slice(1);
  if(page && page!=="research" && workspaceIcons[page]) goToPage(page);
}

(async function init() {
  await loadRegistrationPolicy();
  const invite = new URLSearchParams(location.search).get("invite");
  if(invite) {$("#login-invite").value = invite.slice(0,64);setAuthMode(true);history.replaceState(null,"",location.pathname+location.hash);}
  const ok = await checkAuth();
  if(ok) {hideLogin();await startApp();} else {showLogin();}
})();
