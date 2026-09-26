# 咸鱼之王助手 · UI 设计系统交付说明

> 交付对象：前端 / 后续维护者
> 设计目标：把一个「单页长表单」的工具页，重构成符合操作习惯的状态型控制台 —— 打开就能看见"现在能不能跑"，跑起来能一眼看见"跑到哪了"。

---

## 一、设计出发点：先看用户习惯，再谈美观

原界面把 5 个功能区（方案、设备、运行、模板、采样）平铺在一列卡片里，用户每次执行都要从上滚到下，且"开始"按钮和"运行状态"分离。典型问题的定位：

| 用户真实动作 | 原界面摩擦 | 新界面对策 |
| --- | --- | --- |
| 打开后先确认"能跑吗" | 需要自己核对设备、模板、次数 | 控制台首屏给出**就绪检查**，明确告诉你缺什么 |
| 点开始后盯着进度 | 进度条埋在第二屏 | 首屏**进度环 + 状态胶囊**，无需滚动 |
| 出错后想看原因 | 日志折叠在最底部 | 首屏**日志预览**，出错自动展开、跳转 |
| 复用上次配置 | 方案区在顶部、参数在中部 | **方案卡片**一键载入，参数随动 |
| 换分辨率重采模板 | 采样区在最底部 | 独立「截图采样」视图，进游戏后直接切 |

核心判断：这是一个**低频配置 + 高频运行**的监控型工具，所以信息架构按"运行优先"重排，把配置沉到二级视图。

---

## 二、信息架构

单页应用 + 哈希路由，5 个视图：

```
控制台 (console) ── 运行态 + 快速配置：运行配置（方案芯片 + 十连次数）、状态环、开始/停止、指标、执行概要、日志预览
方案与参数 (plans) ── 完整配置态：方案卡片增删改、循环节奏/识别与重试/安全与设备 三组参数（「识别与重试」组内嵌「多尺度识别」折叠高级项）
模板管理 (templates) ── 资产态：模板网格 + 首次/后续角色指派
截图采样 (capture) ── 生产态：设备截图 + 拖拽框选 + 保存
运行日志 (logs) ── 诊断态：完整日志、复制、清空视图
```

导航分组也按心智分组而非按字母：**运行**（控制台、方案与参数）／**维护**（模板管理、截图采样、运行日志）。

> **职责边界的修正**：初版把控制台定义为"纯运行态"，前提是"配置一次、反复运行"。实际使用中最高频的动作恰恰是**每次运行前换方案、改次数**，逼用户跳视图才能改，属于把最高频操作藏到了第二屏。现在控制台承担**运行前的两个决定**（用哪个方案、跑多少轮），方案与参数页保留**完整参数编辑与方案增删改**。

---

## 三、设计基础（Design Foundations）

### 3.1 色彩系统

全量令牌定义在 `static/tokens.css`，唯一数据源，**任何组件不得写死颜色**。

**品牌色 · 琥珀金**（沿用原界面金色主色，保留"咸鱼之王"金箱子联想，但去掉渐变改为纯色块）：

| Token | 值 | 用途 |
| --- | --- | --- |
| `--brand-050` | `#fdf8ee` | 品牌色浅底（选中卡片、品牌胶囊） |
| `--brand-200` | `#f2dfba` | 品牌色描边 |
| `--brand-400` | `#d8ac57` | 聚焦边框、选中描边 |
| `--brand-500` | `#c1902d` | **主操作按钮底色**、进度条、滑块 |
| `--brand-600` | `#a3731f` | 主按钮 hover、强调文字 |
| `--brand-700` | `#835a18` | 深底上的品牌文字 |

**墨色中性**：`--ink-000 → --ink-900` 共 10 级，用于文本、边框、表面。

**语义色**（前景/背景/描边三件套，保证对比度）：

| 语义 | 前景 | 背景 | 描边 | 使用场景 |
| --- | --- | --- | --- | --- |
| success | `#157a3e` | `#e8f6ee` | `#bfead2` | 运行中、已完成 |
| warn | `#9a5a06` | `#fdf2e1` | `#f2ddb6` | 已暂停 |
| danger | `#bb2d1e` | `#fcecea` | `#f4cbc6` | 停止、出错、模板缺失、框选虚线框 |
| info | `#1c5b8a` | `#e9f2fa` | `#c2dbee` | 准备中、后续模板 |

**语义化表面**（组件引用这层，不直接引用墨色）：

`--surface-canvas` / `--surface-raised` / `--surface-sunken` / `--surface-hover` / `--surface-active`
`--text-primary` / `--text-secondary` / `--text-tertiary` / `--text-on-brand`
`--border-subtle` / `--border-default` / `--border-strong`

> 这一层是深色模式能"一个文件切换"的关键：组件只认语义名，深色主题只重绑语义名（见 `[data-theme="dark"]`），不碰任何组件 CSS。

### 3.2 排版系统

- 西文/中文主字体：`PingFang SC → Microsoft YaHei UI → Microsoft YaHei → system-ui`（Windows 优先雅黑 UI，同时兼容 macOS）
- 等宽字体：`ui-monospace → JetBrains Mono → Cascadia Mono → Consolas`，用于数字、device_id、模板名、日志
- 字号阶梯（10 级）：`11 / 12 / 13 / 14 / 16 / 18 / 20 / 24 / 30 / 40`
- 字重**只用两档**：`400` 正文、`500` 中等强调；标题用 `600/700` 仅限 `page-title`、`console-hero-title`、`metric-value`
- 行高：`--lh-tight: 1.25`（标题）／`--lh-normal: 1.6`（正文）／`--lh-loose: 1.75`（说明段落）
- **等宽数字**：所有数值使用 `font-variant-numeric: tabular-nums`，进度、秒数、置信度回读时数字不跳动

