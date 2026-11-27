# 待办事项清单（TODO）

> 详细架构设计与技术实现细则见文档站专篇：[docs/roadmap-todo.md](docs/roadmap-todo.md)

## P0：拉开行业代差的专业杀手锏（优先推进 · 聚焦施组高标准与高评标分）

- [ ] **P0-1 本地历史标书指纹库与“青天防雷同查重雷达”**
  - 实现 `packages/doc_engine/similarity_radar.py`（法定规范术语白名单剥离 + 5-gram Winnowing 指纹集 + 超 28 字雷同长句定位 + `project_key` 同项目重修豁免）
  - 接入 `qingtian_inspect.py --mode radar`（跨项目高重合度直接触发阻断打回改写）与 `compile_word.py`（终审通过后自动归档指纹入库）
- [ ] **P0-2 多模型分工路由（`deepseek-reasoner` 严审 + `flash` 快写 + `Qwen2.5-VL` 识图）**
  - `AppSettings` 与设置弹窗新增深度推理模型槽位（`reasoningModel`，推荐 `deepseek-reasoner`）及独立视觉模型配置组（`visionBaseUrl` / `visionApiKey` / `visionModel`，推荐 `qwen2.5-vl-72b-instruct`）
  - `runtime/model_factory.py` 新增 `ReasoningAwareOpenAIFormatter`，在同轮 `tool_calls` 消息中回传 `reasoning_content`，支持 `inspector` 质检官启用慢思考逐表挑刺，并发撰稿继续走 `deepseek-flash`
- [ ] **P0-3 识图端：接入多模态视觉大模型（如 `Qwen2.5-VL`），专门攻克扫描件与 CAD 施工蓝图**
  - 原生电子版 PDF/Word/Excel（80%）保持本地引擎秒级零成本直读；盖章扫描页与 `@A3.pdf` 施工蓝图（横断面、路面结构、交口竖向、总平图）路由给 VLM 视觉模型深度解析引线、结构层与空间尺寸（本地 RapidOCR 仅作断网保底）
  - 新增内核工具 `inspect_blueprint_vlm`，支持高倍渲染识图与关键节点大样裁剪存入 `assets/` 入标
- [ ] **P0-4 画图端：纯本地精准矢量制图双轨制（禁用文生图大模型，杜绝乱码汉字与暗标废标）**
  - **轨道一（固定标准图走高精参数引擎 `render_charts.py`）**：保留横道图/组织树/工艺流，新增 `labor_curve`（劳动力动态投入柱状-折线包络图）
  - **轨道二（定制构造图由代码大模型动态编写矢量绘图代码）**：新增 `packages/doc_engine/vector_canvas.py` 本地矢量绘图组件库（封装原生宋体/黑体、明标与 `--blind` 纯黑白暗标锁死调色板、尺寸引线、剖面纹样及文字越界/压字几何碰撞自检），针对具体工程动态绘制深基坑支护断面、道路多层结构横断面与路口导改总平面图
  - 新增 `skills/document-authoring/references/domain-checklists.md`（市政 / 房建 / 改造现行有效规范与产能验算模板）

---

## P1 / P2：次优先级体验打磨与商业分发储备（按需推进）

- [ ] **P1-1** 右栏原生支持 `.png` 配图缩放预览与“青天五维评标得分雷达卡”可视化展示
- [ ] **P1-2** 客户端专属高清 `.ico` 应用图标替换与安装包无损瘦身（裁剪多语言包与闲置子依赖）
- [ ] **P1-3** 本机 Word/WPS COM 测页防首次激活弹窗阻塞的短超时强杀兜底
- [ ] **P2-1** 云服务器轻量激活码与 API Key 额度代理网关（面向多同事/外部客户分发）
