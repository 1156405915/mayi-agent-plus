"""合肥“青天大模型”技术标（施工组织设计）五维量化雷达与防篡改准入证引擎。

参考依据：合肥市公管局《合肥公共资源交易大模型开发建设与应用管理暂行办法》（合公信〔2025〕82号）
AI“类人”评审五大核心维度：
1. 完整性（30分）：评标办法全覆盖、章节层级编号连续不断链、规划图表零缺漏；
2. 针对性（25分）：紧扣工程特征与真实清单数据、严惩万能模板套话；
3. 合规性（20分）：现行国行地标强条准确引用、拦截“按规范/达标即可”模糊逃避词、严守明暗标红线；
4. 可落地性（15分）：定量工艺参数密度、关键工序“工程量÷工期→机械台班产能”匹配验算；
5. 规范性（10分）：杜绝大段不分段“散文墙”、结构化表格承载、图表编号连续且无双重重复题注。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from .docx_auditor import IMAGE_LINK_RE, audit_markdown_drafts

# 万能空洞套话词库：青天评标针对性审查重点扣分对象
CLICHE_PHRASES = (
    "高度重视",
    "精心组织",
    "科学管理",
    "统筹安排",
    "全力以赴",
    "确保万无一失",
    "做到万无一失",
    "保质保量",
    "狠抓落实",
    "周密部署",
    "真抓实干",
    "常抓不懈",
    "警钟长鸣",
    "精益求精",
    "齐抓共管",
)
CLICHE_RE = re.compile("|".join(re.escape(w) for w in CLICHE_PHRASES))

# “按规范/达标即可”模糊逃避词：若所在句既无具体标准编号也无量化数值，则判定为糊弄性表述
VAGUE_COPOUT_RE = re.compile(
    r"(?:压实度按规范|温度达标|按配合比控制|低于规定值不得|不低于规范要求|按规范频次检测|"
    r"降至规范允许值|符合相关规范要求|满足相关规范规定|按有关规定执行|按规范要求执行|"
    r"配备充足的?(?:机械|人员|设备)|合理安排(?:施工|工期|机械|人员))"
)

# 国家/行业/安徽及合肥地方现行技术标准与法规识别正则
STANDARD_CODE_RE = re.compile(
    r"(?:GB(?:/T)?|CJJ(?:/T)?|JGJ(?:/T)?|JTG(?:/T)?|DB34(?:/T)?|CJ/T|NY/T|SL|DL/T|HJ)\s*[\d\-—\.]+|"
    r"建设部令第\s*\d+\s*号|建质〔\d{4}〕\d+号|合治欠发〔\d{4}〕\d+号|合公信〔\d{4}〕\d+号"
)

# 定量工程技术参数识别（百分比、强度、温度、尺寸偏差、仪器精度、机械吨位/功率、工期节点）
QUANT_PARAM_RE = re.compile(
    r"(?:≥|≤|±|>|<)\s*\d+(?:\.\d+)?\s*(?:%|‰|MPa|kPa|mm|cm|m|℃|dB|h|min|d|t|kW)?|"
    r"\d+(?:\.\d+)?\s*(?:～|~|-)\s*\d+(?:\.\d+)?\s*(?:%|MPa|kPa|mm|cm|m|℃|遍|min|h|L/m²)|"
    r"\d+(?:\.\d+)?\s*(?:MPa|kPa|℃|dB\(A\)|L/m²|t|kW|m³/d|m²/d|m/d|车次/d)|"
    r"\bD\d{1,3}\s*(?:—|-|~|～)\s*D\d{1,3}\b"
)

# 关键工序“工程量÷工期→日均强度→机械台班产能”可落地性验算特征识别
CAPACITY_CALC_RE = re.compile(
    r"(?:日均(?:出土|开挖|摊铺|安装|完成|施工|外运|产能|强度)|"
    r"台班(?:产能|效率|产量|定额)|单机(?:日产|台班|产能)|"
    r"\d+(?:\.\d+)?\s*(?:m³/d|m²/d|m/d|t/d|m³/台班|m²/台班|m/台班|车/d|车次/d)|"
    r"产能(?:验算|校核|匹配|复核)|工效(?:验算|校核|匹配))"
)

HEADING_LINE_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
H1_NUM_RE = re.compile(r"^第\s*(\d+)\s*章\b")
SEC_NUM_RE = re.compile(r"^(\d+(?:\.\d+)+)\b")
TABLE_CAPTION_LINE_RE = re.compile(r"^(\*{0,2})表\s*(\d+)[-.](\d+)\s+(.+?)(\*{0,2})$")
FIG_CAPTION_LINE_RE = re.compile(r"^(\*{0,2})图\s*(\d+)[-.](\d+)\s+(.+?)(\*{0,2})$")


def compute_drafts_sha256(draft_files: list[Path], workspace_dir: Path | None = None) -> str:
    """计算全部章节草稿文件名与正文的确定性 SHA-256 摘要，防止质检通过后草稿被私自改动或跳检编译。"""
    hasher = hashlib.sha256()
    sorted_files = sorted(draft_files, key=lambda p: p.name)
    for fp in sorted_files:
        rel = (
            fp.resolve().relative_to(workspace_dir.resolve()).as_posix()
            if workspace_dir and fp.resolve().is_relative_to(workspace_dir.resolve())
            else fp.name
        )
        content = fp.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").strip()
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(content.encode("utf-8"))
        hasher.update(b"\x00")
    return hasher.hexdigest()


def _extract_project_anchors(workspace_dir: Path | None) -> list[str]:
    """从 00-facts.md 或 01-requirements.md 提取项目专属地名、编号与精确工程量特征词，用于评估方案针对性。"""
    if workspace_dir is None:
        return []
    anchors: set[str] = set()
    for fname in ("00-facts.md", "01-requirements.md"):
        fp = workspace_dir / fname
        if not fp.is_file():
            continue
        txt = fp.read_text(encoding="utf-8", errors="replace")
        # 提取项目编号（如 2026BFJGZ50374）
        for code in re.findall(r"\b20\d{2}[A-Z]{3,8}\d{3,8}\b", txt):
            anchors.add(code)
        # 提取带小数点的精确清单工程量（如 58128.470、917.78、8723.310）
        for qty in re.findall(r"\b\d{2,6}\.\d{2,3}\b", txt):
            if not qty.endswith(".00") and not qty.endswith(".000"):
                anchors.add(qty.rstrip("0").rstrip("."))
            anchors.add(qty)
        # 提取典型道路/交口/敏感管线专有名词
        for road in re.findall(r"[\u4e00-\u9fff]{2,5}(?:路|街|大道|地块|交口|支路)", txt):
            if road not in {"规划支路", "城市支路", "市政道路", "既有道路", "施工道路", "临时道路"}:
                anchors.add(road)
    return sorted(a for a in anchors if len(a) >= 3)[:40]


def run_qingtian_radar(
    draft_files: list[Path],
    workspace_dir: Path | None = None,
    blind_mode: bool = False,
    wall_of_text_limit: int = 330,
) -> dict[str, Any]:
    """
    执行合肥“青天大模型”确定性五维雷达扫描。

    输出结构包含：
    - 五维算法基线得分（满分 100：完整性 30 + 针对性 25 + 合规性 20 + 可落地性 15 + 规范性 10）；
    - `blocking_issues`：一票否决硬伤列表（存在任一硬伤则禁止签发准入证）；
    - `warnings`：扣分改进建议列表（精确到文件与行号）；
    - `chapter_metrics`：各章结构化密度、套话数、模糊词、国标数、散文墙段落数等客观画像。
    """
    base_audit = audit_markdown_drafts(draft_files, workspace_dir=workspace_dir, blind_mode=blind_mode)
    blocking_issues: list[dict[str, Any]] = [
        item for item in base_audit["issues"] if item.get("severity") == "error"
    ]
    warnings: list[dict[str, Any]] = [
        item for item in base_audit["issues"] if item.get("severity") != "error"
    ]

    if not draft_files:
        return {
            "passed_radar": False,
            "radar_score": 0.0,
            "dimension_scores": {
                "completeness": 0.0,
                "specificity": 0.0,
                "compliance": 0.0,
                "feasibility": 0.0,
                "normativity": 0.0,
            },
            "blocking_issues": blocking_issues,
            "warnings": warnings,
            "chapter_metrics": [],
        }

    project_anchors = _extract_project_anchors(workspace_dir)
    referenced_images: set[str] = set()
    chapter_metrics: list[dict[str, Any]] = []

    total_cliches = 0
    total_vague_copouts = 0
    total_wall_paragraphs = 0
    total_severe_walls = 0
    total_tables = 0
    total_standard_codes: set[str] = set()
    total_quant_params = 0
    total_capacity_calcs = 0
    heading_chain_breaks = 0
    duplicate_fig_captions = 0
    table_caption_format_errors = 0

    expected_chapter_idx = 1

    for path in sorted(draft_files, key=lambda p: p.name):
        rel_name = (
            path.resolve().relative_to(workspace_dir.resolve()).as_posix()
            if workspace_dir and path.resolve().is_relative_to(workspace_dir.resolve())
            else path.name
        )
        raw_text = path.read_text(encoding="utf-8", errors="replace")
        lines = raw_text.splitlines()
        clean_chars = len(re.sub(r"\s+", "", raw_text))

        ch_num: int | None = None
        expected_h2 = 0
        expected_h3_by_h2: dict[int, int] = {}

        ch_cliches: list[str] = []
        ch_copouts: list[dict[str, Any]] = []
        ch_walls: list[dict[str, Any]] = []
        ch_standards = set(STANDARD_CODE_RE.findall(raw_text))
        total_standard_codes.update(ch_standards)

        ch_quant_count = len(QUANT_PARAM_RE.findall(raw_text))
        ch_capacity_count = len(CAPACITY_CALC_RE.findall(raw_text))
        total_quant_params += ch_quant_count
        total_capacity_calcs += ch_capacity_count

        ch_anchor_hits = [a for a in project_anchors if a in raw_text]

        # 统计表格数（连续管道行记为 1 张表）与段落散文墙
        in_table = False
        in_code = False
        ch_table_count = 0
        para_buf: list[tuple[int, str]] = []
        prev_non_empty_img: tuple[int, str] | None = None
        seen_table_nums: list[int] = []

        def _flush_paragraph() -> None:
            nonlocal total_wall_paragraphs, total_severe_walls
            if not para_buf:
                return
            start_ln = para_buf[0][0]
            joined = "".join(seg for _, seg in para_buf)
            # 排除图片行、HTML 注释指令行与短题注行
            if joined.startswith("![") or joined.startswith("<!--"):
                para_buf.clear()
                return
            char_len = len(re.sub(r"\s+", "", joined))
            if char_len > wall_of_text_limit:
                total_wall_paragraphs += 1
                is_severe = char_len > 420
                if is_severe:
                    total_severe_walls += 1
                ch_walls.append({"line": start_ln, "chars": char_len, "preview": joined[:55] + "..."})
            para_buf.clear()

        for ln, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()

            if stripped.startswith("```"):
                _flush_paragraph()
                in_code = not in_code
                continue
            if in_code:
                continue

            # 识别管道表格块
            if stripped.startswith("|") and stripped.endswith("|"):
                _flush_paragraph()
                if not in_table:
                    in_table = True
                    ch_table_count += 1
            else:
                in_table = False

            if not stripped:
                _flush_paragraph()
                continue

            # 1. 检查标题层级与编号断链（青天结构化抓取生死线）
            hm = HEADING_LINE_RE.match(stripped)
            if hm:
                _flush_paragraph()
                prev_non_empty_img = None
                level = len(hm.group(1))
                title_txt = hm.group(2).strip()
                if level == 1:
                    m1 = H1_NUM_RE.match(title_txt)
                    if m1:
                        ch_num = int(m1.group(1))
                        if ch_num != expected_chapter_idx:
                            heading_chain_breaks += 1
                            blocking_issues.append({
                                "file": rel_name,
                                "line": ln,
                                "severity": "error",
                                "message": f"[青天结构断链] 一级章号跳号：期望 `第{expected_chapter_idx}章`，实际为 `{title_txt}`",
                            })
                        expected_chapter_idx = ch_num + 1
                    else:
                        expected_chapter_idx += 1
                elif level == 2:
                    sm = SEC_NUM_RE.match(title_txt)
                    if sm:
                        parts = [int(x) for x in sm.group(1).split(".")]
                        if len(parts) == 2:
                            if ch_num is not None and parts[0] != ch_num:
                                heading_chain_breaks += 1
                                blocking_issues.append({
                                    "file": rel_name,
                                    "line": ln,
                                    "severity": "error",
                                    "message": f"[青天结构断链] 二级节号 `{sm.group(1)}` 与所属第 {ch_num} 章不匹配",
                                })
                            expected_h2 += 1
                            if parts[1] != expected_h2:
                                heading_chain_breaks += 1
                                blocking_issues.append({
                                    "file": rel_name,
                                    "line": ln,
                                    "severity": "error",
                                    "message": f"[青天结构断链] 二级节号跳号或重号：期望 `{ch_num or parts[0]}.{expected_h2}`，实际为 `{title_txt}`",
                                })
                                expected_h2 = parts[1]
                elif level == 3:
                    sm = SEC_NUM_RE.match(title_txt)
                    if sm:
                        parts = [int(x) for x in sm.group(1).split(".")]
                        if len(parts) == 3:
                            h2_idx = parts[1]
                            expected_h3 = expected_h3_by_h2.get(h2_idx, 0) + 1
                            expected_h3_by_h2[h2_idx] = expected_h3
                            if parts[2] != expected_h3:
                                heading_chain_breaks += 1
                                warnings.append({
                                    "file": rel_name,
                                    "line": ln,
                                    "severity": "warning",
                                    "message": f"三级小节编号不连续：期望 `{parts[0]}.{h2_idx}.{expected_h3}`，实际为 `{title_txt}`",
                                })
                                expected_h3_by_h2[h2_idx] = parts[2]
                continue

            # 2. 检查图片引用与双重题注缺陷（避免 docx_builder 自动 alt 题注 + 下方手写图题重复渲染）
            img_matches = IMAGE_LINK_RE.findall(stripped)
            if img_matches:
                _flush_paragraph()
                for alt_txt, img_src in img_matches:
                    referenced_images.add(img_src.strip().replace("\\", "/"))
                    if alt_txt.strip():
                        prev_non_empty_img = (ln, alt_txt.strip())
                    else:
                        prev_non_empty_img = None
                continue

            fig_cap_m = FIG_CAPTION_LINE_RE.match(stripped)
            if fig_cap_m:
                _flush_paragraph()
                if prev_non_empty_img is not None:
                    duplicate_fig_captions += 1
                    blocking_issues.append({
                        "file": rel_name,
                        "line": ln,
                        "severity": "error",
                        "message": (
                            f"[双重图题冲突] 第 {prev_non_empty_img[0]} 行图片 `![{prev_non_empty_img[1]}]` 已由编译器自动生成居中图题，"
                            f"第 {ln} 行又重复手写 `{stripped}`，会导致 Word 出现两行重复图题。请仅保留 `![图 X-Y 名称](assets/xxx.png)` 单行写法并删去下方手写行。"
                        ),
                    })
                prev_non_empty_img = None
                continue

            prev_non_empty_img = None

            # 3. 检查表题注规范与连续性
            tbl_cap_m = TABLE_CAPTION_LINE_RE.match(stripped)
            if tbl_cap_m:
                _flush_paragraph()
                lead_star, tbl_ch, tbl_seq, _tbl_title, tail_star = tbl_cap_m.groups()
                if lead_star or tail_star:
                    table_caption_format_errors += 1
                    warnings.append({
                        "file": rel_name,
                        "line": ln,
                        "severity": "warning",
                        "message": f"表题注请使用纯文本 `表 {tbl_ch}-{tbl_seq} ...`，勿加粗体星号包裹：`{stripped}`",
                    })
                seq_i = int(tbl_seq)
                expected_tbl_seq = len(seen_table_nums) + 1
                if seq_i != expected_tbl_seq:
                    table_caption_format_errors += 1
                    warnings.append({
                        "file": rel_name,
                        "line": ln,
                        "severity": "warning",
                        "message": f"表编号不连续：期望 `表 {tbl_ch}-{expected_tbl_seq}`，实际为 `{stripped}`",
                    })
                seen_table_nums.append(seq_i)
                continue

            # 4. 扫描套话口号
            for cm in CLICHE_RE.findall(stripped):
                ch_cliches.append(f"L{ln}:{cm}")
                total_cliches += 1

            # 5. 扫描无具体国标或无具体数值支撑的“按规范/达标即可”模糊逃避词（按分句逐一计量，防止长段合并漏计）
            if VAGUE_COPOUT_RE.search(stripped):
                if stripped.startswith("|"):
                    cells = [c.strip() for c in stripped.strip("|").split("|")]
                    bad_cells = [
                        c for c in cells
                        if VAGUE_COPOUT_RE.search(c)
                        and not STANDARD_CODE_RE.search(c)
                        and not re.search(r"(?:≥|≤|±)\s*\d|\d+\s*(?:%|MPa|℃|mm|cm|dB)", c)
                    ]
                    if bad_cells:
                        total_vague_copouts += len(bad_cells)
                        ch_copouts.append({"line": ln, "snippet": " | ".join(bad_cells)[:80]})
                else:
                    for clause in re.split(r"[。；;！!\n]", stripped):
                        hits_in_clause = VAGUE_COPOUT_RE.findall(clause)
                        if not hits_in_clause:
                            continue
                        has_std = bool(STANDARD_CODE_RE.search(clause))
                        has_num = bool(re.search(r"(?:≥|≤|±)\s*\d|\d+\s*(?:%|MPa|℃|mm|cm|dB)", clause))
                        if not (has_std and has_num):
                            total_vague_copouts += len(hits_in_clause)
                            ch_copouts.append({"line": ln, "snippet": clause.strip()[:85]})

            # 普通正文行累积以检测大段散文墙（排除列表项与引用块）
            if stripped.startswith(("- ", "* ", "> ")) or re.match(r"^\d+\.\s+", stripped):
                _flush_paragraph()
                if len(re.sub(r"\s+", "", stripped)) > wall_of_text_limit + 40:
                    total_wall_paragraphs += 1
                    ch_walls.append({"line": ln, "chars": len(stripped), "preview": stripped[:55] + "..."})
            else:
                para_buf.append((ln, stripped))

        _flush_paragraph()
        total_tables += ch_table_count

        for cop in ch_copouts:
            warnings.append({
                "file": rel_name,
                "line": cop["line"],
                "severity": "warning",
                "message": f"[模糊逃避词] 出现无量化指标或无标准编号的空泛表述：`{cop['snippet']}`，请替换为现行规范号+精确数值阈值",
            })

        for wall in ch_walls:
            warnings.append({
                "file": rel_name,
                "line": wall["line"],
                "severity": "warning",
                "message": f"[大段散文墙] 单段连续堆砌 {wall['chars']} 字（`{wall['preview']}`），请拆分为“痛点→定量工艺条目→控制参数表”",
            })

        chapter_metrics.append({
            "file": rel_name,
            "characters": clean_chars,
            "tables": ch_table_count,
            "standards_cited": sorted(ch_standards),
            "standard_count": len(ch_standards),
            "quant_param_hits": ch_quant_count,
            "capacity_calc_hits": ch_capacity_count,
            "project_anchor_hits": len(ch_anchor_hits),
            "cliche_count": len(ch_cliches),
            "cliche_samples": ch_cliches[:6],
            "vague_copout_count": len(ch_copouts),
            "wall_of_text_count": len(ch_walls),
        })

    # 检查 assets/*.png 是否全部在正文中被引用，防止画了图漏插
    unused_assets: list[str] = []
    if workspace_dir is not None and (workspace_dir / "assets").is_dir():
        for png in sorted((workspace_dir / "assets").glob("*.png")):
            if png.name.startswith("_"):
                continue
            rel_png = png.relative_to(workspace_dir).as_posix()
            if rel_png not in referenced_images:
                unused_assets.append(rel_png)
                warnings.append({
                    "file": rel_png,
                    "severity": "warning",
                    "message": f"`assets/` 下的配图未被任何章节草稿引用：{rel_png}",
                })

    # 红线升级判定：模糊逃避词过多、严重散文墙过多、完全无产能验算将直接升级为阻断项
    if total_vague_copouts >= 3:
        blocking_issues.append({
            "file": "<global_compliance>",
            "severity": "error",
            "message": (
                f"[青天合规红线] 全文检出 {total_vague_copouts} 处“按规范要求/温度达标/低于规定值”等模糊糊弄表述（上限允许 2 处），"
                "必须逐处改为具体国标/行标编号与量化控制数值（如 ≥98%、≥160℃、±20mm）！"
            ),
        })

    if total_wall_paragraphs >= 4 or total_severe_walls >= 2:
        blocking_issues.append({
            "file": "<global_readability>",
            "severity": "error",
            "message": (
                f"[青天版面红线] 全文检出 {total_wall_paragraphs} 处超长不分段“散文墙”（其中 >420 字严重散文墙 {total_severe_walls} 处），"
                "呈现明显普通大模型堆字特征！请按各章行号定位拆解为结构化分项条目与技术参数控制表。"
            ),
        })

    if len(draft_files) >= 4 and total_capacity_calcs == 0:
        blocking_issues.append({
            "file": "<global_feasibility>",
            "severity": "error",
            "message": (
                "[青天可落地性红线] 多章节施组全文未检出任何“工程量÷工期→日均施工强度→投入机械台班产能匹配”定量验算！"
                "请至少在主要施工方案或进度计划章节补充核心工序（如土方开挖外运、管道安装、水稳/沥青摊铺）的日均产能与机械配置闭合验算。"
            ),
        })

    # 计算五维确定性雷达分（满分 100 分）
    n_ch = max(1, len(draft_files))
    # 1. 完整性 (30分)
    s_completeness = 30.0
    s_completeness -= min(18.0, len(base_audit["issues"]) * 4.5)
    s_completeness -= min(12.0, heading_chain_breaks * 4.0)
    s_completeness -= min(6.0, len(unused_assets) * 2.0)
    s_completeness = max(0.0, round(s_completeness, 1))

    # 2. 针对性 (25分)
    anchored_chapters = sum(1 for c in chapter_metrics if c["project_anchor_hits"] >= 2)
    anchor_ratio = anchored_chapters / n_ch if project_anchors else 1.0
    s_specificity = 25.0 * (0.55 + 0.45 * min(1.0, anchor_ratio / 0.8))
    s_specificity -= min(10.0, max(0, total_cliches - 2) * 1.2)
    s_specificity = max(0.0, round(min(25.0, s_specificity), 1))

    # 3. 合规性 (20分)
    std_bonus_ratio = min(1.0, len(total_standard_codes) / max(3, n_ch + 2))
    s_compliance = 12.0 + 8.0 * std_bonus_ratio
    s_compliance -= min(14.0, total_vague_copouts * 2.5)
    s_compliance = max(0.0, round(min(20.0, s_compliance), 1))

    # 4. 可落地性 (15分)
    quant_per_ch = total_quant_params / n_ch
    quant_ratio = min(1.0, quant_per_ch / 14.0)
    cap_ratio = min(1.0, total_capacity_calcs / 3.0) if n_ch >= 4 else min(1.0, (total_capacity_calcs + 1) / 2.0)
    s_feasibility = 9.0 * quant_ratio + 6.0 * cap_ratio
    s_feasibility = max(0.0, round(min(15.0, s_feasibility), 1))

    # 5. 规范性 (10分)
    tbl_ratio = min(1.0, total_tables / max(2, int(n_ch * 1.8)))
    s_normativity = 6.0 + 4.0 * tbl_ratio
    s_normativity -= min(7.0, total_wall_paragraphs * 1.2 + total_severe_walls * 1.0)
    s_normativity -= min(4.0, duplicate_fig_captions * 2.0 + table_caption_format_errors * 0.5)
    s_normativity = max(0.0, round(min(10.0, s_normativity), 1))

    radar_score = round(
        s_completeness + s_specificity + s_compliance + s_feasibility + s_normativity,
        1,
    )

    return {
        "passed_radar": len(blocking_issues) == 0 and radar_score >= 85.0,
        "radar_score": radar_score,
        "dimension_scores": {
            "completeness": s_completeness,
            "specificity": s_specificity,
            "compliance": s_compliance,
            "feasibility": s_feasibility,
            "normativity": s_normativity,
        },
        "summary": {
            "chapter_count": len(draft_files),
            "total_characters": base_audit["total_characters"],
            "total_tables": total_tables,
            "unique_standards_count": len(total_standard_codes),
            "unique_standards": sorted(total_standard_codes)[:30],
            "total_quant_params": total_quant_params,
            "total_capacity_calcs": total_capacity_calcs,
            "total_cliches": total_cliches,
            "total_vague_copouts": total_vague_copouts,
            "total_wall_paragraphs": total_wall_paragraphs,
            "duplicate_fig_captions": duplicate_fig_captions,
            "unused_assets": unused_assets,
        },
        "blocking_issues": blocking_issues,
        "warnings": warnings[:50],
        "chapter_metrics": chapter_metrics,
        "drafts_sha256": compute_drafts_sha256(draft_files, workspace_dir=workspace_dir),
    }


def parse_inspector_scorecard(review_md_path: Path) -> dict[str, Any]:
    """
    从青天质检子代理输出的 `03-qingtian-review.md` 中解析并校验结构化五维评分卡 JSON。
    强制由服务端重新求和各分项得分并核查分值上限（30/25/20/15/10），杜绝模型心算误差或越界给分。
    """
    if not review_md_path.is_file():
        raise FileNotFoundError(f"未找到青天评标质检报告文件: {review_md_path.name}，请先派发 inspector 子代理完成评审。")

    content = review_md_path.read_text(encoding="utf-8", errors="replace")
    json_blocks = re.findall(r"```json\s*(\{.*?\})\s*```", content, flags=re.DOTALL)
    if not json_blocks:
        raise ValueError(
            f"`{review_md_path.name}` 中未包含符合规范的 ```json ... ``` 量化评分卡代码块，"
            "请确保青天质检员在报告末尾按规程输出五维 JSON 评分卡。"
        )

    raw_card = json.loads(json_blocks[-1])
    scores = raw_card.get("scores") or {}
    caps = {
        "completeness": 30.0,
        "specificity": 25.0,
        "compliance": 20.0,
        "feasibility": 15.0,
        "normativity": 10.0,
    }
    normalized_scores: dict[str, float] = {}
    for key, max_val in caps.items():
        if key not in scores:
            raise ValueError(f"青天评分卡缺少必选维度字段: `scores.{key}`")
        val = float(scores[key])
        if val < 0.0 or val > max_val:
            raise ValueError(f"维度 `{key}` 得分 {val} 超出合法区间 [0, {max_val}]")
        normalized_scores[key] = round(val, 1)

    recomputed_total = round(sum(normalized_scores.values()), 1)
    blocking_defects = [str(x).strip() for x in (raw_card.get("blocking_defects") or []) if str(x).strip()]
    chapter_rework = list(raw_card.get("chapter_rework") or [])

    return {
        "inspector": str(raw_card.get("inspector") or "hefei-qingtian-inspector"),
        "scores": normalized_scores,
        "ai_total_score": recomputed_total,
        "verdict": str(raw_card.get("verdict") or ("PASS" if recomputed_total >= 90.0 and not blocking_defects else "REWORK")),
        "blocking_defects": blocking_defects,
        "chapter_rework": chapter_rework,
    }


def certify_qingtian_quality(
    workspace_dir: Path,
    draft_files: list[Path],
    review_rel_path: str = "03-qingtian-review.md",
    min_score: float = 90.0,
    blind_mode: bool = False,
) -> dict[str, Any]:
    """
    综合确定性青天雷达与独立 AI 类人评标专家评分卡执行终审裁决：
    - 仅当确定性雷达 `0` 硬伤、AI 质检官 `0` 阻断缺陷、且加权校准综合得分 >= `min_score`（默认 90 分）时，
      签发与当前草稿 SHA-256 强绑定的 `.workspace/qingtian_certificate.json` 准入证；
    - 任一条件不满足时立即清除旧准入证，返回精准返工清单驱动打回重修。
    """
    workspace_dir = workspace_dir.resolve()
    ws_meta = workspace_dir / ".workspace"
    ws_meta.mkdir(parents=True, exist_ok=True)
    cert_path = ws_meta / "qingtian_certificate.json"
    radar_path = ws_meta / "qingtian_radar.json"

    radar = run_qingtian_radar(draft_files, workspace_dir=workspace_dir, blind_mode=blind_mode)
    radar_path.write_text(json.dumps(radar, ensure_ascii=False, indent=2), encoding="utf-8")

    review_path = (workspace_dir / review_rel_path).resolve()
    try:
        ai_card = parse_inspector_scorecard(review_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        if cert_path.exists():
            cert_path.unlink()
        return {
            "passed": False,
            "min_score": min_score,
            "error": str(exc),
            "radar_score": radar["radar_score"],
            "radar_blocking_issues": radar["blocking_issues"],
        }

    # 防虚高融合裁决：若底层确定性雷达分偏低，将自动拉低综合得分，防止模型当“老好人”放水
    ai_score = ai_card["ai_total_score"]
    radar_score = radar["radar_score"]
    calibrated_score = round(min(ai_score, ai_score * 0.65 + radar_score * 0.35), 1)

    passed = (
        len(radar["blocking_issues"]) == 0
        and len(ai_card["blocking_defects"]) == 0
        and calibrated_score >= min_score
    )

    payload = {
        "passed": passed,
        "min_score": min_score,
        "final_score": calibrated_score,
        "ai_score": ai_score,
        "radar_score": radar_score,
        "ai_dimension_scores": ai_card["scores"],
        "radar_dimension_scores": radar["dimension_scores"],
        "radar_blocking_issues": radar["blocking_issues"],
        "ai_blocking_defects": ai_card["blocking_defects"],
        "chapter_rework": ai_card["chapter_rework"],
        "radar_warnings": radar["warnings"][:25],
        "drafts_sha256": radar["drafts_sha256"],
        "review_file": review_rel_path,
        "blind_mode": blind_mode,
        "certified_at": int(time.time()),
    }

    if passed:
        cert_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    elif cert_path.exists():
        cert_path.unlink()

    return payload


def verify_qingtian_release_gate(
    workspace_dir: Path,
    draft_files: list[Path],
    min_score: float = 90.0,
    blind_mode: bool = False,
) -> dict[str, Any]:
    """
    在 `compile_word.py` 编译导出原生 `.docx` 前执行的硬性准入锁死校验：
    1. 核验 `.workspace/qingtian_certificate.json` 是否存在且 `passed == True`、`final_score >= min_score`；
    2. 实时重算当前 `drafts/*.md` 的 SHA-256，若质检后草稿被修改过哪怕 1 个字符，立即拒绝编译；
    3. 实时复跑一次确定性青天雷达，确保不存在任何硬红线漏网。
    """
    workspace_dir = workspace_dir.resolve()
    cert_path = workspace_dir / ".workspace" / "qingtian_certificate.json"
    if not cert_path.is_file():
        return {
            "passed": False,
            "reason": (
                "【青天质检门禁拦截】未检测到高标准质检准入证 (`.workspace/qingtian_certificate.json`)！"
                "正式交付前必须先运行 `qingtian_inspect.py --mode radar`、派发 `inspector` 青天评标质检员出具 `03-qingtian-review.md`、"
                f"并通过 `qingtian_inspect.py --mode certify` 达到 ≥{min_score} 分且零红线后，方可编译 Word。"
            ),
        }

    try:
        cert = json.loads(cert_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"passed": False, "reason": f"准入证文件损坏无法读取: {exc}"}

    if not cert.get("passed") or float(cert.get("final_score") or 0.0) < min_score:
        return {
            "passed": False,
            "reason": f"【青天质检门禁拦截】当前得分 {cert.get('final_score')} 未达准入标准 {min_score} 分或存在未消除红线，禁止导出 Word。",
        }

    current_sha = compute_drafts_sha256(draft_files, workspace_dir=workspace_dir)
    if cert.get("drafts_sha256") != current_sha:
        return {
            "passed": False,
            "reason": (
                "【青天质检门禁拦截】检测到章节草稿在质检签发后发生了内容变更（SHA-256 摘要不一致）！"
                "修改草稿后必须重新执行青天质检复审与 `--mode certify` 签发最新准入证，严禁改后跳检直接编译。"
            ),
        }

    live_radar = run_qingtian_radar(draft_files, workspace_dir=workspace_dir, blind_mode=blind_mode)
    if live_radar["blocking_issues"]:
        return {
            "passed": False,
            "reason": f"【青天质检门禁拦截】实时复检发现 {len(live_radar['blocking_issues'])} 项硬伤未消除",
            "blocking_issues": live_radar["blocking_issues"],
        }

    return {
        "passed": True,
        "final_score": cert["final_score"],
        "ai_score": cert.get("ai_score"),
        "radar_score": cert.get("radar_score"),
        "drafts_sha256": current_sha,
    }
