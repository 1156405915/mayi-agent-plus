# 🐜 Mayi Agent Plus

新一代通用复杂任务智能体桌面工作台：**基于 AgentScope 极简薄内核 + 离线文档感知缓存底座 + 可插拔 Skill 技能体系**。

核心设计围绕通用**五步底层认知闭环**：
1. **环境感知与标准化**（秒级目录扫描 + PDF/Word/Excel 离线批量转带页码锚点的 Markdown 缓存）
2. **目标与约束对齐**（毫秒级纯文本检索与并发提炼，输出 `01-requirements.md`）
3. **全局规划拆解 + 自适应门禁**（生成 `02-plan.md`，复杂任务支持人工一键确认或在线微调后再铺开执行）
4. **分治执行产出**（无锁并发子代理按独立文件物理隔离编写草稿、跑脚本算数据）
5. **客观验证与收敛交付**（确定性脚本硬指标体检 + 独立干净上下文 Reviewer 审查 $\rightarrow$ 合成标准交付物）

---

## 📂 目录结构

```text
mayi-agent-plus/
├── src/                        # Electron 极简宿主与双栏工作台 UI (TypeScript + Vue 3)
│   ├── shared/protocol.ts      # 纯通用前后端协议（零行业字段耦合）
│   ├── main/                   # 会话存储、工作区沙箱与 Python NDJSON 进程桥接
│   ├── preload/                # 安全上下文桥接 API
│   └── renderer/               # 左栏流式对话（含门禁确认卡）+ 右栏五步进度与 Markdown 实时预览/微调
│
├── runtime/                    # AgentScope 通用薄内核 (Python)
│   ├── main.py                 # 标准 NDJSON 请求/事件流与权限门禁双向通道
│   ├── model_factory.py        # OpenAI/DeepSeek 兼容模型适配、截断检测与退避重试
│   ├── orchestrator.py         # 主控 ReActAgent 组装与五步认知系统提示词
│   ├── skill_loader.py         # 标准 SKILL.md 动态发现与按需加载
│   ├── tools_fs.py             # 原子工具 1：目录索引、全文搜索、文件读写编辑
│   ├── tools_ingest.py         # 原子工具 2：批量文档离线转结构化 Markdown 缓存
│   ├── tools_exec.py           # 原子工具 3：受控 Python 脚本执行（计算/制图/编译）
│   ├── tools_subagent.py       # 原子工具 4：无状态并发子代理（独立上下文 Map-Reduce）
│   └── tools_human.py          # 原子工具 5：五步进度同步与自适应门禁确认
│
├── packages/
│   └── doc_engine/             # 纯确定性文档处理引擎（零 Agent 状态耦合，支持独立 CLI 调用）
│       ├── parser.py           # PDF(PDFium+RapidOCR) / DOCX / DOC / XLSX / XLS -> 带锚点 Markdown
│       ├── docx_builder.py     # 标准 Markdown + 版式配置 -> 原生高质量 Word (.docx)
│       └── docx_auditor.py     # 草稿与成品 Word 硬指标确定性体检器
│
├── skills/                     # 可插拔领域技能包
│   └── document-authoring/     # [01] 首个标杆技能：复杂标准文档/方案/标书全流程编纂
│
└── docs/                       # Docsify 在线架构与实战文档站
    ├── architecture.md         # 五步认知闭环与三层通用架构详解
    ├── design-reflection.md    # 架构演进复盘：从状态锁泥潭到极简解耦
    ├── engine/
    │   ├── doc-engine.md       # doc_engine 离线多格式解析、Word 编译与 248 文件压测踩坑实录
    │   └── runtime-and-workbench.md # AgentScope 薄内核五大原子工具与双栏工作台协同机制
    └── skills/
        └── 01-document-authoring.md # 首个标杆长文档编纂 Skill 实现详解
```

---

## 🚀 快速开始

### 1. 安装依赖与准备内嵌 Python 环境

```bash
pnpm install
```

> `postinstall` 会自动通过受管 `uv` 创建项目私有 `.venv` 并安装 AgentScope 与离线文档解析依赖，无需管理员权限，不污染系统环境。

### 2. 启动双栏工作台开发模式

```bash
pnpm dev
```

### 3. 运行自动化测试与类型检查

```bash
pnpm typecheck
pnpm test
.venv\Scripts\python.exe -m unittest discover -s tests/python -p "test_*.py"
```

### 4. 在线阅读 `docs/` 架构与实战文档

```bash
pnpm docs:dev
# （注：`pnpm docs` 是 pnpm 官方内置跳转 npmjs 的保留命令，本地预览请使用 `pnpm docs:dev` 或 `pnpm run docs`）
```

浏览器打开 `http://127.0.0.1:3080` 即可查看完整架构图解、设计复盘与新增 Skill 标准 SOP。