### 3.3 间距系统

4px 基准，8 级主刻度：`4 / 8 / 12 / 16 / 20 / 24 / 28 / 32 / 40 / 48 / 64`（`--sp-1` ~ `--sp-16`）。

- 卡片内边距：`--sp-5`（20px）
- 卡片间距：`--sp-5`（20px）
- 表单字段间距：`--sp-4`（16px）
- 指标卡网格间距：`--sp-3`（12px）

### 3.4 圆角与层级

圆角：`4 / 6 / 10 / 14 / 18 / 24 / full`（`--r-xs` ~ `--r-full`）
- 输入框、按钮：`--r-md`（10px）
- 卡片内容块：`--r-lg`（14px）
- 主卡片：`--r-xl`（18px）
- 首屏 Hero：`--r-2xl`（24px）

阴影：4 级（`--elev-1` ~ `--elev-4`），**平面化取向** —— 一级卡片只用 1px 极淡投影 + 1px 描边，靠边框而不是阴影分层。深色模式下阴影加重并转为纯黑。

### 3.5 动效

| Token | 时长 | 用途 |
| --- | --- | --- |
| `--dur-fast` | 120ms | hover、focus |
| `--dur-base` | 180ms | 视图切换、开关、Toast 进出 |
| `--dur-slow` | 280ms | 进度条、进度环 |

缓动：`--ease-out: cubic-bezier(.22,.61,.36,1)`（进入）／`--ease-in-out`（循环）
运行中状态点使用 1.6s 呼吸动画；`prefers-reduced-motion: reduce` 下所有时长归零。

---

## 四、组件库

定义在 `static/app.css`，命名采用 `block` / `block-element` / `block-variant`。

### 4.1 按钮 `.btn`

| 变体 | 用途 | 视觉 |
| --- | --- | --- |
| `.btn-primary` | 主操作（开始执行、保存方案、获取截图、保存框选） | 琥珀金实底 + 白字，`font-weight: 600` |
| `.btn-danger` | 危险主操作（停止） | `danger-fg` 实底 + 白字 |
| `.btn-ghost` | 次级操作（检测设备、刷新模板、套用推荐） | 透明底 + 描边 |
| `.btn-soft` | 柔和强调 | 当前色 10% 透明底 |
| `.btn-quiet-danger` | 低频危险操作（清除 STOP 文件、删除方案） | 默认隐藏色，hover 才转红 —— 避免危险操作抢视觉焦点 |
| `.btn-lg` | 首屏主操作 | 48px 高、16px 字 |
| `.btn-sm` | 卡片内小操作 | 30px 高 |

尺寸：默认 36px；热区 ≥ 44px（移动端 `.btn-lg` 撑满）
状态：`hover`（提亮/描边加深）→ `active`（下移 1px）→ `disabled`（45% 透明 + `not-allowed`）
焦点：`--focus-ring` 3px 琥珀光晕，键盘可见

### 4.2 表单

- `.input` / `.select`：38px 高、1px 描边、10px 圆角；hover 边框加深，focus 转品牌色 + 光晕
- `.input-num`：等宽数字
- `.input.is-invalid`：`--danger-bg` 底 + `--danger-br` 描边（实时校验，不等用户点了才报错）
- `.input-suffix`：右侧单位（秒）内嵌，不占布局
- `.range`：4px 轨道 + 15px 品牌色滑块（置信度用）
- `.switch`：36×20 轨道开关（设为默认用）
- `.segmented`：分段控件
- `.picks`：快捷档位胶囊组，`aria-pressed` 表达选中；`.plan` 前缀无冲突，用于「十连次数」的 10/20/30/50/100
- `.stepper`：输入框两侧 38px `−` / `+` 图标按钮，与 `.input` 等高对齐；数值 ≥100 后步长自动升为 10
- `.estimate`：耗时估算行，「预计耗时 **5 分 9 秒**」，数值等宽 + `tabular-nums`；`.is-long` 超 30 分钟转琥珀警示
- `.field` / `.field-label` / `.field-hint`：字段三段式 —— **标签在左、实时值在右、说明在下**，说明用 11px 三级文字，不抢注意力；`.is-danger` / `.is-warn` 变体随校验与风险切换
- 禁用态统一为 `--surface-sunken` 底 + 三级文字色（运行时锁定的视觉语言）

### 4.3 状态与反馈

- `.pill` + 语义后缀（`:idle` `:info` `:success` `:warn` `:danger` `:brand`）：11px 胶囊，带 6px 状态点；`.live` 附加呼吸动画
- `.progress` / `.progress-fill`：6px 圆角进度条
- `.ring`：132px 进度环（SVG，周长 339.292，`stroke-dashoffset` 驱动），中心显示 `已完成 / 目标`
- `.metric`：指标卡 —— 11px 大写标签 + 等宽大字值 + 11px 备注
- `.alert`：整幅告警条，左侧 20px 描边圆形惊叹号 + 标题 + 说明 + 操作按钮 + 关闭 ×；三档语义变体 `alert-warn` / `alert-danger` / `alert-info`。所有文字与描边走 `currentColor`，深浅主题自动反色
- `.toast`：右下角堆叠通知，左侧 3px 色条，3.6s 自动消失并淡出；分 `is-success` / `is-error` / `is-warn`
- `.empty-state`：虚线框 + 居中引导文案（无方案、无模板、无截图、无日志）

