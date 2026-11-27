---
name: 复杂标准文档与标书编纂
description: 面向多源参考资料目录，按“离线解析与三级导航 -> 事实基线与招标约束对齐 -> 目录规划与门禁确认 -> 总工级反套话分章撰稿 -> 合肥青天大模型五维严苛质检闭环（≥90分准入锁死） -> 原生标准/暗标 Word (.docx) 交付”执行。
---

# 复杂标准文档与标书编纂作业规程 (`document-authoring`)

当用户提供资料文件夹或参考附件，要求编写施工组织设计、投标技术方案、项目实施方案或标准报告并交付正式 Word 文档时，严格遵循以下五步认知闭环执行，并使用 `update_cognitive_steps` 实时更新右栏进度。

> **专业高标准铁律**：生成的技术标严禁写成普通大模型堆砌套话的“散文墙”！`load_skill_detail` 返回的 `references["qingtian-rubric.md"]` 已完整给出《合肥“青天大模型”评标质检与总工级撰稿标准》，撰稿与质检环节必须严格遵照执行。

---

## 第一步：环境感知与标准化 (`perceive`)

1. 先检查工作区是否已有 `.workspace/parsed/INDEX.md`；若尚未标准化，调用 `scan_directory(target="source")` 概览后调用一次 `ingest_documents()` 完成批量离线转换。
2. **遵循三级高速导航法查阅资料**（严禁对二进制文件调 `read_lines`）：
   - **一级看全貌**：读 `.workspace/parsed/INDEX.md` 识别核心文件（招标正文、补疑/变更公告、工程量清单、最高限价、地勘与设计说明）及对应的 `toc.md`；
   - **二级定页码**：读长文档的 `toc.md` 或用 `search_text(scope="parsed")` 定位关键词页码；
   - **三级精翻页**：用 `read_parsed_pages(doc_path, start_page, end_page)` 直读目标物理页；若关键扫描页标记“按需待识”，调用 `ocr_pdf_pages` 精准补识；若需对多专业 Excel 工程量清单全表精确提取汇总，调用 `run_python` 读取原表计算。

---

## 第二步：目标与事实基线对齐 (`align`)

1. 若核心资料较多（$\ge 3$ 份），调用 `run_subagents(role="researcher")` 分专题并发提取：
   - **实质性响应与废标红线**（核查属于**技术暗标**还是**明标**；暗标严禁出现投标单位名称、电话、彩色图表或页眉标识）；
   - **详细评审评分表与编制版式要求**（各评分模块、细分考核点、分值权重，以及纸张、行距、页边距、字号、**页数上限**）；
   - **项目工程核心事实与五专业精确工程量**（项目名称、编号、地点、规模、总工期日历天、开竣工日期、估算价/限价、危大工程范围、补疑最新修正口径、各专业分项工程量）。
2. 主控交叉核对（**补疑/变更公告优先级高于原招标正文**！），落盘两份基线文件（子代理启动时内核会自动注入）：
   - `01-requirements.md`：招标要求、评分细则映射与排版红线；
   - `00-facts.md`：全项目唯一合法数据源（含精确工程量、关键工序工期分配及日均产能估算基线，严禁撰稿时编造数据或写错单位）。

---

## 第三步：全局规划拆解与门禁确认 (`plan`)

1. 对照 `01-requirements.md` 与 `00-facts.md` 编写 `02-plan.md`，须明确：
   - 正式标题、选用版式预设（合肥及市政房建技术明标优先选用 `hefei_qingtian_bidding`：A4 / 22磅 / 页边距上2.5下2.5左3.0右2.0 / 一级三号其余四号 / 自动绑定青天 90 分门禁；技术暗标选用 `blind_bidding`；通用非投标报告选用 `standard_report`）；
   - 完整章节目录、各章对招标评分项的踩点映射、篇幅页数分配预算及标准文件命名（**统一使用 `drafts/01-xxx.md`、`drafts/02-xxx.md` 格式**）；
   - 图表配置清单（明确各章插入的组织架构图、工艺流程图、A4 横版总进度横道图、专项断面图及各节工艺参数/验收标准控制表）。
2. **触发自适应门禁**：调用 `ask_user_gate(title="请确认提取的标准要求与文档目录规划", summary=..., review_files=["01-requirements.md", "02-plan.md"])`。若返回 `modified_files` 非空，重读最新文件后再推进。

---

## 第四步：高清制图与总工级反套话撰稿 (`execute`)

1. **先生成高清专业配图**（存于 `assets/*.png`）：
   - 优先使用 `render_charts.py`（传入 `.workspace/charts_spec.json`，暗标必加 `"--blind"`）或编写高分辨率制图脚本；
   - **制图防重号规范**：图片内部标题仅写纯图名（如 `"排水管道施工工艺流程图"`），**切勿在图片内部写死 `"图 3-1"` 等章节序号**。
