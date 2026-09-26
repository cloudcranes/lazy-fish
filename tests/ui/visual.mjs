// 视觉回归：Playwright 拉五个视图截图，与 tests/ui/baselines/*.png 比对；
// pixelmatch 阈值 0.1% diff（≤ 0.001 的比例视为像素差异）。
//
// 退出码契约（PR-20 重申，与 a11y.mjs §11 一致）：
//   - 0：5 视图全部 ≤ 阈值（含 BASELINE_CREATED / BASELINE_UPDATED）。
//   - 1：有视图 FAIL（ratio > 0.001）或 SIZE_MISMATCH。
//   - 2：环境不可用（端口未通 / docker-up 健康检查超时 / npm ci / playwright install 失败等）。
//
// 设计要点：
//   - 与 a11y.mjs 共用同一套视图枚举与 hash 切换时序，保证"视图-视角"一致：
//     console / plans / templates / capture / logs 五张图，覆盖首屏与 4 个二级视图。
//   - 视口锁 1280×900（设计断点 ≥1081px 的完整双栏形态，桌面回归基线）。
//     移动端标签栏在 smoke.mjs 里另行覆盖，这里不重复采样。
//   - 截图在 `networkidle` 之后、且对应 #view-X.is-active 出现后再拍，
//     与 a11y.mjs 的"给一次微任务 + 视图淡入动画（--dur-slow=280ms）的余量"同源。
//   - Baseline 目录 `tests/ui/baselines/` 内 PNG 与 view 名一一对应：
//     console.png / plans.png / templates.png / capture.png / logs.png。
//     缺失即视为新增，生成 baseline 写入后视为 pass；存在但内容不同，按像素差
//     占整图比例 > 阈值 → fail。
//   - 阈值 0.001（0.1%）：容忍极少量像素抖动（动画残留、状态点呼吸），挡得住
//     真实改动（字号 / 间距 / 颜色变化必然 > 1%）。阈值不是"完美比对"，是
//     "样式层实质变更"探测。
//   - 浏览器锁定 playwright chromium 主线版 1.49.x（仓根 package.json devDeps
//     `playwright: ^1.49.0`），收敛跨平台字体差异——避免在 Ubuntu runner 上
//     因字体子像素抗锯齿漂移刷出零星 diff，让 0.1% 阈值稳定可重复。
//   - 第一次运行（baseline 缺失）会**生成** baseline 并视为通过 —— 与
//     docs/UI-DESIGN.md §12「baseline 缺失 = 初始化」一致。后续 PR 应带 baseline。
//     维护者如需重生成，传递 `--update-baseline` 强制覆盖。
//   - 合并门槛（PR-20 起）：CI 上 visual job 已无 `continue-on-error`，PR + main
//     均 fail-fast；baseline 变动必须走 PR + 人工审 diff 图，禁直推 main 重生成。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { PNG } from "pngjs";
import pixelmatch from "pixelmatch";
import fse from "fs-extra";

const URL = process.env.APP_URL || "http://127.0.0.1:8999/";
const VIEWS = ["console", "plans", "templates", "capture", "logs"];
// 0.001 = 0.1% 像素差异。低于此值视为可接受的渲染抖动，高于即视为样式层实质变更。
const DIFF_RATIO_THRESHOLD = 0.001;

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASELINE_DIR = path.join(__dirname, "baselines");
const DIFF_DIR = path.join(__dirname, "diffs");

const UPDATE_BASELINE = process.argv.includes("--update-baseline");

fse.ensureDirSync(BASELINE_DIR);
fse.ensureDirSync(DIFF_DIR);

const summary = [];
let browser;
try {
  browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    deviceScaleFactor: 1,
  });
  const page = await ctx.newPage();
  // 用 domcontentloaded 而非 networkidle：前端每秒轮询 /api/tasks/state，
  // networkidle 在轮询下永不达成（视觉回归只关心首屏渲染，不关心网络静默）。
  await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 30000 });

  for (const view of VIEWS) {
    // hash 路由切换（与 a11y.mjs 一致），再等视图淡入 + 状态 active
    await page.evaluate((v) => {
      location.hash = "#" + v;
    }, view);
    await page.waitForTimeout(400);
    await page.waitForSelector(`#view-${view}.is-active`, { timeout: 5000 });

    // 再给一次微任务 + 任何 async render，让 lazy 模板/状态环的最后一帧落定
    await page.waitForTimeout(200);

    const actual = await page.screenshot({ type: "png", fullPage: false });
    const actualPath = path.join(DIFF_DIR, `${view}.actual.png`);
    fs.writeFileSync(actualPath, actual);

    const baselinePath = path.join(BASELINE_DIR, `${view}.png`);
    if (!fs.existsSync(baselinePath)) {
      fs.writeFileSync(baselinePath, actual);
      summary.push({ view, status: "BASELINE_CREATED", ratio: 0, diffPixels: 0 });
      console.log(`[visual] ${view} baseline created (first run)`);
      continue;
    }

    if (UPDATE_BASELINE) {
      fs.writeFileSync(baselinePath, actual);
      summary.push({ view, status: "BASELINE_UPDATED", ratio: 0, diffPixels: 0 });
      console.log(`[visual] ${view} baseline updated (--update-baseline)`);
      continue;
    }

    const baseline = fs.readFileSync(baselinePath);
    const a = PNG.sync.read(baseline);
    const b = PNG.sync.read(actual);

    if (a.width !== b.width || a.height !== b.height) {
      summary.push({
        view,
        status: "SIZE_MISMATCH",
        ratio: 1,
        diffPixels: a.width * a.height,
        detail: `baseline ${a.width}x${a.height} vs actual ${b.width}x${b.height}`,
      });
      console.error(
        `[visual] ${view} SIZE MISMATCH: baseline ${a.width}x${a.height} vs actual ${b.width}x${b.height}`
      );
      continue;
    }

    const diff = new PNG({ width: a.width, height: a.height });
    const diffPixels = pixelmatch(
      a.data,
      b.data,
      diff.data,
      a.width,
      a.height,
      { threshold: 0 }
    );
    const totalPixels = a.width * a.height;
    const ratio = totalPixels === 0 ? 0 : diffPixels / totalPixels;
    const diffPath = path.join(DIFF_DIR, `${view}.diff.png`);
    fs.writeFileSync(diffPath, PNG.sync.write(diff));

    const status = ratio > DIFF_RATIO_THRESHOLD ? "FAIL" : "PASS";
    summary.push({ view, status, ratio, diffPixels, totalPixels });
    console.log(
      `[visual] ${view} ${status} ratio=${(ratio * 100).toFixed(3)}% diff=${diffPixels}/${totalPixels}`
    );
  }
} catch (err) {
  console.error("[visual] 环境不可用：", err && err.message ? err.message : err);
  console.error("提示：在 CI 上由 docker-up job 起 lazy-fish 后才会跑；本地需 docker compose up lazy-fish。");
  if (browser) await browser.close().catch(() => {});
  process.exit(2);
}

await browser.close();

const failed = summary.filter((s) => s.status === "FAIL" || s.status === "SIZE_MISMATCH");
const created = summary.filter((s) => s.status === "BASELINE_CREATED");
const updated = summary.filter((s) => s.status === "BASELINE_UPDATED");
console.log(
  `\n[visual] summary: ${summary.length} views, fail=${failed.length}, baseline_created=${created.length}, baseline_updated=${updated.length}, threshold=${(DIFF_RATIO_THRESHOLD * 100).toFixed(3)}%`
);
process.exit(failed.length > 0 ? 1 : 0);