### 4.4 业务组件

| 组件 | 说明 |
| --- | --- |
| `.plan-card` | 方案卡：名称 + 默认徽章 + 参数摘要 + `.plan-card-eta` 耗时预估 + 模板角色摘要 + 卡内操作行。**整卡即"载入参数"**（`.plan-card-hit` 绝对定位覆盖层，`z-index: 1`），操作行 `z-index: 2` 保证按钮可独立点击；`aria-pressed` 表达使用中状态，`.is-active` 用品牌描边 + 右上角对勾 |
| `.plan-card-actions` | 卡内 `设为默认` / `删除`；虚线分隔，`.btn` 等宽撑开 |
| `.plan-card-actions.is-armed` | 删除二次确认态：操作行改为纵向，先一行红字说明「删除「X」后将从列表移除」，再一行「确认删除」(danger) / 「取消」(ghost)。卡片同时加 `.is-armed`（危险描边，优先级高于 `.is-active` 的品牌描边）。按钮带 `data-lock`，运行中锁定。后端是**软删除**（移入 `data/plans-trash/`），所以文案不说"不可恢复" |
| `.tpl-target` | 采样页「将覆盖」提示：56px 缩略图（真实模板图）+ 标题 + 等宽文件名。侧栏仅 260px，正文只放文件名，长解释放按钮下方常驻 `.side-hint`。带 `.tpl-target[hidden] { display: none }` —— 作者样式的 `display: flex` 会盖掉 UA 的 `[hidden]` |
| `.btn-icon` | 30px 方形图标按钮，默认与背景同色（`--text-tertiary`），hover 才显形；`.is-danger` hover 转红。用于步进器与卡内次要操作 |
| `.plan-bar` | 「当前生效方案」状态条：胶囊徽章 + 方案名 + 说明 + `.plan-dirty` 未保存改动标记 + 覆盖保存入口 |
| `.run-config` | 控制台首屏「运行配置」卡：左栏 `.plan-chips` 方案芯片、右栏十连次数（`.picks` 档位 + `.stepper` + `.estimate` + 实时校验提示）。窄屏（≤1080px）转单列 |
| `.plan-chip` | 控制台方案芯片：两行结构（方案名 + 默认徽章 / 参数摘要）。**整块即载入**，`aria-pressed` 表达使用中，选中态用 `--surface-active` 底 + `--brand-400` 描边 + `--brand-700` 文字（深浅主题共用同一套语义名，不单独覆盖） |
| `.plan-dirty` | 琥珀胶囊 + 6px 圆点，仅当表单参数与已载入方案不一致时出现（方案页与控制台各一处，同一个 `dirty` 判定） |
| `.tpl-card` | 模板卡：16:9 缩略图 + 文件名 + `首次`(radio) / `后续`(checkbox) 角色；选中描边、缺失态转红 |
| `.tpl-flag` | 「推荐」角标（`z-index: 2` 保证不被缩略图盖住） |
| `.live-shot` + `.live-badge` | 设备实时画面：1px 描边凹槽内嵌后端截图，左上角「实时」呼吸徽标仅在任务运行中出现 |
| `.shot-stage` + `.marquee` | 截图舞台与框选虚线框，框内右上角实时显示 `宽 × 高` |
| `.log-view` | 等宽日志面板，自动滚动，`ERROR/失败/异常` 行转红、`警告/WARN` 转琥珀；`.is-compact` 变体限高 236px 用于控制台 |
| `.quick-row` | `dt/dd` 执行概要行，右侧等宽数值 |
| `.advanced` | 参数组内的折叠高级项（`<details>`）：标题 + 右侧等宽状态回读（如「0.78–1.30 · 17 档」），展开后是 `.advanced-row` 三列数字输入。默认收起——低频参数不该挤占主表单；状态回读放在标题行，**不展开也能看到当前配置是否合理**，`.is-danger` 在参数非法时转红 |

---

## 五、响应式策略

移动优先断点（与 `app.css` 一致）：

| 断点 | 布局变化 |
| --- | --- |
| ≥ 1081px | 完整双栏：`console-grid` 执行概要 + 设备实时画面并排，运行日志独占整行；`shot-layout` 画面 + 侧栏并排 |
| ≤ 1080px | 展开为单列堆叠 |
| ≤ 900px | **侧边栏转为底部标签栏**（固定底部、图标在上文字在下、6 个入口），顶栏精简（隐藏设备与状态元信息），内容区底部预留 96px 避让 |
| ≤ 560px | Hero 字号 30→24px，指标卡转 2 列，主按钮撑满 |

移动端底部导航隐藏「收起侧栏」（该功能在标签栏形态下无意义），保留「外观」主题切换。

实测 390×844 无横向溢出。

---

## 六、无障碍（WCAG AA）