2. **分批并发撰写总工级章节草稿**（`run_subagents(role="author")`，每批 3~6 章）：
   - 在每个 `author` 子任务的 `prompt` 中明确要求严格执行以下**反套话高标准规范**：
     1. **层级编号不断链**：首行 `# 第X章 章节标题`，二级 `## X.1`，三级 `### X.1.1`，连续编号不跳号；
     2. **总工级三段式结构（严禁超过 280 字的大段散文墙！）**：每节按“**本项目工况痛点锚定**（≤150字） → **定量工艺步骤与机械产能匹配验算**（`- **工序**：...`，核心工序须写出 `清单量 ÷ 计划天数 = 日均强度 ≤ 机械台数 × 台班产能`） → **现行国行地标强条与实测验收控制表**（GFM 管道表）”组织；
     3. **零套话、零模糊逃避词**：严禁堆砌“高度重视、精心组织、科学管理、确保万无一失”等套话；**严禁写“压实度按规范、温度达标、低于规定值不得摊铺、按规范频次检测”**，必须写出具体标准号（如 `CJJ 1-2008`、`GB 50268-2008`、`JGJ 46`、`DB34/T 5040`）与精确阈值（如 `≥98%`、`≥160℃`、`±20mm`）；
     4. **插图与表题唯一规范**：插入图片统一用**单行 `![图 X-Y 图题名称](assets/xxx.png)`，严禁在下方再重复手写一行 `图 X-Y ...`**（编译器已自动用 `![...]` 生成居中图题）；表格正上方写纯文本 `表 X-Y 表名称`（不加粗体星号）；横道图前后包裹 `<!-- page: landscape -->` 与 `<!-- page: portrait -->`。

---

## 第五步：合肥“青天大模型”质检闭环与锁定交付 (`verify`)

> **硬性准入机制**：`compile_word.py` 已接入青天防篡改门禁锁！未取得 `≥ 90.0` 分且零红线的 `.workspace/qingtian_certificate.json`（或拿证后又改动了草稿），Word 编译将被物理拒绝！

1. **第 1 关：运行确定性“青天五维雷达”扫描**：
   - 调用 `run_python` 执行 `qingtian_inspect.py`：`["--mode", "radar", "--drafts-dir", "drafts"]`（暗标加 `"--blind"`）。
   - 脚本自动扫描结构断链、>330 字散文墙、套话口号、“按规范”模糊词、国标引用数、定量产能验算及双重图题，并将快照写入 `.workspace/qingtian_radar.json`。若检出硬伤（如双重图题、缺章、断链、严重散文墙），先快速修复再送专家评审。
2. **第 2 关：派发独立“合肥青天评标质检官”深度量化评审**：
   - 调用 `run_subagents` 启动独立质检角色（`role="inspector"`，`output_file="03-qingtian-review.md"`），在 `role_prompt` / `prompt` 中传入 `qingtian-rubric.md` 的五维百分制量表（完整性 30 + 针对性 25 + 合规性 20 + 可落地性 15 + 规范性 10），逐章通读 `drafts/*.md` 出具结构化评标报告与末尾 JSON 评分卡。
3. **第 3 关：执行终审认证与自动打回重修闭环**：
   - 调用 `run_python` 执行 `qingtian_inspect.py`：`["--mode", "certify", "--drafts-dir", "drafts", "--review-file", "03-qingtian-review.md", "--min-score", "90"]`（暗标加 `"--blind"`）。
   - **若返回 `"passed": false`**：严禁强行编译！立即按返回的 `radar_blocking_issues`、`ai_blocking_defects` 与 `chapter_rework` 派 `author` 子代理重修对应章节——消除散文墙、补齐国标号与产能验算、修正数据口径，随后**重新执行 `radar` → `inspector` 复审 → `certify`**，直至 `"passed": true` 签发 `.workspace/qingtian_certificate.json`！
4. **第 4 关：编译原生 `.docx` 与真实排版页数复核**：
   - 持有效准入证调用 `run_python` 执行 `compile_word.py`：
     `["--drafts-dir", "drafts", "--output", "deliverables/<正式标题>.docx", "--title", "<正式标题>", "--preset", "hefei_qingtian_bidding"]`（暗标传 `"--blind"`；**招标有页数上限时必须追加 `"--max-pages", "<N>"`**）。
   - 若真实排版测页（`audit.real_pages`）超出页数上限，定点精简冗余描述后，必须重新走一遍 `qingtian_inspect.py` 更新草稿 SHA-256 准入证，再重编译直至 `passed: true`！
   - 最终向用户汇报：**青天五维认证得分（`qingtian_gate.final_score`）**、整改消除的专业问题摘要、章节与图表统计、**真实排版页数（`audit.real_pages`）**及 `deliverables/` 成品路径。
