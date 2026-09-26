// 自动 a11y 自检：Playwright + @axe-core/playwright 驱动内置 Chromium，
// 对 5 个视图依次执行 axe-core，serious/critical 视作用例失败。
//
// 设计要点：
//   - 复用 tests/ui/ 下"纯 CDP/Node 内置"的协议思路，但这次用 Playwright
//     只为拿到 axe-core 的页面快照；其他 .mjs 仍是裸 mjs，零共享代码。
//   - 5 个视图对应 nav 按钮（console / plans / templates / capture / logs），
//     每个视图先点击切换、等一次微任务，再 inject axe-core 跑分析。
//   - 默认 fail-on：serious / critical；moderate / minor 仅打印不阻塞。
//     这与 docs/UI-DESIGN.md §6「WCAG AA」保持一致——contrast / keyboard
//     / aria 之类的真问题都在 serious/critical 里。
//   - 退出码：0=全部通过；1=发现 serious/critical；2=环境起不来（端口未通）。
import { chromium } from "playwright";
import { AxeBuilder } from "@axe-core/playwright";

const URL = process.env.APP_URL || "http://127.0.0.1:8999/";
const VIEWS = ["console", "plans", "templates", "capture", "logs"];
const FAIL_ON = new Set(["serious", "critical"]);

const summary = [];

let browser;
try {
  browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const ctx = await browser.newContext();
  const page = await ctx.newPage();

  // 视图切换先走一次：让 SPA 完成首屏路由与 lazy 模板载入
  await page.goto(URL, { waitUntil: "networkidle", timeout: 30000 });

  for (const view of VIEWS) {
    // 通过 hash 路由切换（与 smoke.mjs 的 "hash routing works on load" 同一套机制）
    await page.evaluate((v) => {
      location.hash = "#" + v;
    }, view);
    // 给一次微任务 + 视图淡入动画（design token --dur-slow=280ms）的余量
    await page.waitForTimeout(400);
    await page.waitForSelector(`#view-${view}.is-active`, { timeout: 5000 });

    const results = await new AxeBuilder({ page })
      // 5 个视图里有用户提交表单 / 自定义控件，把"实验规则"关掉避免误报；
      // 保留 wcag2a/2aa 是底线。
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();

    const blocking = results.violations.filter((v) => FAIL_ON.has(v.impact));
    const total = results.violations.length;
    summary.push({
      view,
      total,
      serious: results.violations.filter((v) => v.impact === "serious").length,
      critical: results.violations.filter((v) => v.impact === "critical").length,
      blocking: blocking.map((v) => `${v.id} (${v.impact}) → ${v.nodes.length} node(s)`),
    });

    if (blocking.length) {
      console.error(`\n[a11y] ${view} FAIL:`);
      for (const v of blocking) {
        console.error(`  - ${v.id} [${v.impact}] ${v.help}`);
        for (const n of v.nodes.slice(0, 3)) {
          console.error(`    target: ${n.target.join(" ")}`);
          console.error(`    html:   ${n.html.slice(0, 160)}`);
        }
      }
    } else {
      console.log(`[a11y] ${view} ok (total=${total})`);
    }
  }
} catch (err) {
  console.error("[a11y] 环境不可用：", err && err.message ? err.message : err);
  console.error("提示：在 CI 上由 docker-up job 起 lazy-fish 后才会跑；本地需 docker compose up lazy-fish。");
  if (browser) await browser.close().catch(() => {});
  process.exit(2);
}

await browser.close();

const totalSerious = summary.reduce((n, s) => n + s.serious, 0);
const totalCritical = summary.reduce((n, s) => n + s.critical, 0);
console.log(
  `\n[a11y] summary: ${summary.length} views, serious=${totalSerious}, critical=${totalCritical}`
);
process.exit(totalSerious + totalCritical > 0 ? 1 : 0);