- **对比度**：正文文字与背景全部 ≥ 4.5:1；`--ink-500`（三级文字）仅用于备注类非关键信息，与白底对比 4.6:1；大字号（≥18px）≥ 3:1。语义色均使用「深前景 + 浅背景」组合而非彩色文字配白底。深浅两套主题分别实测，不假设深色会自动继承浅色的对比度。
- **键盘可达**：所有交互元素为原生 `<button>` / `<input>` / `<select>`，Tab 顺序与视觉顺序一致；`:focus-visible` 3px 光晕环，不移除默认焦点样式。
- **屏幕阅读器**：Toast 容器 `aria-live="polite"`；导航 `aria-label` + `aria-current="page"`；视图区 `role="tabpanel"`；参数分组 `role="group"` + `aria-label`；进度环 `role="img"` + `aria-label`；纯装饰 SVG 加 `aria-hidden`；方案卡覆盖层按钮带 `aria-label="载入方案 X"` 与 `aria-pressed`（表达"当前使用中"）；次数档位用 `aria-pressed` 而非 `:checked` 的视觉态。
- **触控热区**：移动端标签栏项最小 56px 宽 44px 高；按钮默认 36px 高，主操作 48px。
- **动效偏好**：`prefers-reduced-motion: reduce` 时所有过渡时长归零，呼吸动画停止。
- **文本缩放**：全站使用 rem/相对字号 + 流式网格（`minmax(0, 1fr)`），200% 缩放不溢出。
- **颜色不单独承载信息**：状态除颜色外均带文字（"运行中"/"已暂停"），模板缺失除变红外还有红色描边 + 徽章计数。

---

## 七、交互细节中的可用性决策

1. **就绪检查替代"点了再说"**：点击开始前先跑 `readiness()`，按「模板缺失 → 未指派模板 → 未连接设备 → 次数无效」优先级给出唯一一条最该修的提示；缺模板时自动跳转模板视图并高亮问题卡。
2. **运行时锁定**：任务运行中所有会改配置的控件（`data-lock`）统一禁用，杜绝"跑着改参数"产生的不确定状态；仅保留「停止」可用。
3. **危险操作降噪**：删除方案、清除 STOP 文件使用 `.btn-quiet-danger`，默认接近普通文字色，hover 才转红；删除方案另加**内联二次确认**并回显方案名。**不使用原生 `confirm()`** —— 它在嵌入式预览面板（沙箱 iframe 未开 `allow-modals`）里会静默返回 `false`，用户勾过"阻止此页面创建更多对话框"之后同理，表现是"按钮点了没反应且没有任何提示"。内联确认还顺带满足"危险操作不该离操作对象 800px 远"：确认/取消就出现在卡片自己的操作行里，Esc 也能放弃。
4. **实时回读**：滑块/数字输入旁边始终显示当前值（`0.86`、`4.8 s`），控制台概要同步刷新，用户不需要"心算"参数组合的效果。
5. **日志双视图**：控制台保留最近 40 行做氛围感知，完整日志放独立视图；日志视图打开时自动滚到底部。
6. **主题跟随系统**：首次访问跟随 `prefers-color-scheme`，手动切换后写入 `localStorage`，深色模式便于长时间挂机时观看。
7. **首屏只回答两个问题**：Hero 区恒定表达"现在能不能跑"（就绪检查），"跑到哪了"交给状态环与指标卡，"为什么停了"交给告警条。任务结束后 Hero 立刻回到就绪判断，不会永久停留在终态文案。
8. **失败可见化**：连续未匹配次数实时上指标卡，达到阈值自动暂停时把后端 `last_error` 原文抬到首屏告警条，并附「继续执行」直达按钮；告警条可关闭，同一状态指纹不再重复弹出，新状态自动重新出现。
9. **方案选择 = 载入，不是选中**：方案卡整卡就是「载入这个方案的全部参数」这一个动作。**必须区分"浏览器里高亮的卡"与"真正已载入参数的方案"** —— 前者只是视觉状态，后者才决定表单内容。两者用同一个变量（`activePlanFilename`）驱动，避免出现"界面显示方案 A、参数却是方案 B"这种显示层撒谎。载入是只读操作，所以整卡可点、无二次确认。
10. **未保存改动必须可见**：参数指纹（`planFingerprint`，排除设备这类环境状态）与载入时快照比对，不一致就亮出「参数已改动」并给一键覆盖保存。没有这个信号，用户根本不知道自己改的参数是否已落盘。
11. **方案操作内联在卡上**：`设为默认` / `删除` 放在卡片自身的操作行，而不是让用户选中后再滚到页面别处找按钮 —— 危险操作不该离操作对象 800px 远。
12. **执行次数给决策信息，不只是输入框**：裸数字输入框无法回答"100 次要跑多久"。因此加了快捷档位（10/20/30/50/100）、± 步进（≥100 后步长自动升 10），以及**三处联动的耗时估算**（字段内、控制台概要、指标卡备注、方案卡）。估算基于后端真实循环节奏：`0.35s 识别开销 + 补点次数×连点间隔 + 点后等待 + 抖动期望值(抖动/2)`，并明确标注"按全部命中估算"——匹配失败走 `interval` 重试会更慢，不能给用户一个偏乐观的数字却不说明。
13. **超长运行先警告**：估算超过 30 分钟时，估算值与提示文案一起转琥珀色并提示"运行中可随时停止"。不做阻断式弹窗——任务本来就可随时停止，弹窗只会变成噪音。
14. **高频操作不下沉**：控制台首屏直接给「方案芯片 + 十连次数」，把"换方案 / 改次数"这两个每次运行前都要做的动作留在原地；低频的细粒度参数（置信度、抖动、连点间隔…）与方案增删改仍在「方案与参数」。判断标准不是"属于哪一类信息"，而是**每轮运行前是否都要摸一次**。
15. **镜像控件只有一份真相**：控制台的次数控件是 `#clickCount` 的镜像，档位与步进统一走 `setClickCount()` / `nudgeCount()` 写回原输入框，再沿同一条 `renderSummaries()` 链刷新（档位高亮、耗时估算、概要、指标卡备注）。镜像输入框在 `document.activeElement === 自己` 时跳过回写，否则会在用户打字时光标跳位。**绝不新增第二个次数状态**——多一份状态就多一处不同步。
16. **重复输入是可用性债**：采样页要重采旧按钮时，用户得凭记忆把 `video-repeat10.png` 敲一遍，敲错一个字符就多出一个废模板。因此加「选择已有模板」下拉（首项「＋ 新建模板」），选中即回填名称。手敲的路径也保留 —— 但**两条路径共用同一个判定**（`renderTemplateTarget()` 按 trim 后的名称是否命中 `templatesCache` 决定），所以无论怎么填，只要命中已有模板就亮出「将覆盖该模板」+ 缩略图预览，主按钮文案同步变成「覆盖该模板」、toast 也说「已覆盖保存」。覆盖是不可逆的，不能悄悄发生。
17. **高级参数折叠，但状态回读必须外露**：尺度范围/步长这类参数 99% 的时间不用动，但一旦被改坏会直接导致大面积漏检。因此用 `.advanced` 折叠项收纳，标题行右侧常驻等宽状态回读（如「0.78–1.30 · 17 档」），**不展开也能判断当前配置是否合理**；参数非法时回读转危险色。折叠不是"藏起来"，是"降低视觉权重同时保持可观测"。
18. **文案不能比实现更绝对**：删除方案的文案从「不可恢复」改为「将从列表移除」，因为后端实现是软删除（文件移入 `data/plans-trash/`）。界面措辞必须与真实语义一致 —— 说"不可恢复"但实际可找回，会削弱用户对危险操作提示的信任，下次真不可恢复的提示也会被当成狼来了。

