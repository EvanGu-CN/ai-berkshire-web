/* Local files remain in the browser until the user selects them; previews never save reports. */
"use strict";
let reportImportEntries = [];
let reportImportBusy = false;
let reportImportOwnerId = "";
const reportImportDialog = $("#report-import-dialog");
const importStatusLabels = {ready:"待导入", imported:"已导入", duplicate:"重复跳过", skipped:"已跳过", error:"需检查"};

function reportImportSummary() {
  const counts = {};
  for (const entry of reportImportEntries) counts[entry.row.status] = (counts[entry.row.status] || 0) + 1;
  const ready = reportImportEntries.filter(e => e.row.status === "ready");
  const selected = ready.filter(e => e.selected).length;
  $("#report-import-summary").textContent = reportImportEntries.length
    ? `待导入 ${selected} · 已导入 ${counts.imported || 0} · 重复 ${counts.duplicate || 0} · 跳过 ${counts.skipped || 0} · 问题 ${counts.error || 0}`
    : "选择后显示导入预览";
  $("#report-import-confirm").disabled = reportImportBusy || !selected;
  $("#report-import-confirm").textContent = reportImportBusy ? "处理中…" : `确认导入${selected ? ` ${selected} 份` : ""}`;
  $("#report-import-all").disabled = reportImportBusy || !ready.length;
  $("#report-import-all").checked = !!selected && selected === ready.length;
  $("#report-import-all").indeterminate = selected > 0 && selected < ready.length;
}

function renderReportImportEntries() {
  const list = $("#report-import-list");
  list.innerHTML = reportImportEntries.map((entry, index) => {
    const row = entry.row;
    return `<label class="import-row"><input type="checkbox" data-import-index="${index}" ${entry.selected ? "checked" : ""} ${reportImportBusy || row.status !== "ready" ? "disabled" : ""} aria-label="导入 ${escapeHtml(row.title || row.path)}"><span class="import-row-body"><strong>${escapeHtml(row.title || row.path)}</strong><span>${escapeHtml([row.group, row.type, row.date || (row.title ? "日期未知" : "")].filter(Boolean).join(" · "))}</span><span class="import-source-path">${escapeHtml(row.path)}</span>${row.reason ? `<span class="${row.status === "error" ? "bad" : "hint"}">${escapeHtml(row.reason)}</span>` : ""}</span><span class="import-status ${row.status === "error" ? "bad" : row.status === "imported" ? "ok" : ""}">${importStatusLabels[row.status] || row.status}</span></label>`;
  }).join("") || '<p class="hint">没有找到可导入的 Markdown 报告。</p>';
  reportImportSummary();
}

function setReportImportBusy(busy) {
  reportImportBusy = busy;
  for (const id of ["report-import-folder", "report-import-files", "report-import-close"]) $("#" + id).disabled = busy;
  renderReportImportEntries();
}

function reportImportBatches(entries) {
  const batches = [];
  let batch = [], bytes = 0;
  for (const entry of entries) {
    const size = new TextEncoder().encode(JSON.stringify(entry.doc)).length;
    if (batch.length && (batch.length >= 64 || bytes + size > 6 * 1024 * 1024)) {
      batches.push(batch); batch = []; bytes = 0;
    }
    batch.push(entry); bytes += size;
  }
  if (batch.length) batches.push(batch);
  return batches;
}

function reportImportRelativePath(file) {
  return file.webkitRelativePath ? file.webkitRelativePath.split("/").slice(1).join("/") : file.name;
}

