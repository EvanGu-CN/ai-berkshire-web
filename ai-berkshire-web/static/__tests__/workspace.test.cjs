const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");
const app = fs.readFileSync(path.join(__dirname, "../app.js"), "utf8");
const workspace = fs.readFileSync(
  path.join(__dirname, "../workspace.js"),
  "utf8",
);
const context = vm.createContext({ TextDecoder, Error });
vm.runInContext(workspace.slice(workspace.indexOf("function pageNumbers"), workspace.indexOf("function formatDate")), context);
vm.runInContext(
  app.slice(
    app.indexOf("function renderMarkdown"),
    app.indexOf("/* ================= 全局状态"),
  ),
  context,
);
vm.runInContext(
  workspace.slice(
    workspace.indexOf("async function readEvents"),
    workspace.indexOf("async function refreshAccount"),
  ),
  context,
);

test("markdown rejects script URLs and escapes active content and attributes", () => {
  const html = context.renderMarkdown(
    '[click](javascript:alert(1))\n[quote](https://example.com/"onmouseover="bad)\n<img src=x onerror=bad>',
  );
  assert.ok(!html.includes('href="javascript:'));
  assert.ok(!html.includes("<img"));
  assert.ok(!html.includes(' onmouseover="bad'));
  assert.ok(html.includes("&lt;img"));
});
test("safe links, tables and code remain readable", () => {
  const html = context.renderMarkdown(
    "[source](https://example.com)\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```js\n<script>\n```",
  );
  assert.ok(html.includes('rel="noopener noreferrer"'));
  assert.ok(html.includes("<table>"));
  assert.ok(html.includes("&lt;script&gt;"));
});

test("standard page numbers include current page and bounded endpoints", () => {
  assert.deepEqual(Array.from(context.pageNumbers(1, 0)), []);
  assert.deepEqual(Array.from(context.pageNumbers(1, 2)), [1, 2]);
  assert.deepEqual(Array.from(context.pageNumbers(5, 10)), [1, "…", 4, 5, 6, "…", 10]);
  assert.deepEqual(Array.from(context.pageNumbers(10, 10)), [1, "…", 9, 10]);
});
function stream(chunks) {
  let i = 0;
  return {
    body: {
      getReader() {
        return {
          async read() {
            return i < chunks.length
              ? { done: false, value: chunks[i++] }
              : { done: true };
          },
          async cancel() {},
          releaseLock() {},
        };
      },
    },
  };
}
test("NDJSON handles split Unicode characters and a final event without newline", async () => {
  const bytes = new TextEncoder().encode(
    '{"type":"delta","text":"中文"}\n{"type":"done"}',
  );
  const events = [];
  await context.readEvents(
    stream([bytes.slice(0, 25), bytes.slice(25, 27), bytes.slice(27)]),
    (e) => events.push(e),
  );
  assert.equal(events[0].text, "中文");
  assert.equal(events[1].type, "done");
});
test("stream errors propagate and cannot become successful completion", async () => {
  const bytes = new TextEncoder().encode(
    '{"type":"error","message":"failed"}\n{"type":"done"}\n',
  );
  const events = [];
  await assert.rejects(
    context.readEvents(stream([bytes]), (e) => events.push(e)),
    /failed/,
  );
  assert.equal(events.length, 0);
});
test("malformed events fail clearly", async () => {
  await assert.rejects(
    context.readEvents(
      stream([new TextEncoder().encode("broken\n")]),
      () => {},
    ),
    /报告传输中断/,
  );
});

test("idle heartbeat lines never become content or completion", async () => {
  const events = [];
  await context.readEvents(stream([new TextEncoder().encode('\n\n{"type":"delta","text":"报告"}\n\n{"type":"done"}\n')]), e => events.push(e));
  assert.equal(events.length, 2);
  assert.equal(events[0].text, "报告");
  assert.equal(events[1].type, "done");
});

test("transport failure explains history recovery and preserves user cancellation", async () => {
  const broken = (err) => ({ body: { getReader: () => ({
    async read() { throw err; }, async cancel() {}, releaseLock() {},
  }) } });
  await assert.rejects(context.readEvents(broken(new TypeError("network error")), () => {}), /连接已中断.*研究历史/);
  const abort = new Error("stopped");
  abort.name = "AbortError";
  await assert.rejects(context.readEvents(broken(abort), () => {}), err => err === abort);
});
