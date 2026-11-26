---
name: 复杂标准文档与标书编纂
description: 面向多源参考资料目录，按“离线解析 -> 动态提取要求与标准 -> 编写目录与门禁确认 -> 分章并发草稿 -> 审校整合成标准 Word (.docx)”五步闭环交付高质量文档。
---

# 复杂标准文档与标书编纂作业规程 (`document-authoring`)

当用户提供资料文件夹或参考附件，要求编写施工组织设计、投标技术方案、可行性研究报告、项目实施方案或行业标准报告并交付正式 Word 文档时，严格遵循本五步流程执行，并使用 `update_cognitive_steps` 实时更新右栏进度。

---

## 第一步：环境感知与标准化 (`perceive`)

1. 调用 `scan_directory(target="source")` 查看用户资料目录内的全部文件清单与类型。
2. 调用一次 `ingest_documents()` 将所有 PDF / DOCX / DOC / XLSX / XLS / TXT / CSV 批量离线转换为 `.workspace/parsed/` 下的带页码锚点 Markdown 缓存。
3. 检查返回的 `batch_results` 与告警：
   - 后续阅读和搜索 **100% 针对 `.workspace/parsed/*/full.md`**（使用 `search_text(scope="parsed")` 和 `read_lines`），切勿尝试直接对二进制文件调 `read_lines`。
   - 若遇到超大工程量清单或财务表格（提示超过预览行上限），在需要汇总核算时调用 `run_python` 使用 `openpyxl` 直接读取原表计算确定值。

---

## 第二步：目标与约束对齐 (`align`)

1. 若已转换文档较多（$\ge 3$ 份或总长较大），优先调用 `run_subagents` 派发 `role="researcher"` 的并发子代理按文档或专题分头检索提炼：
   - 废标项 / 强制性实质响应条款；
   - 评分办法与分值权重（暗标/明标格式约束、页数限制、字体行距要求）；
   - 核心事实数据（项目名称、地点、工期、质量目标、关键工程量、技术规范编号）。
2. 主控汇总消歧后，将完整结果写入工作区 `01-requirements.md`，结构须包含：
   - **一、项目核心事实台账**（每项附 `[源文件名 P页码]`）
   - **二、强制性/废标性约束与版式要求**
   - **三、评分标准与章节响应映射要求**
   - **四、待确认事项或合理假设说明**（严禁凭空捏造缺失参数）

---

## 第三步：全局规划拆解与门禁确认 (`plan`)

1. 严格对照 `01-requirements.md` 编写 `02-plan.md`，明确：
   - 交付文档正式标题、副标题与选用的版式预设（`formal_bidding` 用于标书/公文，`standard_report` 用于通用技术报告）；
   - 完整章节目录结构（建议约定章节文件名：`drafts/01-xxx.md`、`drafts/02-xxx.md`...）；
   - 每一章必须覆盖的评分点、核心数据表格及目标字数。
2. **触发自适应门禁**：调用 `ask_user_gate(title="请确认提取的标准要求与文档目录规划", summary=..., review_files=["01-requirements.md", "02-plan.md"])`。
   - 若返回 `modified_files` 非空或用户附带修改意见，**先用 `read_lines` 重新读取更新后的文件**再推进第四步。

---

## 第四步：分治执行产出草稿 (`execute`)

1. 按 `02-plan.md` 规划的章节，分批调用 `run_subagents`（`role="author"`，每批最多 4~6 章，指定各自独立的 `output_file`，例如 `"drafts/01-overview.md"`）。
2. **受控 Markdown 写作规范（务必传达给每个撰稿子代理）**：
   - 每个章节文件首行必须使用一级标题：`# 第X章 章节标题`，节标题依次使用 `## X.1`、`### X.1.1`；
   - 关键工程参数、资源配置计划、进度节点、制度标准尽量结合**标准 GFM 管道表格**呈现，表格上方一行写明题注（如 `表 1-1 主要工程量汇总表`）；
   - 内容必须充实具体、紧扣本项目特征与 `01-requirements.md` 中的真实参数，**严禁出现 `TODO`、`XXX`、`待补充`、`此处略` 等任何未完成占位符**。

---

## 第五步：客观验证与合成交付 (`verify`)

1. **确定性脚本硬检**：调用 `run_python` 执行本技能内置的 `verify_drafts.py`：
   - `script_path`: 使用 `load_skill_detail` 返回的 `verify_drafts.py` 路径
   - `args`: `["--drafts-dir", "drafts"]`
   - 若报告 `passed: false`，立即针对报错文件与行号调用 `edit_workspace_file` 修复。
2. **独立 Reviewer 语义挑刺**：调用 `run_subagents` 启动一个 `role="reviewer"` 的独立子代理，令其对比 `01-requirements.md` 与 `drafts/*.md` 核查是否有漏响应评分点或前后工期/数值不一致，并将审查意见写入 `03-review.md`；对发现的实质性问题做定点修补。
3. **编译生成标准原生 Word 文档**：调用 `run_python` 执行 `compile_word.py`：
   - `args`: `["--drafts-dir", "drafts", "--output", "deliverables/<正式文档名>.docx", "--title", "<正式文档标题>", "--preset", "formal_bidding"]`
4. 确认脚本输出 `"passed": true` 且生成字节数正常后，向用户汇报提取依据覆盖情况、总字数、预估页数及交付文件路径。