async function previewReportImport(files) {
  if (reportImportBusy || !files.length) return;
  reportImportEntries = [];
  setReportImportBusy(true);
  $("#report-import-selection").textContent = `${files.length} 个文件`;
  $("#report-import-progress").textContent = "正在读取本机文件…";
  try {
    const totalBytes = files.filter(file => /\.(md|markdown|json)$/i.test(file.name)).reduce((sum, file) => sum + file.size, 0);
    if (totalBytes > 200 * 1024 * 1024 || files.length > 10000) throw Error("请分批选择文件，每次最多 200 MiB、10000 个文件");
    const indexFile = files.find(file => reportImportRelativePath(file).toLowerCase() === "index.json");
    const metadata = new Map();
    let indexWarning = "";
    if (indexFile && indexFile.size <= 4 * 1024 * 1024) {
      try {
        const index = JSON.parse(await indexFile.text());
        for (const item of Array.isArray(index.reports) ? index.reports : []) {
          if (typeof item.path !== "string") continue;
          const fields = {};
          for (const [key, max] of [["title",400],["group",120],["type",100],["date",64]]) {
            if (typeof item[key] === "string") fields[key] = item[key].slice(0, max);
          }
          metadata.set(item.path.replaceAll("\\", "/").replace(/^reports\//i, ""), fields);
        }
      } catch (_) { indexWarning = "index.json 无法解析，已按正文和目录识别。"; }
    }
    let ignored = 0;
    const orderedFiles = [...files].sort((a, b) => Number(metadata.has(reportImportRelativePath(b))) - Number(metadata.has(reportImportRelativePath(a))));
    for (const file of orderedFiles) {
      const relative = reportImportRelativePath(file);
      if (!/\.(md|markdown)$/i.test(file.name)) {ignored++; continue;}
      let row = {path:relative, status:"error", reason:"尚未完成预览"};
      let doc = null;
      if (relative.split("/").some(part => part.startsWith(".")) || /^readme\.(md|markdown)$/i.test(file.name)) row = {...row, status:"skipped", reason:"目录说明或隐藏文件"};
      else if (file.size > 4 * 1024 * 1024) row.reason = "单份报告不能超过 4 MiB";
      else if (relative.length > 600) row.reason = "文件相对路径过长";
      else {
        try { doc = {path:relative, content:await file.text(), metadata:metadata.get(relative) || {}}; }
        catch (_) { row.reason = "无法读取此文件，请重新选择"; }
      }
      reportImportEntries.push({doc, row, selected:false});
    }
    const seen = new Set();
    const candidates = reportImportEntries.filter(entry => entry.doc);
    let processed = 0;
    for (const batch of reportImportBatches(candidates)) {
      $("#report-import-progress").textContent = `正在预览 ${processed} / ${candidates.length} 份…`;
      const data = await api("/api/reports/import/preview", {method:"POST", body:JSON.stringify({items:batch.map(e => e.doc)})});
      batch.forEach((entry, index) => {
        entry.row = data.items[index];
        if (entry.row.status === "ready") {
          const identity = entry.row.content_hash;
          if (seen.has(identity)) entry.row = {...entry.row, status:"duplicate", reason:"所选文件中有相同正文（不受路径影响）"};
          else { seen.add(identity); entry.selected = true; }
        }
      });
      processed += batch.length;
    }
    $("#report-import-progress").textContent = `${indexWarning}预览完成${ignored ? `，已忽略 ${ignored} 个非 Markdown 文件` : ""}。确认后全站用户可阅读，只有导入者可删除。`;
  } catch (err) {
    $("#report-import-progress").textContent = "预览未完成：" + err.message + "。可重新选择文件重试。";
    // Unchecked documents must not become importable after a failed preview.
  } finally { setReportImportBusy(false); }
}

$("#btn-report-import").addEventListener("click", () => {
  if (reportImportOwnerId !== currentUser?.id) {
    reportImportOwnerId = currentUser?.id || "";
    reportImportEntries = [];
    $("#report-import-progress").textContent = "";
    $("#report-import-selection").textContent = "尚未选择文件";
    for (const id of ["report-import-directory-input", "report-import-files-input"]) $("#" + id).value = "";
    renderReportImportEntries();
  }
  reportImportDialog.showModal();
  $("#report-import-folder").focus();
});
$("#report-import-close").addEventListener("click", () => reportImportDialog.close());
reportImportDialog.addEventListener("cancel", e => {if (reportImportBusy) e.preventDefault();});
reportImportDialog.addEventListener("close", () => $("#btn-report-import").focus());
for (const [button, input] of [["report-import-folder", "report-import-directory-input"], ["report-import-files", "report-import-files-input"]]) {
  $("#" + button).addEventListener("click", () => {$("#" + input).value = ""; $("#" + input).click();});
  $("#" + input).addEventListener("change", e => previewReportImport(Array.from(e.target.files || [])));
}
$("#report-import-list").addEventListener("change", e => {
  if (!e.target.matches("[data-import-index]")) return;
  reportImportEntries[Number(e.target.dataset.importIndex)].selected = e.target.checked;
  reportImportSummary();
});
$("#report-import-all").addEventListener("change", e => {
  for (const entry of reportImportEntries) if (entry.row.status === "ready") entry.selected = e.target.checked;
  renderReportImportEntries();
});
$("#report-import-confirm").addEventListener("click", async () => {
  if (reportImportBusy) return;
  const selected = reportImportEntries.filter(entry => entry.selected && entry.row.status === "ready");
  if (!selected.length) return;
  setReportImportBusy(true);
  let saved = false;
  try {
    let processed = 0;
    for (const batch of reportImportBatches(selected)) {
      $("#report-import-progress").textContent = `正在导入 ${processed} / ${selected.length} 份，请稍候…`;
      const data = await api("/api/reports/import", {method:"POST", body:JSON.stringify({items:batch.map(e => e.doc)})});
      saved = true;
      batch.forEach((entry, index) => {entry.row = data.items[index]; entry.selected = false;});
      processed += batch.length;
      renderReportImportEntries();
    }
    $("#report-import-progress").textContent = "导入完成。关闭后可在「本地导入 → 只看我的」阅读、复制和下载。";
  } catch (err) {
    $("#report-import-progress").textContent = "导入中断：" + err.message + "。已完成的报告已保留，可重试剩余报告，相同正文会自动去重，不受路径影响。";
  } finally {
    setReportImportBusy(false);
    if (saved) {
      reportSource = "imported"; reportScope = "mine"; reportOffset = 0;
      for (const id of ["report-search", "report-group", "report-type"]) $("#" + id).value = "";
      $$("#report-source-chips .chip").forEach(chip => chip.classList.toggle("active", chip.dataset.source === "imported" || chip.dataset.scope === "mine"));
      try { await loadReports(); } catch (err) { toast("报告已保存，列表刷新失败：" + err.message, "bad"); }
    }
  }
});
