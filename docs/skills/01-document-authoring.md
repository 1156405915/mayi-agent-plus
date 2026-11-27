# 01 - 复杂标准长文档编纂 Skill (`document-authoring`)

`skills/document-authoring` 是验证 **Mayi Agent Plus** 五步认知闭环的首个标杆技能包，适用于工程施工组织设计（支持明标与严格技术暗标）、项目投标技术方案、可行性研究报告、行业深度调研报告等需要**严格响应上百份外部资料约束，并输出图文宽表兼备的出版级 `.docx`** 的复杂任务。

---

## 一、技能包目录组成

```text
skills/document-authoring/
├── SKILL.md                        # 核心作业规程（三级高速导航、反套话总工级撰稿、青天五维质检锁死闭环）
├── references/
│   └── qingtian-rubric.md          # 合肥“青天大模型”评标五维百分制量表与反套话三段式标准
├── scripts/
│   ├── render_charts.py            # 基于 Pillow 原生绘制高分辨率甘特横道图、组织架构图、施工工艺流程图
│   ├── verify_drafts.py            # 基础确定性体检（缺章对齐、占位符拦截、跨章总工期一致性、暗标红线）
│   ├── qingtian_inspect.py         # 青天五维确定性雷达（--mode radar）与防篡改准入证签发（--mode certify）
│   └── compile_word.py             # 校验 ≥90 分 SHA-256 准入证后编译横竖分节 .docx 并执行真机测页
└── presets/
    ├── hefei_qingtian_bidding.json # 合肥公共资源交易标准版式（A4/22磅/上2.5下2.5左3.0右2.0/三号四号/强制青天门禁）
    ├── formal_bidding.json         # 正式投标明标/政务公文版式（默认绑定青天 90 分准入门禁）
    ├── blind_bidding.json          # 技术暗标安全合规版式（纯黑白零底纹、无署名页眉、绑定青天准入门禁）
    └── standard_report.json        # 通用非投标现代商务/技术研报版式
```

---

## 二、端到端五步执行流水线

```mermaid
sequenceDiagram
    participant U as 用户 (双栏工作台)
    participant O as 主控 Orchestrator
    participant P as doc_engine & 技能脚本
    participant SA as 并发 Sub-Agents

    U->>O: 挂载复杂资料目录 + 编纂目标
    Note over O,P: Step 1: 环境感知与三级高速导航
    O->>P: ingest_documents -> 生成 INDEX.md / toc.md / full.md
    O->>P: INDEX.md -> toc.md -> read_parsed_pages 秒级定位核心页

    Note over O,SA: Step 2: 目标与约束对齐
    O->>SA: 并发提炼废标红线(明/暗标)、评分标准、工期造价与工程量
    SA-->>O: 汇总写入 01-requirements.md (附源文件名与物理页码)

    Note over O,U: Step 3: 全局规划拆解 + 自适应门禁
    O->>O: 编写章节目录、评分点映射与配图规划 -> 02-plan.md
    O->>U: ask_user_gate 弹出门禁卡，支持用户在右栏直接微调大纲保存
    U-->>O: 点击“确认放行”

    Note over O,SA: Step 4: 高清图表绘制 + 分章并发撰稿
    O->>P: 运行 render_charts.py 生成甘特图 / 组织架构图 / 工艺流程图 (assets/*.png)
    O->>SA: 并发启动撰稿子代理（自动获注全局底座），各自输出 drafts/0X-xxx.md
    SA-->>O: 返回各章字数与图表/宽表嵌入状态

    Note over O,P: Step 5: 双重客观验证与原生 Word 编译交付
    O->>P: 运行 verify_drafts.py 硬检缺章、占位符、工期矛盾与暗标红线
    O->>SA: 启动独立干净上下文 Reviewer 子代理逐条挑刺 -> 定点修补
    O->>P: 运行 compile_word.py 合成横竖自适应分节原生 Word (.docx)
    P-->>U: 交付 deliverables/<文档标题>.docx
```

---

## 三、M4 出版级排版与图表核心能力