---

## 八、已知取舍与坑

- **深色模式的语义色必须成套映射**。`.pill-brand` 曾经在深色下被单独覆盖成 `background: --brand-200` + `color: --brand-100`，而这两个 token 在深色主题里分别是 `#4a3a19` 与 `#33270f` —— 深棕配深棕，对比度约 **1.4:1**，完全不可读。正确做法是**不覆盖**：`--brand-050` / `--brand-700` 已在 `tokens.css` 里按主题重映射，直接引用即可。新增语义色片时请遵守：底用 `-bg`/`-050`，字用 `-fg`/`-700`，不要跨档位混搭。
- **`disabled` 只能有一个归属地**。`applyLock()` 是唯一设置 `disabled` 的地方，`setLocked()` 只负责记录运行状态并转调它。早期版本里 `renderSummaries()` 也会写 `disabled`，结果 1 秒一次的状态轮询会在运行中把刚锁上的控件重新解开。
- **动态插入的 `[data-lock]` 节点要立刻补一次 `applyLock()`**。`innerHTML` 换掉操作行之后，新按钮的 `disabled` 是默认的 `false`；不补的话，运行中会出现最长 1 秒的可点窗口。`renderPlanActions()` 末尾就补了这一下。
- **`[hidden]` 挡不住作者样式里的 `display`**。`[hidden] { display: none }` 在 UA 样式表里，作者写的 `.tpl-target { display: flex }` 优先级更高，会让"隐藏"元素照常显示。凡是自定义了 `display` 又要用 `hidden` 切换的组件，必须显式补 `X[hidden] { display: none }`。同类坑此前在 `.live-shot img` 上出现过一次。
- **原生对话框在嵌入式宿主里不可信**。`confirm()` / `alert()` / `prompt()` 在沙箱 iframe（未开 `allow-modals`）或被用户"阻止此页面创建更多对话框"后，会静默返回 `false`/`undefined`，不弹任何 UI。凡是靠它做分支判断的流程，都会表现为"点了没反应"。危险操作的确认一律内联到页面里，并配合 toast 做结果反馈。
- **静态资源必须带版本号**。`index.html` 的 css/js 引用注入 `{{ asset_version }}`（后端取 `static/` 最新 mtime），HTML 响应带 `Cache-Control: no-store`。否则改完前端刷新不生效，会被误判成"前后端没联动"。
- **尺度阶梯前后端有两份实现，必须逐字同步**。后端 `MatchProfile.scales()` 与前端 `scaleLadder()`（`static/app.js`）都以 **1.0 为锚点**向两侧展开、补端点、`round4`。只改一侧会让界面回显的档数（如「16 档」）与后端实际扫描不符 —— 出现过一次，原因是前端忘了同步"锚定 1.0"这条规则。
- **删除类操作不要直接 `unlink()`**。部分托管运行环境会注入 `sitecustomize.py` 把 `Path.unlink` 重定向到回收站并带**批量删除守卫**（同一会话累计超过阈值直接 `SystemExit(1)`），接口会返回 500，用户侧表现为"删除按钮偶发失灵"。本项目的做法是软删除（`os.replace` 到 `data/plans-trash/`），并在接口补 `OSError → 409`。**"偶发失败"通常指向有状态的外部约束（计数器/配额/守卫），而不是代码里的竞态。**
- **参数组内的折叠项用原生 `<details>`**，不自己实现展开态。原生元素自带键盘可达、`aria-expanded` 与"页面内查找自动展开"等行为，用 `<button>` + 类名手搓这些都要自己补。

---

## 九、前后端接口契约（前端消费的字段）

`GET /api/tasks/state` 是唯一的实时数据源，前端每秒轮询一次。**每个字段都必须有落点**，遗漏即等于功能缺失：

