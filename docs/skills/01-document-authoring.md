# 01 - 复杂标准长文档编纂 Skill (`document-authoring`)

`skills/document-authoring` 是验证 **Mayi Agent Plus** 五步认知闭环的首个标杆技能包，适用于工程施工组织设计、项目投标技术文件、可行性研究报告、行业深度调研报告等需要**严格响应外部资料约束并输出规范排版 `.docx`** 的复杂长文档任务。

---

## 一、技能包目录组成

```text
skills/document-authoring/
├── SKILL.md                    # 核心作业规程（触发条件、五步标准产物格式、禁令清单）
├── scripts/
│   ├── compile_word.py         # 将工作区章节 Markdown 集合 + 版式配置编译为原生 .docx
│   └── verify_drafts.py        # 确定性硬指标审计（章节完整性、占位符扫描、表格列数、字数统计）
└── presets/
    ├── standard_report.json    # 通用商务/技术报告标准版式（A4、微软雅黑/宋体、自动目录）
    └── formal_bidding.json     # 招投标/公文严谨版式（宋体/黑体、固定行距、跨页表头重复）
```

---

## 二、端到端执行流水线

```mermaid
sequenceDiagram
    participant U as 用户 (双栏工作台)
    participant O as 主控 Orchestrator
    participant P as doc_engine (离线解析/编译)
    participant SA as 并发 Sub-Agents

    U->>O: 提供资料文件夹路径 + 编纂目标
    Note over O,P: Step 1: 环境感知与标准化
    O->>P: scan_directory + ingest_documents
    P-->>O: 生成 .workspace/parsed/*/full.md + manifest.json

    Note over O,SA: Step 2: 目标与约束对齐
    O->>SA: 按资料分片并发提取硬性条款、评分点、格式与核心数据
    SA-->>O: 汇总写入 .workspace/01-requirements.md (含源文件页码定位)

    Note over O,U: Step 3: 全局规划拆解 + 自适应门禁
    O->>O: 编写完整目录、章节评分点映射与版式选择 -> .workspace/02-plan.md
    O->>U: ask_user 触发门禁卡片，右栏实时展示大纲供审阅/微调
    U-->>O: 点击“确认执行”（或保存微调后的 02-plan.md）

    Note over O,SA: Step 4: 分治执行产出
    O->>SA: 按章节并发启动独立子代理，各自写入 .workspace/drafts/chXX.md
    SA-->>O: 返回各章字数与完成摘要

    Note over O,P: Step 5: 客观验证与收敛交付
    O->>P: 运行 verify_drafts.py 执行确定性硬指标体检
    O->>SA: 启动独立干净上下文 Reviewer 子代理对照 01-requirements.md 逐条核查
    SA-->>O: 输出修订意见 -> 定点修补对应章节 chXX.md
    O->>P: 运行 compile_word.py 合成原生排版 Word (.docx)
    P-->>U: 交付 .workspace/deliverables/<文档标题>.docx
```

---

## 三、受控 Markdown 与原生 Word 映射规则

为保证模型编写高效且最终 `.docx` 排版严谨，`docx_builder.py` 支持以下确定性语法映射：

- **章节标题层级**：各章文件首行 `# 第X章 标题` 映射为 Word 原生 `Heading 1`（自动进入 TOC 目录域），`##` / `###` / `####` 依次映射为 `Heading 2 ~ 4`；
- **富文本强调**：`**加粗**`、`*斜体*` 映射为原生 Run 字体属性并自动分别设置中文字体（`eastAsia`）与西文字体；
- **原生专业表格**：标准 GFM 管道表格自动渲染为全边框自适应宽度 Word 原生表格，首行默认居中加粗底纹，并开启 `w:tblHeader` **跨页自动重复表头**；表格上方紧邻的 `表 X-X 标题` 段落自动设为 `Caption` 题注并开启“与下段同页（`keep_with_next`）”；
- **插图与图题**：`![图题说明](assets/xxx.png)` 自动居中插入工作区图片并生成规范图题段落。
