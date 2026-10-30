/* Workspace interactions, accessible dialogs and account views. */
"use strict";
const workspaceIcons = {
  research:
    '<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="m16 3 5 5-9 9-5 1 1-5Z"/>',
  pipeline:
    '<rect x="3" y="3" width="7" height="6" rx="1"/><rect x="14" y="15" width="7" height="6" rx="1"/><path d="M6 9v9h8M14 6h4v9"/>',
  memory:
    '<path d="M12 5C7 1 2 4 3 9c-3 4 0 9 5 9l4 3 4-3c5 0 8-5 5-9 1-5-4-8-9-4Zm0 0v16M7 8l5 3 5-3M6 14l6 2 6-2"/>',
  timeline: '<path d="M4 3v17h17M7 15l4-5 4 2 6-8"/>',
  history: '<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v6l4 2"/>',
  reports:
    '<path d="M3 4h6l3 3 3-3h6v16h-6l-3 1-3-1H3ZM12 7v14M6 8h2M6 12h2M16 9h2M16 13h2"/>',
  users:
    '<circle cx="9" cy="8" r="3"/><path d="M3 20v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6m1 3c3 0 4 2 4 5"/>',
  account:
    '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="9" r="3"/><path d="M5 18c1-5 13-5 14 0"/>',
  settings:
    '<path d="m9 3-1 3-3 1 1 4-2 2 2 3 3 1 1 4h4l1-4 3-1 2-3-2-2 1-4-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
};
document.querySelectorAll(".nav-item").forEach((button) => {
  const icon = button.querySelector(".nav-ic");
  if (icon)
    icon.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${workspaceIcons[button.dataset.page] || ""}</svg>`;
});
function closeMobileNav() {
  document.querySelector(".sidebar").classList.remove("mobile-open");
  document.querySelector(".sidebar-scrim").hidden = true;
  document.querySelector("#menu-toggle").setAttribute("aria-expanded", "false");
}
document.querySelector("#menu-toggle").addEventListener("click", () => {
  const open = document
    .querySelector(".sidebar")
    .classList.toggle("mobile-open");
  document.querySelector(".sidebar-scrim").hidden = !open;
  document
    .querySelector("#menu-toggle")
    .setAttribute("aria-expanded", String(open));
  if (open) document.querySelector(".nav-item.active").focus();
});
document
  .querySelector(".sidebar-scrim")
  .addEventListener("click", closeMobileNav);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeMobileNav();
  }
});

function uiDialog(
  message,
  { title = "确认操作", password = false, danger = false } = {},
) {
  const dialog = document.querySelector("#action-dialog");
  if (dialog.open) return Promise.resolve(false);
  const input = dialog.querySelector("#dialog-input");
  dialog.querySelector("#dialog-title").textContent = title;
  dialog.querySelector("#dialog-description").textContent = message;
  input.hidden = !password;
  input.required = password;
  input.value = "";
  dialog.querySelector("#dialog-label").hidden = !password;
  dialog.querySelector("#dialog-accept").classList.toggle("danger", danger);
  const previous = document.activeElement;
  return new Promise((resolve) => {
    let result = false;
    const form = dialog.querySelector("form");
    const submit = (e) => {
      e.preventDefault();
      if (password && !input.checkValidity()) {
        input.reportValidity();
        return;
      }
      result = password ? input.value : true;
      dialog.close();
    };
    const cancel = () => dialog.close();
    const done = () => {
      form.removeEventListener("submit", submit);
      dialog
        .querySelector("#dialog-cancel")
        .removeEventListener("click", cancel);
      input.value = "";
      previous?.focus();
      resolve(result);
    };
    form.addEventListener("submit", submit);
    dialog.querySelector("#dialog-cancel").addEventListener("click", cancel);
    dialog.addEventListener("close", done, { once: true });
    dialog.showModal();
    (password ? input : dialog.querySelector("#dialog-cancel")).focus();
  });
}
const uiConfirm = (message) =>
  uiDialog(message, { title: "请确认", danger: true });
const uiPrompt = (message) =>
  uiDialog(message, { title: "重置用户密码", password: true });

function goToPage(page) {
  document.querySelector(`.nav-item[data-page="${page}"]`)?.click();
}
document.addEventListener("click", (e) => {
  const go = e.target.closest("[data-go]");
  if (go) goToPage(go.dataset.go);
});
const reportDateControls = new Map();
function createReportDateFilter(id, onChange) {
  const host = document.getElementById(id);
  const options = [["all","全部日期"],["1w","近1周"],["1m","近1个月"],["3m","近3个月"],["6m","近6个月"],["12m","近12个月"],["custom","自定义日期"],["undated","日期未知"]];
  host.innerHTML = `<div class="field"><label for="${id}-period">报告日期</label><select id="${id}-period">${options.map(([value,label]) => `<option value="${value}">${label}</option>`).join("")}</select></div><div class="date-custom" hidden><div class="field"><label for="${id}-from">开始日期</label><input type="date" id="${id}-from"></div><div class="field"><label for="${id}-to">结束日期</label><input type="date" id="${id}-to"></div></div><span class="date-filter-status hint" role="status"></span>`;
  const select = host.querySelector("select"), from = host.querySelector('[id$="-from"]'), to = host.querySelector('[id$="-to"]');
  const status = host.querySelector(".date-filter-status");
  const state = {period:"all"};
  function change() {
    host.querySelector(".date-custom").hidden = select.value !== "custom";
    if (select.value === "custom" && !from.value && !to.value) {
      const today = new Intl.DateTimeFormat("en-CA", {timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
      from.value = today; to.value = today;
    }
    if (select.value === "custom" && (!from.value && !to.value || from.value && to.value && from.value > to.value)) {
      status.textContent = (from.value || to.value ? "开始日期不能晚于结束日期" : "请至少选择一个日期") + "，当前仍使用上次有效范围。";
      return;
    }
    status.textContent = select.value === "all" ? "含日期未知的报告" : select.value === "undated" ? "仅显示未确定报告日期的资料" : "按报告日期筛选";
    for (const key of Object.keys(state)) delete state[key];
    Object.assign(state, {period:select.value}, select.value === "custom" ? {date_from:from.value,date_to:to.value} : {});
    onChange();
  }
  select.addEventListener("change", change); from.addEventListener("change", change); to.addEventListener("change", change);
  const control = {params:()=>({...state}), reset:()=>{select.value="all";from.value="";to.value="";host.querySelector(".date-custom").hidden=true;Object.keys(state).forEach(key=>delete state[key]);state.period="all";status.textContent="含日期未知的报告";}};
  control.reset(); reportDateControls.set(id, control);
  return control;
}
function reportDateParams(id) { return reportDateControls.get(id)?.params() || {period:"all"}; }

function pageNumbers(current, total) {
  if (total <= 5) return Array.from({length:total}, (_, index)=>index+1);
  const numbers = [...new Set([1,total,current-1,current,current+1].filter(page=>page>=1 && page<=total))].sort((a,b)=>a-b);
  const result=[];
  for (const page of numbers) {if (result.length && page-result[result.length-1]>1) result.push("…"); result.push(page);}
  return result;
}
function renderPageNumbers(id, offset, limit, total, onPage) {
  const host = document.getElementById(id);
  const pages = Math.ceil(total / limit), current = pages ? Math.min(pages, Math.floor(offset/limit)+1) : 0;
  const hadFocus = host.contains(document.activeElement);
  host.classList.add("page-numbers");
  host.innerHTML = `<span class="page-summary">第${current}页，共${pages}页</span>` + pageNumbers(current,pages).map(page => page === "…" ? '<span aria-hidden="true">…</span>' : `<button type="button" class="btn small page-number" data-page="${page}" aria-label="第${page}页" ${page===current?'aria-current="page"':''}>${page}</button>`).join("");
  host.querySelectorAll("button").forEach(button=>button.onclick=()=>{if(Number(button.dataset.page)!==current)onPage(Number(button.dataset.page));});
  if (hadFocus) host.querySelector('[aria-current="page"]')?.focus({preventScroll:true});
}

function formatDate(value) {
  if (!value) return "—";
  // Legacy timestamps without an offset were local China time.
  let raw = String(value).replace(" ", "T");
  if (!/(Z|[+-]\d\d:\d\d)$/.test(raw)) raw += "+08:00";
  const date = new Date(raw);
  return Number.isNaN(date.getTime())
    ? "—"
    : new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(date);
}
function makeEmpty(title, description) {
  return `<div class="empty-state"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(description)}</span></div>`;
}
async function checkedResponse(res) {
  if (res.ok) return res;
  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    showLogin("会话已过期，请重新登录");
  }
  let detail;
  try {
    detail = (await res.json()).detail;
  } catch (_) {}
  throw new Error(
    typeof detail === "string"
      ? detail
      : res.status === 422
        ? "请检查填写的内容"
        : `请求失败（${res.status}），请稍后重试`,
  );
}
async function readEvents(res, onEvent) {
  const reader = res.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      let next;
      try {
        next = await reader.read();
      } catch (err) {
        if (err.name === "AbortError") throw err;
        throw new Error("与服务器的连接已中断，请到「研究历史」查看已保存的内容，再决定是否重试。");
      }
      const { done, value } = next;
      buffer += done
        ? decoder.decode()
        : decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      if (done && buffer.trim()) {
        lines.push(buffer);
        buffer = "";
      }
      for (const line of lines) {
        if (!line.trim()) continue;
        let ev;
        try {
          ev = JSON.parse(line);
        } catch (_) {
          throw new Error("报告传输中断，请重试");
        }
        if (ev.type === "error") throw new Error(ev.message || "生成未能完成");
        onEvent(ev);
      }
      if (done) break;
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
async function refreshAccount() {
  if (!currentUser) return;
  try {
    currentUser = await api("/api/auth/me");
    setCurrentUserUI();
  } catch (_) {}
}
async function loadRecentWork() {
  try {
    const data = await api("/api/history?limit=3");
    const box = document.querySelector("#recent-work");
    box.hidden = !data.items?.length;
    document.querySelector("#recent-items").innerHTML = (data.items || [])
      .map(
        (it) =>
          `<button class="recent-item" data-history="${escapeHtml(it.id)}"><strong>${escapeHtml(it.arguments || it.skill_title)}</strong><span>${escapeHtml(it.skill_title)} · ${formatDate(it.created_at)}</span></button>`,
      )
      .join("");
    document
      .querySelectorAll("[data-history]")
      .forEach(
        (btn) =>
          (btn.onclick = () =>
            openHistory(btn.dataset.history).catch((err) =>
              toast(err.message, "bad"),
            )),
      );
  } catch (_) {}
}
async function loadAccount() {
  const root = document.querySelector("#account-overview");
  root.innerHTML = loadingLine("加载账户…");
  try {
    const data = await api("/api/account/usage");
    const q = data.quota;
    currentUser.quota = q;
    setCurrentUserUI();
    const role = currentUser.is_root_admin
      ? "初始管理员"
      : currentUser.is_admin
        ? "管理员"
        : TIER_LABELS[currentUser.tier] || "普通用户";
    root.innerHTML = `<div class="stat-card"><span class="stat-label">${escapeHtml(currentUser.username)}</span><strong>${escapeHtml(role)}</strong><small>角色与会员等级独立管理</small></div><div class="stat-card"><span class="stat-label">今日已用</span><strong>${q.used}<span class="muted"> / ${q.quota || "不限"}</span></strong><small>含进行中的任务</small></div><div class="stat-card"><span class="stat-label">今日剩余</span><strong>${q.remaining === null ? "不限" : q.remaining}</strong><small>${escapeHtml(q.timezone || "Asia/Shanghai")} 每日零点恢复</small></div>`;
    const actions = registrationPolicy.platform?.usage_actions || {
      research: "新建研究",
      pipeline: "研究流水线",
      memory: "研究记忆问答",
      extract: "深度结论抽取",
    };
    const states = {
      running: "进行中 · 已预留",
      completed: "已计次",
      failed: "未计次",
    };
    document.querySelector("#usage-list").innerHTML = data.items.length
      ? data.items
          .map(
            (it) =>
              `<div class="usage-row"><b>${escapeHtml(actions[it.action] || "操作")}</b><span>${states[it.status] || "已结束"}</span><span>${formatDate(it.created_at)}</span></div>`,
          )
          .join("")
      : makeEmpty("还没有研究用量", "从研究工作台开始你的第一份报告。");
    await loadInvites();
  } catch (err) {
    root.innerHTML = makeEmpty("账户信息加载失败", err.message);
  }
}
async function loadInvites() {
  const data = await api("/api/invite/list");
  document.querySelector("#invite-list").innerHTML = data.codes.length
    ? data.codes
        .map(
          (it) =>
            `<div class="invite-row"><div><code>${escapeHtml(it.code)}</code><small>${it.used ? "已使用" : it.expired ? "已过期" : `有效至 ${formatDate(it.expires_at)}`}</small></div>${!it.used && !it.expired ? `<button class="btn small" data-invite="${escapeHtml(it.code)}">复制链接</button>` : ""}</div>`,
        )
        .join("")
    : makeEmpty("和伙伴一起研究", "生成一个邀请链接，有效期由平台策略配置。");
  document.querySelectorAll("[data-invite]").forEach(
    (button) =>
      (button.onclick = async () => {
        const url = new URL("/", location.origin);
        url.searchParams.set("invite", button.dataset.invite);
        try {
          await navigator.clipboard.writeText(url.href);
          toast("邀请链接已复制", "ok");
        } catch (_) {
          toast("复制失败，请手动复制推荐码", "bad");
        }
      }),
  );
}
document
  .querySelector("#quota-pill")
  .addEventListener("click", () => goToPage("account"));
document.querySelector("#btn-invite").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try {
    await api("/api/invite/generate", { method: "POST" });
    await loadInvites();
    toast("邀请已生成，点击复制链接分享", "ok");
  } catch (err) {
    toast(err.message, "bad");
  } finally {
    btn.disabled = false;
  }
});
document
  .querySelector("#password-form")
  .addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.currentTarget,
      btn = form.querySelector("button");
    if (
      document.querySelector("#new-password").value !==
      document.querySelector("#confirm-password").value
    ) {
      toast("两次新密码不一致", "bad");
      return;
    }
    btn.disabled = true;
    try {
      await api("/api/account/password", {
        method: "PUT",
        body: JSON.stringify({
          old_password: document.querySelector("#old-password").value,
          new_password: document.querySelector("#new-password").value,
        }),
      });
      form.reset();
      localStorage.removeItem(TOKEN_KEY);
      location.reload();
    } catch (err) {
      toast(err.message, "bad");
    } finally {
      btn.disabled = false;
    }
  });
document.querySelector("#policy-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.currentTarget.querySelector("button");
  btn.disabled = true;
  try {
    await api("/api/admin/account-policy", {
      method: "PUT",
      body: JSON.stringify({
        quotas: Object.fromEntries([...document.querySelectorAll('[data-tier-quota]')].map(input=>[input.dataset.tierQuota, +input.value])),
        invite_required: document.querySelector("#policy-invite").checked,
      }),
    });
    toast("规则已保存", "ok");
    await refreshAccount();
  } catch (err) {
    toast(err.message, "bad");
  } finally {
    btn.disabled = false;
  }
});

// Report dialog: explicit reading controls, keyboard focus, copy and export.
let readerSource = "",
  readerPrevious = null;
function showReportReader(source) {
  exitFs();
  readerSource = source;
  readerPrevious = document.activeElement;
  const modal = document.querySelector("#report-modal");
  modal.classList.add("open");
  document.body.classList.add("has-modal");
  modal.querySelector(".modal-box").focus();
  modal.querySelector(".reader-body").scrollTop = 0;
  modal.querySelector(".reader-toc")?.remove();
  const headings = Array.from(
    modal.querySelectorAll(".markdown h2,.markdown h3"),
  );
  if (headings.length > 1) {
    const toc = document.createElement("nav");
    toc.className = "reader-toc";
    toc.setAttribute("aria-label", "报告目录");
    headings.forEach((heading, i) => {
      heading.id = `report-heading-${i}`;
      const link = document.createElement("a");
      link.href = "#" + heading.id;
      link.textContent = heading.textContent;
      link.onclick = (e) => {
        e.preventDefault();
        heading.scrollIntoView({ behavior: "smooth", block: "start" });
      };
      toc.appendChild(link);
    });
    modal.querySelector(".markdown").before(toc);
  }
}
function closeReportReader() {
  const modal = document.querySelector("#report-modal");
  if (!modal.classList.contains("open")) return;
  exitFs();
  modal.classList.remove("open");
  document.body.classList.remove("has-modal");
  readerPrevious?.focus();
}
document.querySelector("#reader-copy").onclick = async () => {
  try {
    await navigator.clipboard.writeText(readerSource);
    toast("报告已复制", "ok");
  } catch (_) {
    toast("复制失败，请手动选择正文", "bad");
  }
};
function downloadMarkdown(content, title) {
  const url = URL.createObjectURL(
    new Blob([content], { type: "text/markdown;charset=utf-8" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = title.replace(/[\\/:*?"<>|]/g, "-").slice(0, 90) + ".md";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
document.querySelector("#reader-download").onclick = () =>
  downloadMarkdown(
    readerSource,
    document.querySelector("#modal-title").textContent,
  );
document.querySelector("#reader-focus").onclick = () => {
  if (document.querySelector("#report-modal .modal-box.fullscreen")) exitFs();
  else enterFs(document.querySelector("#modal-content"));
};
document.addEventListener("keydown", (e) => {
  const modal = document.querySelector("#report-modal");
  if (
    !modal.classList.contains("open") ||
    document.querySelector("#action-dialog").open
  )
    return;
  if (e.key === "Tab") {
    const nodes = Array.from(
      modal.querySelectorAll("button,a[href],input,select,textarea"),
    ).filter((n) => !n.disabled && n.getClientRects().length);
    const first = nodes[0],
      last = nodes[nodes.length - 1];
    if (
      e.shiftKey &&
      (document.activeElement === first ||
        document.activeElement === modal.querySelector(".modal-box"))
    ) {
      last?.focus();
      e.preventDefault();
    } else if (!e.shiftKey && document.activeElement === last) {
      first?.focus();
      e.preventDefault();
    }
  }
});
// Keep all tabular research views inside their own scroll region on small screens.
document.querySelectorAll(".concl-table").forEach((table) => {
  if (table.parentElement.classList.contains("table-scroll")) return;
  const wrapper = document.createElement("div");
  wrapper.className = "table-scroll";
  wrapper.tabIndex = 0;
  wrapper.setAttribute("aria-label", "研究数据表，可横向滚动");
  table.before(wrapper);
  wrapper.appendChild(table);
});