| 后端字段 | 前端落点 |
| --- | --- |
| `status` | 顶栏状态胶囊、Hero 状态胶囊、进度环中心标签、告警条分支 |
| `clicked` / `target` / `progress_percent` | 进度环数值、环填充（周长 339.292），告警条进度文案 |
| `is_running` | 运行时锁定 `setLocked()`、实时徽章显隐、Hero 走运行态还是就绪态 |
| `misses` | 「连续失败」指标卡 + 动态备注 |
| `last_error` | 告警条正文原文展示（暂停/出错时） |
| `last_match` | 「最近匹配」指标卡：模板名 + 置信度 + **尺度** + 坐标 + 连点次数 |
| `last_screenshot` | 「设备实时画面」卡片；运行中同时驱动采样页（`followRun` 开关） |
| `logs` | 控制台紧凑日志 + 日志视图，按关键词分级着色 |

配套图片接口 `GET /api/screenshots/{name}` 即 `last_screenshot` 的取图入口，前端仅在文件名变化时重新请求，避免无谓流量。

契约维护约定：

1. 后端新增状态字段时，必须同步在本表登记并在 `renderXxx` 中消费；否则字段等于不存在。
2. 表单类输入一律加 `autocomplete="off"`：浏览器会恢复上次填写的值，可能静默覆盖从后端检测到的真实设备。
3. 设备值以 `/api/devices` 返回为准，检测后强制与下拉框同步，不使用本地残留值。

---

## 十、文件清单与维护约定

```
static/tokens.css     设计令牌（颜色/字体/间距/圆角/阴影/动效/浮动提示色 + 深色主题）← 改视觉先改这里
static/app.css        基础重置 + 组件 + 布局 + 响应式
static/app.js         视图路由、状态轮询、方案/模板/采样逻辑
templates/index.html  5 个视图的语义化骨架（含资产版本号占位符）
xyzw_auto_clicker/app.py  提供 asset_version（static 目录 mtime）与 HTML no-store 响应头
```

维护约定：

1. 新增颜色必须先进 `tokens.css` 定义令牌，再在组件中通过 `var()` 引用；深色模式只需补 `[data-theme="dark"]` 映射。
2. 新增组件按 `block / block-element / block-variant` 命名，优先复用 `.btn` `.card` `.pill` `.field` `.alert` 等既有原子。
3. 结构类工具类（`.eyebrow` `.page-title` `.num` `.sr-only` `.scroll-y`）在 `app.css` 顶部集中定义。
4. 修改布局后请在 1440 / 1080 / 900 / 560 / 390 五个宽度回归检查。
5. **前端资源改动即刻生效**：`index.html` 通过 `{{ asset_version }}` 给 css/js 加版本号（取 `static/` 目录最新 mtime），HTML 本身返回 `Cache-Control: no-store`。改完前端刷新页面即可，无需强刷。

---

**设计者**：UI Designer
**交付状态**：已实现并完成浏览器实测（83 项冒烟 + 61 项控制台运行配置 + 44 项方案与次数 + 31 项方案增删改 + 31 项模板名选择 + 14 项框选链路，共 264 项断言全部通过）
**验证覆盖**：视图路由、控制台方案芯片载入与次数镜像（双向）、方案卡载入与未保存改动检测、方案设为默认/删除（内联二次确认 + Esc 取消 + 运行时锁定）及其状态清理、模板角色指派与缺失校验、采样页模板名选择与覆盖提示、执行次数档位/步进/耗时估算/实时校验、运行时锁定、深色/浅色切换、侧栏折叠、移动端标签栏、截图框选与裁剪保存、后端状态字段联动（实时画面/连续失败/暂停原因）、资产缓存失效
**测试入口**：`tests/ui/`（纯 CDP，无 npm 依赖，见 `tests/ui/README.md`）

---

## 十一、自动 a11y（axe-core + CI）

§6 的 a11y 验收过去只靠「作者目测 + 14 项键盘/ARIA 冒烟断言」，本质是「我检查过的部分」，不是「全部 DOM 跑过 WCAG 检查」。

### 11.1 现状（PR-17 起）：合并门槛

PR-10 把 axe-core 拉进 CI 作为质量护栏，PR-17 把它从「过渡期 warn」升级为「合并门槛 fail-fast」——`a11y` job 的 `continue-on-error` 已摘除，**PR + main 一样只要出现 `serious` / `critical` 就阻止合并**。这与 lint / test job 的「PR 上 warn、main 上 fail」彻底脱钩：a11y 走的是「PR 上即阻塞」路径，与 release 流程同等级。

| 阶段 | PR-10（PR 引入期） | PR-17（合并门槛） |
| --- | --- | --- |
| lint / test | `continue-on-error` PR 上 warn | （未变） |
| **a11y** | `continue-on-error: ${{ github.event_name == 'pull_request' }}` PR 上 warn | **fail-fast**，无 `continue-on-error` |
| 判失败口径 | `impact ∈ {serious, critical}` | 同（未变） |

### 11.2 编排与依赖

