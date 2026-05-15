# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Accumulate-PPTs 是一个静态网站，以书架风格展示学术论文阅读汇报的演示幻灯片。每个幻灯片是一个独立的 HTML 文件，通过主页面 `index.html` 中的 iframe 加载渲染。网站使用 Tailwind CSS（CDN）、自定义 CSS 动画和原生 JavaScript —— 无构建步骤、无框架、无包管理器。

## 架构

**单页面入口 (`index.html`)** — 3D 书架 UI：
- 读取 `slides-manifest.json` 获取可用幻灯片列表
- 使用 Tailwind + 自定义 CSS 渲染筛选/分页的书卡（3D 倾斜、影院开场、树叶摇曳动画）
- 全屏 iframe 遮罩层打开幻灯片
- 分类筛选、搜索、键盘导航均为内联 JS 实现

**幻灯片 (`paper-slides/*.html`)** — 每个是独立 HTML 文件，使用 html-paper-slides 模板系统（见下方 Skills）。内嵌所有 CSS/JS，从同级 `_assets/` 文件夹加载图表。

**PDF 提取** — `skills/html-paper-slides/scripts/pdf_extractor.py` 从 PDF 提取透明背景图表。依赖 `pymupdf` 和 `Pillow`。

## 关键目录

| 路径 | 用途 |
|------|------|
| `index.html` | 着陆页（唯一的"应用"文件） |
| `slides-manifest.json` | 所有幻灯片的注册表（标题、分类、路径、标签） |
| `paper-slides/` | 生成的 HTML 幻灯片 + 对应的 `_assets/` 文件夹 |
| `paper-slides/<名称>/` | 提取的图表 (`crop_*.png`) + `extraction_summary.json` |
| `raw/` | 源 PDF 文件（已 gitignore） |
| `ingest/` | 中间提取输出（已 gitignore） |
| `output/` | 生成的内容产物 |
| `assets/` | 站点级静态资源（favicon、宣传图片） |
| `skills/` | Skill 定义和模板 |

## Skills

两个 Skill 驱动幻灯片生成：

### html-paper-slides (`skills/html-paper-slides/`)
生成苹果风/Notion 风的 HTML 演示文稿，用于论文阅读汇报。工作流：
1. 理解论文 → 运行 `pdf_extractor.py` 提取图表 → 生成 `<论文名>.md` 内容文件
2. 按规范结构规划 13–22 页幻灯片（封面 → 摘要 → 引言 → 相关工作 → 方法 → 实验 → 总结 → 结尾）
3. 使用模板组件填充内容（`.card`、`.badge`、`.highlight-bar`、`.vs-col`、`.steps`、`.quote-block`、`.ipo-circle`、`.bullet-list`、`.prompt-box`、`.glow`、`.cover-tag`）
4. 输出单个独立 HTML 文件到 `paper-slides/`

**来自 SKILL.md 的关键 CSS/JS 规则：**
- 配色：背景 `#ffffff`、正文 `#000000`、标题 `#3966A2`、强调 `#132843`、次要 `#6191D3`
- 字体：`Inter`、`Noto Sans SC`，然后系统回退 —— 禁止 Google Fonts `<link>`（`file://` 下会 404）
- `.slide.active` 的 `opacity`、`visibility`、`transform` 必须加 `!important` 以覆盖 JS 内联样式
- JS 翻页函数只能切换 `classList`（`active`、`exit-up`），禁止设置内联样式
- 不显示翻页箭头；仅支持左右键盘翻页

### html-slides (`skills/html-slides/`)
简约暗色主题幻灯片模板（`skills/html-slides/templates/presentation.html`）。使用不同配色（深色背景、紫色标题、金色强调）。共享相同的 Slide Engine 架构。

## 添加新幻灯片

1. 将 PDF 放入 `raw/`
2. 运行 `python skills/html-paper-slides/scripts/pdf_extractor.py <pdf路径> --output-dir paper-slides/<论文名>/`
3. 按 html-paper-slides skill 规范生成 HTML 幻灯片
4. 保存到 `paper-slides/<论文名>.html`
5. 在 `slides-manifest.json` 中添加条目（标题、分类、文件路径、标签）
6. 在浏览器中打开 `index.html` 验证（或直接通过 `file://` 打开 HTML 文件）

## 开发注意事项

- **无构建系统** — 全部为静态 HTML/CSS/JS，直接在浏览器中打开 `index.html` 即可。
- **无 npm/yarn** — Tailwind 通过 CDN script 标签加载并内联配置。
- **清单驱动** — 新幻灯片必须在 `slides-manifest.json` 中注册才会显示在着陆页。
- **自包含幻灯片** — 每个 HTML 文件必须支持离线运行（`file://` 协议）；避免外部字体 `<link>` 标签和 API 调用。
- **幻灯片中的图片路径** — 使用从幻灯片 HTML 位置的相对路径（如 `Dialogue_Director/crop_001_page001_reg1.png`）。
