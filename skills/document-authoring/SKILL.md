---
name: 复杂标准文档与标书编纂
description: 面向多源参考资料目录，按“离线解析与三级导航 -> 动态提取要求与标准 -> 目录规划与门禁确认 -> 图文宽表分章草稿 -> 双重审校合成标准/暗标 Word (.docx)”五步闭环交付专业出版级文档。
---

# 复杂标准文档与标书编纂作业规程 (`document-authoring`)

当用户提供资料文件夹或参考附件，要求编写施工组织设计、投标技术方案、可行性研究报告、项目实施方案或行业标准报告并交付正式 Word 文档时，严格遵循以下五步闭环执行，并使用 `update_cognitive_steps` 实时更新右栏进度。

---

## 第一步：环境感知与标准化 (`perceive`)

1. 先检查工作区是否已有 `.workspace/parsed/INDEX.md`；若尚未标准化，调用 `scan_directory(target="source")` 概览后调用一次 `ingest_documents()` 完成批量离线转换。
2. **遵循三级高速导航法查阅资料**（严禁对二进制文件调 `read_lines`）：
   - **一级看全貌**：读 `.workspace/parsed/INDEX.md` 识别核心文件（如招标正文、补疑、清单、控价、地勘与设计说明）及对应的 `toc.md`；
   - **二级定页码**：读长文档的 `toc.md` 或用 `search_text(scope="parsed")` 定位关键词页码；
   - **三级精翻页**：用 `read_parsed_pages(doc_path, start_page, end_page)` 直接读取目标物理页；若某张关键扫描页标记“按需待识”，调用 `ocr_pdf_pages` 精准补识；若需对超大 Excel 全表精确汇总，调用 `run_python` 读取原表计算。

---

## 第二步：目标与约束对齐 (`align`)

1. 若核心资料较多（$\ge 3$ 份），调用 `run_subagents(role="researcher")` 分专题并发提取：
   - **实质性响应与废标红线**（特别核查是否属于**技术暗标**——暗标严禁出现投标单位名称、电话、彩色图表或页眉标识！）；
   - **详细评审评分表**（各评分模块、分值权重、评审细则及页数/字号版式要求）；
   - **项目工程核心事实**（项目名称、编号、建设地点、建设规模、计划总工期日历天、合同估算价/最高限价、质量标准、主要专业工程量、适用规范标准号）。
2. 主控交叉核对（注意“补疑/变更公告”优先级高于原招标正文！），汇总写入 `01-requirements.md`。

---

## 第三步：全局规划拆解与门禁确认 (`plan`)

1. 对照 `01-requirements.md` 编写 `02-plan.md`，须明确：
   - 正式标题、选用版式预设（`formal_bidding` 明标公文 / `blind_bidding` 纯黑白技术暗标 / `standard_report` 通用报告）；
   - 完整章节目录及标准文件命名（**统一使用 `drafts/01-xxx.md`、`drafts/02-xxx.md` 格式**，便于体检脚本自动核查是否漏章）；
   - 图表配置计划（明确哪几章需插入施工总进度横道图、项目组织架构图、关键工艺流程图及横向宽表）。
2. **触发自适应门禁**：调用 `ask_user_gate(title="请确认提取的标准要求与文档目录规划", summary=..., review_files=["01-requirements.md", "02-plan.md"])`。若返回 `modified_files` 非空，必须重读最新文件后再推进。

---

## 第四步：图文生成与分治产出草稿 (`execute`)

1. **先用 `render_charts.py` 批量生成高清专业配图**（存于 `assets/*.png`）：
   - 将图表 JSON 配置写入 `.workspace/charts_spec.json`（支持 `type: "gantt" | "org_tree" | "flow"`），调用 `run_python` 执行 `render_charts.py`（暗标务必加 `"--blind"` 参数生成纯黑白线条图！）：
     ```json
     {
       "charts": [
         {"type": "org_tree", "output": "assets/org_tree.png", "title": "施工现场组织管理机构图", "root": "项目经理", "deputies": ["技术负责人", "生产副经理", "安全总监"], "departments": [{"name": "工程技术部", "items": ["测量组", "道路班组", "排水班组"]}, {"name": "质量安全部", "items": ["质检员", "安全员"]}]},
         {"type": "gantt", "output": "assets/schedule_gantt.png", "title": "施工总进度计划横道图", "total_days": 240, "tasks": [{"name": "施工准备与便道导改", "start": 1, "end": 20, "critical": true}, {"name": "雨污水管网施工", "start": 21, "end": 95, "critical": true}]},
         {"type": "flow", "output": "assets/road_flow.png", "title": "沥青混凝土面层施工工艺流程图", "steps": [{"title": "基层验收清扫", "detail": "弯沉及高程复测", "checkpoint": "监理签认"}, {"title": "透层油洒布", "detail": "智能洒布车定量喷洒", "checkpoint": "洒布量检测"}]}
       ]
     }
     ```
2. **分批并发撰写各章 Markdown 草稿**（`run_subagents(role="author")`，每批 3~5 章）：
   - 首行统一为 `# 第X章 章节标题`，节标题为 `## X.1`、`### X.1.1`；
   - 引用已生成的配图：`![1-1 施工现场组织管理机构图](assets/org_tree.png)`；
   - **宽表与横道图横向分节**：对于 $\ge 8$ 列的大表会自动横排；对于施工进度横道图，可在插图前后包裹 `<!-- page: landscape -->` 与 `<!-- page: portrait -->` 实现 A4 横版满幅高清展示；
   - 表格上方紧邻一行写题注 `表 X-X 表名称`，严禁出现 `TODO`、`XXX`、`待补充` 等任何占位符；暗标严禁出现公司署名与联系方式。

---

## 第五步：客观验证与合成交付 (`verify`)

1. **运行 `verify_drafts.py` 硬指标体检**：
   - 调用 `run_python` 执行 `verify_drafts.py`（传入 `["--drafts-dir", "drafts"]`，暗标额外加 `"--blind"`）。若有报错，立即定位修改对应草稿直至 `passed: true`。
2. **启动独立 Reviewer 语义挑刺**：
   - 调用 `run_subagents(role="reviewer")` 在全新上下文逐条核对 `01-requirements.md` 评分点与参数一致性，输出 `03-review.md` 并定点优化草稿。
3. **编译生成原生 `.docx` 并复检**：
   - 调用 `run_python` 执行 `compile_word.py`：
     `["--drafts-dir", "drafts", "--output", "deliverables/<正式标题>.docx", "--title", "<正式标题>", "--preset", "formal_bidding"]`（暗标传 `"--blind"`）。
   - 确认返回 `"passed": true` 后，向用户汇报依据覆盖度、章节与图表统计、预估页数及 `deliverables/` 成品路径。