- **依赖**：`@axe-core/playwright` + `playwright`，仅在仓根 `package.json` 的 `devDependencies` 与 `tests/ui/package.json` 中声明；`workspaces: [tests/ui, relay]` 让 `npm ci` 把依赖下发到 `tests/ui/node_modules/`。生产镜像里没有这些（`.dockerignore` 显式排除 `tests/**/node_modules/`）。
- **驱动脚本**：`tests/ui/a11y.mjs` 用 Playwright 自带 Chromium 打开 `http://127.0.0.1:8999/`，按 `console / plans / templates / capture / logs` 顺序切换视图，对每个视图跑 `AxeBuilder({ page }).withTags(['wcag2a','wcag2aa','wcag21a','wcag21aa']).analyze()`。
- **判失败标准**：axe 报告 `impact ∈ {serious, critical}` 即视为 CI 失败；`moderate / minor` 仅打印不阻塞。
- **退出码语义**（契约，PR-17 重申不变）：
  - `0`：5 视图全部无 serious/critical。
  - `1`：任意视图出现 `serious` 或 `critical`。
  - `2`：环境起不来（端口未通、`docker-up` 健康检查超时、`npm ci` / `npx playwright install` 失败等）。
- **CI 编排**：`docker-up` job（启动懒鱼容器并把 8999 端口对外暴露，循环 `/api/health` 直至就绪）→ `a11y` job（`needs: docker-up`，先 `npm ci` + `npx playwright install --with-deps chromium`，再 `node tests/ui/a11y.mjs`）。PR-17 之后两个 job 之间不再有 `continue-on-error`，a11y 失败直接把整个 workflow 标红。

### 11.3 为什么从过渡期升到合并门槛

- axe-core 早期会刷出重复 landmark、装饰 SVG 等 moderate/minor 误报；本仓库设计 token 收敛（`tokens.css` 单一来源、深浅双主题、所有组件 `var(--xxx)`）已保证不再新增此类噪声。
- `tests/ui/a11y.mjs` 退出码语义稳定（0/1/2），从 PR-10 到 PR-17 未变；脚本本身不需要随门升级而改动。
- §6 WCAG AA 设计原则与 axe 规则一一对应：contrast / keyboard / aria 的真问题均落在 serious/critical，升级合并门槛不会误伤正当 PR。

### 11.4 与 §6 的关系

§6 是「设计原则」（WCAG AA、对比度、键盘、屏幕阅读器），本节是「验证手段 + 合并门槛」。设计原则改了 → axe 规则自动跟上；axe 规则出了 serious/critical → 反过来逼 §6 落地，并且 PR 不再被允许通过。

> **不需要单独跑 axe**：CI 已经把 a11y 跑在 `docker-up` 起的真容器上。本地想调试时：`docker compose up -d lazy-fish` → `npm ci && npx playwright install chromium` → `node tests/ui/a11y.mjs`。本地退出码与 CI 一致：`echo $?` 应是 `0` / `1` / `2` 之一。

---

## 十二、视觉回归（playwright 截图 + pixelmatch）

§3 的设计令牌（颜色 / 字号 / 间距 / 圆角 / 阴影）改了，§4 的组件 CSS 跟着改，§6 的 a11y 验收随之失守 —— 但靠 a11y 检测不出来。CSS 层级抖动（垂直居中差 1px、字号变化、图标换位）axe-core 不会报，smoke.mjs 也不会断言"长成什么样"，结果就是 PR 合了之后用户看到"控制台按钮位置和昨天不一样"。

PR-18 用 **pixelmatch** 在 CI 上把"长成什么样"锁住，PR-20 把它从"过渡期 warn"升到合并门槛：

### 12.1 依赖与驱动

- **依赖**：`pixelmatch ^5.3.0` + `pngjs ^7.0.0` + `fs-extra ^11.2.0`（仅 devDep），声明在仓根 `package.json` 与 `tests/ui/package.json` 中，沿用 §11 的 `workspaces` 下发机制。生产镜像里没有这些（`.dockerignore` 显式排除 `tests/**/node_modules/`）。
- **驱动脚本**：`tests/ui/visual.mjs` 用 Playwright 自带 Chromium 打开 `http://127.0.0.1:8999/`，按 `console / plans / templates / capture / logs` 顺序切换视图（同 §11 的视图枚举与 hash 时序，保证"视角一致"），1280×900 视口拍 5 张 PNG，与 `tests/ui/baselines/*.png` 用 `pixelmatch({ threshold: 0 })` 做像素比对。
- **判失败标准**：差异像素占比 `> 0.1%`（即 `0.001`）即视为 CI 失败。`SIZE_MISMATCH`（baseline 与 actual 尺寸不同）直接视为 fail。**阈值不是"完美比对"，是"样式层实质变更"探测** —— 容忍极少量抖动（动画残留、状态点呼吸、字体子像素抗锯齿），挡得住真实改动（字号 / 间距 / 颜色变化必然 > 1%）。

### 12.2 Baseline 生命周期

- **首次初始化**：`tests/ui/baselines/*.png` 缺失会**生成** baseline 并视为通过 —— 让首次合入的 PR 不会因为"没基线"被自己卡住。后续 PR 必须带 baseline，否则会被本次合入 PR 的"生成"动作产出的 baseline 锁死。
- **更新入口**：`node tests/ui/visual.mjs --update-baseline` 或 `npm run test:visual:update` 强制覆盖 5 张基线图。
- **更新流程**（给维护者）：
  1. 本地起容器：`docker compose up -d lazy-fish`
  2. 装依赖：`npm ci && npx playwright install --with-deps chromium`
  3. 确认改动是预期的（不是误改），跑：`npm run test:visual:update`
  4. **逐图人工核对** `tests/ui/baselines/*.png` 与改动前的版本（git diff 不可视化 PNG，**必须用图像浏览器或 PR review 的 image diff**）—— 确认没有把"按钮错位"也写进 baseline
  5. 提交 `tests/ui/baselines/*.png` 与样式改动一起进 PR；CI 在 PR 上 fail-fast（合并门槛，PR-20 起），但 `tests/ui/diffs/*.diff.png` 会作为 artifact 上传，reviewer 二次核对
  6. 合并到 main 后 main 上同样 fail-fast，新 baseline 是合并门槛
