"""确定性专业工程/商务图表绘制脚本：基于 Pillow 原生绘制高分辨率施工进度横道图 (Gantt)、组织管理架构图 (OrgTree) 与工艺流程图 (Flow)。"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]

BOLD_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    *FONT_CANDIDATES,
]


def _load_font(size: int, bold: bool = False):
    """加载系统原生清晰中文字体，避免任何中文方块乱码。"""
    for fp in (BOLD_FONT_CANDIDATES if bold else FONT_CANDIDATES):
        if Path(fp).is_file():
            try:
                return ImageFont.truetype(fp, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _palette(monochrome: bool) -> dict[str, str]:
    """根据明标彩色主题或暗标纯黑白主题返回合规配色表。"""
    if monochrome:
        return {
            "bg": "#FFFFFF",
            "fg": "#000000",
            "muted": "#000000",
            "border": "#000000",
            "grid": "#888888",
            "header_bg": "#FFFFFF",
            "stripe": "#FFFFFF",
            "bar_normal": "#FFFFFF",
            "bar_critical": "#000000",
            "card_bg": "#FFFFFF",
            "badge_bg": "#FFFFFF",
        }
    return {
        "bg": "#FFFFFF",
        "fg": "#0F172A",
        "muted": "#475569",
        "border": "#334155",
        "grid": "#CBD5E1",
        "header_bg": "#EFF6FF",
        "stripe": "#F8FAFC",
        "bar_normal": "#2563EB",
        "bar_critical": "#DC2626",
        "card_bg": "#F8FAFC",
        "badge_bg": "#FEF3C7",
    }


def _draw_centered_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font, fill: str) -> None:
    """在矩形框内水平垂直居中绘制单行或多行文本。"""
    x0, y0, x1, y1 = box
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=4, align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = x0 + max(4, (x1 - x0 - tw) // 2)
    ty = y0 + max(2, (y1 - y0 - th) // 2)
    draw.multiline_text((tx, ty), text, font=font, fill=fill, spacing=4, align="center")


def render_gantt_chart(spec: dict[str, Any], out_path: Path, monochrome: bool = False) -> dict[str, Any]:
    """
    绘制高分辨率施工总进度计划横道图（Gantt Chart）。
    输入 spec 字段：`title`、`total_days`、`tasks: [{name, start, end, critical}]`。
    """
    pal = _palette(monochrome)
    title = str(spec.get("title") or "项目施工总进度计划横道图")
    tasks = list(spec.get("tasks") or [])
    if not tasks:
        raise ValueError("甘特图 `tasks` 列表不能为空")

    total_days = max(
        int(spec.get("total_days") or 0),
        max(int(t.get("end") or 1) for t in tasks),
        10,
    )

    width = 2200
    row_h = 64
    top_h = 150
    bottom_h = 80
    height = top_h + len(tasks) * row_h + bottom_h

    img = Image.new("RGB", (width, height), pal["bg"])
    draw = ImageDraw.Draw(img)

    f_title = _load_font(34, bold=True)
    f_hdr = _load_font(22, bold=True)
    f_cell = _load_font(20, bold=False)
    f_small = _load_font(18, bold=False)

    _draw_centered_text(draw, (0, 18, width, 74), title, f_title, pal["fg"])

    col_idx_x = 40
    col_name_x = 120
    col_days_x = 540
    chart_x0 = 670
    chart_x1 = width - 50
    chart_w = chart_x1 - chart_x0
    table_top = 86
    table_bottom = top_h + len(tasks) * row_h

    # 表头背景与主外框
    draw.rectangle((col_idx_x, table_top, chart_x1, top_h), fill=pal["header_bg"], outline=pal["border"], width=2)
    draw.rectangle((col_idx_x, table_top, chart_x1, table_bottom), outline=pal["border"], width=2)

    _draw_centered_text(draw, (col_idx_x, table_top, col_name_x, top_h), "序号", f_hdr, pal["fg"])
    _draw_centered_text(draw, (col_name_x, table_top, col_days_x, top_h), "分部分项工序名称", f_hdr, pal["fg"])
    _draw_centered_text(draw, (col_days_x, table_top, chart_x0, top_h), "持续天", f_hdr, pal["fg"])

    # 绘制时间刻度网格（自动分 10~12 个刻度区间）
    step_days = max(5, math.ceil(total_days / 10 / 5) * 5)
    ticks = list(range(0, total_days + 1, step_days))
    if ticks[-1] != total_days:
        ticks.append(total_days)

    for day in ticks:
        gx = chart_x0 + round((day / total_days) * chart_w)
        draw.line((gx, table_top, gx, table_bottom), fill=pal["grid"], width=1)
        label = f"{day}d" if day > 0 else "0"
        draw.text((gx - 12, table_top + 18), label, font=f_small, fill=pal["muted"])

    # 逐行绘制工序信息与进度横道条
    for i, task in enumerate(tasks):
        ry0 = top_h + i * row_h
        ry1 = ry0 + row_h
        if not monochrome and i % 2 == 1:
            draw.rectangle((col_idx_x + 2, ry0, chart_x1 - 2, ry1), fill=pal["stripe"])
        draw.line((col_idx_x, ry0, chart_x1, ry0), fill=pal["border"], width=1)

        s_day = max(1, int(task.get("start") or 1))
        e_day = max(s_day, min(total_days, int(task.get("end") or s_day)))
        dur = e_day - s_day + 1
        is_crit = bool(task.get("critical", False))

        _draw_centered_text(draw, (col_idx_x, ry0, col_name_x, ry1), str(i + 1), f_cell, pal["fg"])
        name_txt = str(task.get("name") or f"工序 {i + 1}")[:22]
        draw.text((col_name_x + 14, ry0 + 18), name_txt, font=f_cell, fill=pal["fg"])
        _draw_centered_text(draw, (col_days_x, ry0, chart_x0, ry1), str(dur), f_cell, pal["fg"])

        bx0 = chart_x0 + round(((s_day - 1) / total_days) * chart_w)
        bx1 = max(bx0 + 14, chart_x0 + round((e_day / total_days) * chart_w))
        by0, by1 = ry0 + 18, ry1 - 18

        if monochrome:
            # 暗标纯黑白模式：关键线路用纯黑实心条，普通工序用白底黑框斜线阴影条，清晰区分且零彩色违规
            fill_c = "#000000" if is_crit else "#FFFFFF"
            draw.rectangle((bx0, by0, bx1, by1), fill=fill_c, outline="#000000", width=2)
            if not is_crit:
                for hx in range(bx0 + 8, bx1, 12):
                    draw.line((hx, by1, min(bx1, hx + 10), by0), fill="#000000", width=1)
        else:
            fill_c = pal["bar_critical"] if is_crit else pal["bar_normal"]
            draw.rounded_rectangle((bx0, by0, bx1, by1), radius=5, fill=fill_c)

        span_lbl = f"D{s_day}-D{e_day}"
        lbl_x = bx1 + 8 if bx1 + 110 < chart_x1 else max(chart_x0 + 4, bx0 - 105)
        draw.text((lbl_x, ry0 + 20), span_lbl, font=f_small, fill=pal["fg"])

    for vx in (col_name_x, col_days_x, chart_x0):
        draw.line((vx, table_top, vx, table_bottom), fill=pal["border"], width=2)

    # 底部图例说明
    leg_y = table_bottom + 24
    if monochrome:
        draw.rectangle((col_idx_x, leg_y, col_idx_x + 36, leg_y + 20), fill="#000000", outline="#000000", width=2)
        draw.text((col_idx_x + 46, leg_y - 2), "关键线路工序", font=f_small, fill="#000000")
        draw.rectangle((col_idx_x + 220, leg_y, col_idx_x + 256, leg_y + 20), fill="#FFFFFF", outline="#000000", width=2)
        draw.text((col_idx_x + 266, leg_y - 2), f"非关键工序（计划总工期：{total_days} 日历天）", font=f_small, fill="#000000")
    else:
        draw.rectangle((col_idx_x, leg_y, col_idx_x + 36, leg_y + 20), fill=pal["bar_critical"])
        draw.text((col_idx_x + 46, leg_y - 2), "关键线路工序", font=f_small, fill=pal["fg"])
        draw.rectangle((col_idx_x + 220, leg_y, col_idx_x + 256, leg_y + 20), fill=pal["bar_normal"])
        draw.text((col_idx_x + 266, leg_y - 2), f"常规工序（计划总工期：{total_days} 日历天）", font=f_small, fill=pal["fg"])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), format="PNG")
    return {"type": "gantt", "output": str(out_path), "width": width, "height": height, "task_count": len(tasks)}


def render_org_tree(spec: dict[str, Any], out_path: Path, monochrome: bool = False) -> dict[str, Any]:
    """
    绘制项目组织管理架构树图（OrgTree）。
    输入 spec 字段：`title`、`root` (字符串或 `{name, duty}`)、`deputies: [...]`、`departments: [{name, items: [...]}]`。
    """
    pal = _palette(monochrome)
    title = str(spec.get("title") or "项目管理组织机构体系图")
    root_label = spec.get("root", "项目经理")
    if isinstance(root_label, dict):
        root_label = f"{root_label.get('name', '项目经理')}\n({root_label.get('duty', '全面负责')})"

    deputies = [str(d) for d in (spec.get("deputies") or ["项目技术负责人", "项目生产副经理", "专职安全总监"])]
    depts = list(spec.get("departments") or [])
    if not depts:
        depts = [
            {"name": "工程技术部", "items": ["测量放线组", "道路排水施工队"]},
            {"name": "质量安全部", "items": ["现场质检组", "文明施工督导组"]},
            {"name": "物资合约部", "items": ["材料封样组", "成本核算组"]},
        ]

    col_count = max(2, len(depts))
    width = max(1600, col_count * 340 + 120)
    max_sub_items = max((len(d.get("items") or []) for d in depts), default=2)
    height = 540 + max_sub_items * 74

    img = Image.new("RGB", (width, height), pal["bg"])
    draw = ImageDraw.Draw(img)
    f_title = _load_font(32, bold=True)
    f_box_b = _load_font(22, bold=True)
    f_box = _load_font(19, bold=False)

    _draw_centered_text(draw, (0, 20, width, 74), title, f_title, pal["fg"])

    cx = width // 2
    # 第一层：决策核心（项目经理）
    r_box = (cx - 170, 96, cx + 170, 172)
    draw.rounded_rectangle(r_box, radius=8, fill=pal["header_bg"], outline=pal["border"], width=3)
    _draw_centered_text(draw, r_box, str(root_label), f_box_b, pal["fg"])

    # 第二层：副职管理层
    dep_y0, dep_y1 = 224, 292
    draw.line((cx, 172, cx, 198), fill=pal["border"], width=2)
    if deputies:
        span_w = min(width - 200, len(deputies) * 320)
        start_x = cx - span_w // 2
        step_x = span_w // len(deputies)
        centers_2 = [start_x + step_x * i + step_x // 2 for i in range(len(deputies))]
        if len(centers_2) > 1:
            draw.line((centers_2[0], 198, centers_2[-1], 198), fill=pal["border"], width=2)
        for d_cx, d_txt in zip(centers_2, deputies):
            draw.line((d_cx, 198, d_cx, dep_y0), fill=pal["border"], width=2)
            b = (d_cx - 135, dep_y0, d_cx + 135, dep_y1)
            draw.rounded_rectangle(b, radius=6, fill=pal["card_bg"], outline=pal["border"], width=2)
            _draw_centered_text(draw, b, d_txt, f_box_b, pal["fg"])
            draw.line((d_cx, dep_y1, d_cx, 326), fill=pal["border"], width=2)
        if len(centers_2) > 1:
            draw.line((centers_2[0], 326, centers_2[-1], 326), fill=pal["border"], width=2)
        draw.line((cx, 326, cx, 356), fill=pal["border"], width=2)
    else:
        draw.line((cx, 198, cx, 356), fill=pal["border"], width=2)

    # 第三层：各专业职能部室与下辖作业班组
    dept_y0, dept_y1 = 384, 452
    margin_x = 70
    usable_w = width - 2 * margin_x
    col_w = usable_w // len(depts)
    dept_centers = [margin_x + col_w * i + col_w // 2 for i in range(len(depts))]
    if len(dept_centers) > 1:
        draw.line((dept_centers[0], 356, dept_centers[-1], 356), fill=pal["border"], width=2)

    for d_cx, dept in zip(dept_centers, depts):
        draw.line((d_cx, 356, d_cx, dept_y0), fill=pal["border"], width=2)
        db = (d_cx - 135, dept_y0, d_cx + 135, dept_y1)
        draw.rounded_rectangle(db, radius=6, fill=pal["header_bg"], outline=pal["border"], width=2)
        _draw_centered_text(draw, db, str(dept.get("name") or "职能部门"), f_box_b, pal["fg"])

        sub_items = [str(x) for x in (dept.get("items") or [])]
        prev_bottom = dept_y1
        for s_idx, s_txt in enumerate(sub_items):
            sy0 = 478 + s_idx * 70
            sy1 = sy0 + 52
            draw.line((d_cx, prev_bottom, d_cx, sy0), fill=pal["border"], width=2)
            sb = (d_cx - 125, sy0, d_cx + 125, sy1)
            draw.rectangle(sb, fill=pal["bg"], outline=pal["border"], width=2)
            _draw_centered_text(draw, sb, s_txt[:16], f_box, pal["fg"])
            prev_bottom = sy1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), format="PNG")
    return {"type": "org_tree", "output": str(out_path), "width": width, "height": height, "departments": len(depts)}


def render_process_flow(spec: dict[str, Any], out_path: Path, monochrome: bool = False) -> dict[str, Any]:
    """
    绘制多步施工工艺流程 / 业务控制蛇形框图（Process Flow）。
    输入 spec 字段：`title`、`steps: [{title, detail, checkpoint}]`（每行排布 4 步并带转向箭头）。
    """
    pal = _palette(monochrome)
    title = str(spec.get("title") or "关键施工工艺与质量控制流程图")
    steps = list(spec.get("steps") or [])
    if not steps:
        raise ValueError("流程图 `steps` 不能为空")

    cols = min(4, len(steps))
    rows = math.ceil(len(steps) / cols)
    width = 1840
    height = 140 + rows * 210

    img = Image.new("RGB", (width, height), pal["bg"])
    draw = ImageDraw.Draw(img)
    f_title = _load_font(32, bold=True)
    f_step = _load_font(21, bold=True)
    f_desc = _load_font(17, bold=False)

    _draw_centered_text(draw, (0, 20, width, 78), title, f_title, pal["fg"])

    margin_x = 70
    card_w = 350
    card_h = 128
    gap_x = (width - 2 * margin_x - cols * card_w) // max(1, cols - 1) if cols > 1 else 0

    centers: list[tuple[int, int, int, int]] = []
    for idx, st in enumerate(steps):
        r = idx // cols
        c_in_row = idx % cols
        # 奇数行反向排列，形成顺畅的 S 型蛇形工艺流向
        c = (cols - 1 - c_in_row) if (r % 2 == 1) else c_in_row
        x0 = margin_x + c * (card_w + gap_x)
        y0 = 110 + r * 200
        x1, y1 = x0 + card_w, y0 + card_h
        centers.append((x0, y0, x1, y1))

        draw.rounded_rectangle((x0, y0, x1, y1), radius=8, fill=pal["card_bg"], outline=pal["border"], width=2)
        st_title = f"{idx + 1}. {st.get('title', '工序步骤')}"
        draw.text((x0 + 16, y0 + 14), st_title[:18], font=f_step, fill=pal["fg"])

        detail = str(st.get("detail") or "").strip()
        if detail:
            wrapped = "\n".join(detail[i : i + 18] for i in range(0, min(len(detail), 36), 18))
            draw.multiline_text((x0 + 16, y0 + 50), wrapped, font=f_desc, fill=pal["muted"], spacing=3)

        chk = str(st.get("checkpoint") or "").strip()
        if chk:
            badge_box = (x0 + 16, y1 - 30, x1 - 16, y1 - 6)
            draw.rectangle(badge_box, fill=pal["badge_bg"], outline=pal["border"], width=1)
            draw.text((x0 + 22, y1 - 29), f"★ 验收点：{chk[:13]}", font=f_desc, fill=pal["fg"])

    # 绘制相邻步骤之间的方向箭头
    for i in range(len(centers) - 1):
        ax0, ay0, ax1, ay1 = centers[i]
        bx0, by0, bx1, by1 = centers[i + 1]
        if ay0 == by0:
            if bx0 > ax1:
                sx, sy, ex, ey = ax1, (ay0 + ay1) // 2, bx0, (by0 + by1) // 2
                draw.line((sx, sy, ex, ey), fill=pal["border"], width=3)
                draw.polygon([(ex, ey), (ex - 12, ey - 7), (ex - 12, ey + 7)], fill=pal["border"])
            else:
                sx, sy, ex, ey = ax0, (ay0 + ay1) // 2, bx1, (by0 + by1) // 2
                draw.line((sx, sy, ex, ey), fill=pal["border"], width=3)
                draw.polygon([(ex, ey), (ex + 12, ey - 7), (ex + 12, ey + 7)], fill=pal["border"])
        else:
            sx, sy = (ax0 + ax1) // 2, ay1
            ex, ey = (bx0 + bx1) // 2, by0
            draw.line((sx, sy, ex, ey), fill=pal["border"], width=3)
            draw.polygon([(ex, ey), (ex - 7, ey - 12), (ex + 7, ey - 12)], fill=pal["border"])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), format="PNG")
    return {"type": "flow", "output": str(out_path), "width": width, "height": height, "step_count": len(steps)}


def main() -> int:
    """解析 JSON 图表规格文件并在工作区生成高分辨率 PNG 配图（支持批量一次生成多张图）。"""
    ap = argparse.ArgumentParser(description="Render high-DPI engineering/business charts to PNG")
    ap.add_argument("--spec-file", help="图表规格 JSON 文件路径（支持单个图表对象或 `charts: [...]` 数组）")
    ap.add_argument("--spec-json", help="直接传入内联 JSON 字符串")
    ap.add_argument("--blind", action="store_true", help="启用技术暗标纯黑白线条模式")
    args = ap.parse_args()

    workspace_dir = Path(os.environ.get("MAYI_WORKSPACE_DIR") or Path.cwd()).resolve()
    if args.spec_file:
        sp = Path(args.spec_file)
        sp = sp.resolve() if sp.is_absolute() else (workspace_dir / sp).resolve()
        payload = json.loads(sp.read_text(encoding="utf-8"))
    elif args.spec_json:
        payload = json.loads(args.spec_json)
    else:
        raise ValueError("必须传入 `--spec-file` 或 `--spec-json`")

    chart_list = payload.get("charts") if isinstance(payload, dict) and "charts" in payload else [payload]
    generated = []

    for item in chart_list:
        kind = str(item.get("type") or "flow").lower()
        rel_out = str(item.get("output") or f"assets/{kind}.png").replace("\\", "/").lstrip("/")
        out_path = (workspace_dir / rel_out).resolve()
        if not out_path.is_relative_to(workspace_dir):
            raise PermissionError(f"图表输出路径必须位于工作区内: {rel_out}")

        mono = bool(args.blind or item.get("monochrome", False))
        if kind == "gantt":
            info = render_gantt_chart(item, out_path, monochrome=mono)
        elif kind == "org_tree":
            info = render_org_tree(item, out_path, monochrome=mono)
        elif kind == "flow":
            info = render_process_flow(item, out_path, monochrome=mono)
        else:
            raise ValueError(f"不支持的图表类型: {kind}（可选: gantt | org_tree | flow）")
        generated.append({**info, "relative_path": rel_out})

    print(json.dumps({"status": "succeeded", "generated_count": len(generated), "charts": generated}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