### 1. 三类高分辨率工程/商务配图确定性生成（`render_charts.py`）
无需依赖外网绘图接口或浏览器截图，直接通过 JSON 规格调用系统原生中文字体生成印刷级 PNG 配图（同时支持彩色明标与 `--blind` 纯黑白斜线阴影暗标主题）：
- **`gantt`（施工总进度横道图）**：自动计算时间刻度网格、持续天数列、关键线路高亮与工期跨度标注；
- **`org_tree`（组织管理/质安保证体系树图）**：决策层 $\rightarrow$ 副职层 $\rightarrow$ 职能部门 $\rightarrow$ 施工班组四级自动对称布局与正交连线；
- **`flow`（关键施工工艺与验收控制流程图）**：S 型多行蛇形流向卡片，内置 `★ 验收点` 质量关卡徽标。

### 2. 横竖版自适应分节与防劈行专业表格（`docx_builder.py`）
- **自动宽表横排**：当 Markdown 表格列数 $\ge 8$ 列（如多阶段设备配置表、工程量分解表）时，编译器自动新建横向 A4 分节（版心宽从 15.6cm 扩展至 24.3cm），表后自动切回竖向 A4；
- **显式横版指令**：在甘特横道图或大图前后书写 `<!-- page: landscape -->` 与 `<!-- page: portrait -->`，即可让大幅横道图满幅高清呈现；
- **原生细节保护**：所有表格默认注入 `<w:tblHeader/>`（跨页自动重复表头）与 `<w:cantSplit/>`（严禁单行在页底被劈成上下两半）。

### 3. 技术暗标“防废标”全链路闭环（`blind_bidding`）
针对公共资源交易技术暗标严苛的废标审查：
- **图表层**：`render_charts.py --blind` 输出纯白底、纯黑框线、斜线纹理区分非关键工序的无彩图表；
- **体检层**：`verify_drafts.py --blind` 自动拦截正文误写的公司署名、11 位手机号、电子邮箱及跨章工期超限；
- **编译层**：`compile_word.py --blind` 强制移除封面副标题与页眉文字，锁定全书 `#000000` 纯黑宋体/黑体，并在编译后由 `audit_docx_file(blind_mode=True)` 二次扫描所有 Run 色彩值与页眉段落。

---

## 四、M6 合肥“青天大模型”评标质检员与防篡改准入证闭环

针对传统大模型编写施组容易退化为“大段散文墙、堆砌万能套话、关键参数用‘按规范要求’糊弄、工程量与机械工期不算账”的行业痛点，系统对标《合肥公共资源交易大模型开发建设与应用管理暂行办法》（合公信〔2025〕82号）构建了**硬算法雷达 + 独立 AI 质检官 + SHA-256 准入证锁死**三道防线：

1. **总工级反套话“三段式”撰稿规程**：强制要求各施工分项按“工况痛点锚定（≤150字）$\rightarrow$ 定量工艺步骤与机械台班产能匹配验算（`清单量 ÷ 工期 = 日均强度 ≤ 机械台数 × 台班产能`）$\rightarrow$ 现行国行地标强条与量化验收控制表”组织，单段连续纯叙述文字严禁超过 280 字。
2. **五维确定性雷达（`qingtian_inspect.py --mode radar`）**：从完整性（30分）、针对性（25分）、合规性（20分）、可落地性（15分）、规范性（10分）五个维度毫秒级扫描结构断链、套话口号、无数值“按规范”模糊词、现行规范引用数、产能算式密度与双重重复图题。
3. **独立首席评标质检官（`role="inspector"`）与防放水准入锁**：
   - `inspector` 子代理在干净上下文中通读全稿并输出 `03-qingtian-review.md` 及五维 JSON 评分卡；
   - `qingtian_inspect.py --mode certify` 执行“AI 得分 + 底层雷达分”加权防放水校准，仅当**零硬伤且综合得分 $\ge 90.0$ 分**时签发绑定全稿 SHA-256 的 `.workspace/qingtian_certificate.json`；
   - `compile_word.py` 在导出投标文档前强制核验准入证哈希——未达标或拿证后私改草稿一律物理拒签（退出码 `3`），倒逼自动返工重修直至真正达到高标准。