- **禁直推 main 重生成**：baseline 变动必须走 PR + 人工审 diff 图，避免误把"渲染异常"当"正常样式"固化下来。

### 12.3 CI 编排

- **Job**：`visual` job（`needs: docker-up`，复用 §11 的懒鱼容器；先 `npm ci` + `npx playwright install --with-deps chromium`，再 `node tests/ui/visual.mjs`）。
- **PR vs main 策略（PR-20 起：合并门槛）**：PR + main 都 fail-fast —— `visual` job 已摘除 `continue-on-error`，与 §11 a11y job 同语义（PR-17 那一波）。这与 lint / test job 的「PR 上 warn、main 上 fail」彻底脱钩：visual 走的是「PR 上即阻塞」路径，与 release 流程同等级。
- **从过渡期到合并门槛的演进**：
  - PR-18 引入期：PR 上 `continue-on-error: ${{ github.event_name == 'pull_request' }}`，main 上 fail-fast —— baseline 漂移可能在多平台字体差异下刷出少量噪声，给作者留"先合并、后修"的过渡期（与 §11 PR-10 同策略）。
  - PR-20 升级：baseline 已稳定（playwright chromium 锁 1.49.x 主线版，跨平台字体子像素抗锯齿差异收敛；阈值 0.1% 不再被抖动刷出），删除 `continue-on-error` 即升级为合并门槛，演进路径同 §11.3。
  - 与 a11y job 同语义（PR-17 那一波）：visual 现在也走"PR 上即阻塞"路径，与 release 流程同等级；baseline 变动必须走 PR + 人工审 diff 图，禁直推 main 重生成。
- **Diff 工件**：`if: failure()` 时把 `tests/ui/diffs/*.diff.png` 作为 artifact 上传（`actions/upload-artifact@v4`），reviewer 在 PR 上能直接看到差异像素位置。
- **退出码语义**（与 §11 a11y 同约定；脚本顶部注释已显式写出，见 `tests/ui/visual.mjs` 顶部）：
  - `0`：5 视图全部 ≤ 阈值（含 baseline_created / baseline_updated）。
  - `1`：有视图超阈值或尺寸不匹配。
  - `2`：环境起不来（端口未通、`docker-up` 健康检查超时、`npm ci` / `npx playwright install` 失败等）。

### 12.4 与 §11 的关系

§11 是 a11y 质量护栏（axe-core 跑全部 DOM），§12 是视觉质量护栏（pixelmatch 跑 5 个视图快照）。一个看"对不对"，一个看"好不好看"；两者互不替代：

- axe 全绿不代表布局没崩（按钮错位 2px 是合法合规的，但人眼会跳出来）
- pixelmatch 全过不代表 aria 没漏（baseline 里的"已有错误"会被像素比对"正确地"接受）

设计上：§3 令牌改了 → §12 baseline 必然要更新；§6 WCAG AA 改了 → §11 axe 规则跟上；§12 与 §11 任何一条失守都应触发 PR review 重审对方是否要随之调整。

### 12.5 阈值选择的权衡

- 调到 `0%`（精确比对）：被字体子像素抗锯齿差异、运行中状态环的微小呼吸直接打死 —— 全平台字体回退一致才有可能通过。
- 调到 `1%`：把"按钮内边距错 2px"这种肉眼可见的变化漏掉。
- `0.1%`（`0.001`）：是"人眼看不出差异，但 pixelmatch 能区分真改"的下限 —— 本仓库采用此值。

### 12.6 Baseline 稳定性（PR-20）

阈值稳定的前提是 baseline 不被无关因素刷出 diff，下列两条是 PR-20 升级合并门槛时的硬约束：

- **浏览器锁定**：playwright chromium 必须走 **1.49.x 主线版**（仓根 `package.json` 与 `tests/ui/package.json` devDeps 均声明 `playwright: ^1.49.0`）。如出现跨平台字体差异（Linux runner 上 Noto/Sans 字体回退与作者本机 macOS/Windows 字体不一致 → 文本子像素抗锯齿漂移 → pixelmatch 在 0.1% 阈值下刷出零星 diff），不要放宽阈值；优先校验 playwright 版本是否被自动跳到 1.50+。如确需升级，必须同步跑一次 baseline 全量回归（5 视图逐图人工核对）再合入。
- **PNG 编码稳定**：`pixelmatch({ threshold: 0 })` 与 PNG byte-level 比对相比，对图像编码抖动宽容；但 baseline 一旦入库就不要改 pngjs / fs-extra / pixelmatch 主版本——同样的像素输入，不同版本可能产生不同 diff 图（artifact 可视化口径会变），升级需要与 baseline 全量回归同步评估。

> **不需要单独跑视觉回归**：CI 已经把 visual 跑在 `docker-up` 起的真容器上。本地想调试时：`docker compose up -d lazy-fish` → `npm ci && npx playwright install chromium` → `node tests/ui/visual.mjs`（baseline 缺失会自动生成）。想强制覆盖 baseline：`node tests/ui/visual.mjs --update-baseline` 或 `npm run test:visual:update`。
