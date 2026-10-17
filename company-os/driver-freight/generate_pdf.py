#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""車主運費明細 PDF 產生器（company-os / driver-freight 第一塊）

目標：給年長車主看的「一張就看得懂」月結運費明細。

輸入：一份「寬表」CSV（欄位 日期/車主/司機/車號/路線/運費/備註1~7）。
輸出：單一車主、單月的 A4 直式、單頁 PDF。

版面原則（對象為年長者）：
  - 大標題、關鍵數字特大。
  - 字級大、行距寬鬆、黑白為主、僅用少量灰階分隔線。
  - 「有值才印」：單一車主報表中「車主」整欄固定 → 不重複印在每列，改放標題；
    每列的備註只印有填的，空的不佔空間；整月全空的備註欄自動省略。
  - 一張到底、不分頁：筆數多時自動縮字級/縮行高壓進一頁，但維持可讀下限。

繁體中文：使用 reportlab 內建 CJK 字型 STSong-Light（UnicodeCIDFont），
免安裝外部字型即可正確顯示繁中。
"""

import csv
import os
import re
import sys
from collections import OrderedDict
from datetime import date
from decimal import Decimal, InvalidOperation

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

# 繁體中文 CJK 字型：reportlab 內建，無需外部字型檔
FONT_NAME = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT_NAME))

REMARK_COLS = [f"備註{i}" for i in range(1, 8)]
REQUIRED_COLS = ["日期", "車主", "司機", "車號", "路線", "運費"]


# ---------------------------------------------------------------------------
# 資料讀取
# ---------------------------------------------------------------------------
def load_rows(csv_path):
    """讀寬表，回傳 list[dict]。"""
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, strict=True)
        if not reader.fieldnames:
            raise ValueError("CSV 缺少欄位名稱")
        fields = [name.strip() for name in reader.fieldnames]
        if "" in fields or len(fields) != len(set(fields)):
            raise ValueError("CSV 欄位名稱不可空白或重複")
        missing = set(REQUIRED_COLS) - set(fields)
        if missing:
            raise ValueError("CSV 缺少必要欄位：" + "、".join(sorted(missing)))
        reader.fieldnames = fields
        rows = []
        for r in reader:
            if None in r or any(v is None for v in r.values()):
                raise ValueError(f"CSV 第 {reader.line_num} 行欄位數量不符")
            rows.append({k: v.strip() for k, v in r.items()})
    validate_rows(rows)
    return rows


def merged_remark(row):
    """把一列中有值的備註合併成一行字串；全空回傳空字串（不佔版面）。"""
    parts = [row.get(c, "") for c in REMARK_COLS]
    parts = [p for p in parts if p]
    return "　".join(parts)


def parse_amount(s):
    """解析十進位金額；不可把錯誤資料當成零元。"""
    text = str(s).strip()
    if not re.fullmatch(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", text):
        raise ValueError(f"無效運費金額：{s!r}")
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"無效運費金額：{s!r}") from exc


def fmt_money(v):
    """整數不帶小數、有小數才顯示小數，並加千分位。"""
    text = f"{v:,f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def validate_rows(rows):
    """一張明細只能包含單一車主、單月的有效資料。"""
    if not rows:
        raise ValueError("沒有資料")
    owners = set()
    months = set()
    for number, row in enumerate(rows, 1):
        for field in REQUIRED_COLS:
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"第 {number} 筆缺少必要欄位：{field}")
        try:
            day = date.fromisoformat(row["日期"])
            if day.isoformat() != row["日期"]:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"第 {number} 筆日期須為有效的 YYYY-MM-DD") from exc
        try:
            parse_amount(row["運費"])
        except ValueError as exc:
            raise ValueError(f"第 {number} 筆：{exc}") from exc
        owners.add(row["車主"])
        months.add((day.year, day.month))
    if len(owners) != 1 or len(months) != 1:
        raise ValueError("每份明細只能包含單一車主、單一月份")


def group_by_date(rows):
    """依日期把列分組（保留出現順序）。回傳 OrderedDict[date] -> list[row]。"""
    groups = OrderedDict()
    for r in rows:
        groups.setdefault(r["日期"], []).append(r)
    return groups


def fmt_date_cn(date_str):
    """2025-10-01 → 10月1日。"""
    try:
        _, m, d = date_str.split("-")
        return f"{int(m)}月{int(d)}日"
    except ValueError:
        return date_str


# ---------------------------------------------------------------------------
# 文字換行（依像素寬度斷，支援中英混排）
# ---------------------------------------------------------------------------
def wrap_text(text, font, size, max_width):
    """把 text 依 max_width 斷成多行（逐字量測，適合 CJK）。"""
    if not text:
        return [""]
    lines = []
    cur = ""
    for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        trial = cur + ch
        if pdfmetrics.stringWidth(trial, font, size) <= max_width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = ch
    lines.append(cur)
    return lines or [""]


# ---------------------------------------------------------------------------
# PDF 繪製
# ---------------------------------------------------------------------------
def build_pdf(rows, out_path):
    validate_rows(rows)
    owner = rows[0]["車主"]
    first_date = rows[0]["日期"]
    year, month = first_date.split("-")[0], first_date.split("-")[1]

    trip_count = len(rows)
    total = sum(parse_amount(r["運費"]) for r in rows)
    groups = group_by_date(rows)

    page_w, page_h = A4
    margin_x = 16 * mm
    margin_top = 14 * mm
    margin_bottom = 13 * mm
    content_w = page_w - 2 * margin_x

    # --- 固定區塊高度（與實際繪製的間距一致）---
    title_size = 23
    kpi_label_size = 13
    kpi_num_size = 28
    header_size = 13

    title = f"【{owner}】 {year} 年 {int(month)} 月 運費明細"
    title_lines = wrap_text(title, FONT_NAME, title_size, content_w)
    title_h = len(title_lines) * (title_size + 4)
    top_block_h = title_h + 10 + kpi_label_size + kpi_num_size + 18 + 10
    header_h = header_size + 12   # 表頭 + 其下分隔線空間
    total_row_h = 61              # 本月合計區塊（雙線 + 大字）
    footer_h = 18                 # 頁尾說明

    avail_rows_h = (
        page_h - margin_top - margin_bottom
        - top_block_h - header_h - total_row_h - footer_h
    )

    # 欄寬配置（車主不列入表格 → 放標題；滿足「整欄全同可省略」）
    col = OrderedDict()
    col["日期"] = 62
    col["司機"] = 58
    col["車號"] = 72
    # 路線為彈性欄
    col["運費"] = 78
    fixed = sum(col.values())
    route_w = content_w - fixed
    # 重新排序：日期 司機 車號 路線 運費
    order = ["日期", "司機", "車號", "路線", "運費"]
    for text, size, width in (
        (f"{trip_count} 趟", kpi_num_size, content_w * 0.5 - 12),
        (f"$ {fmt_money(total)}", kpi_num_size, content_w * 0.5 - 12),
        (f"本月合計　共 {trip_count} 趟", 17, content_w * 0.5 - 12),
        (f"$ {fmt_money(total)}", 17, content_w * 0.5 - 12),
    ):
        if pdfmetrics.stringWidth(text, FONT_NAME, size) > width:
            raise ValueError("趟次或合計金額過長，無法完整顯示於單頁明細")

    # --- 自動字級：由大到小找能一頁塞下的最大字級（含逐日小計列）---
    def layout_metrics(body_size):
        row_gap = body_size * 0.40          # 行距寬鬆但不浪費
        remark_size = max(body_size - 2, 9)
        subtotal_size = body_size + 1.5     # 小計略大
        line_h = body_size + row_gap
        remark_line_h = remark_size + row_gap * 0.6
        subtotal_h = subtotal_size + 6      # 含上下留白
        total_h = 0
        per_group = []
        for date, grp in groups.items():
            per_row = []
            for r in grp:
                cell_lines = {
                    name: wrap_text(r[name][5:] if name == "日期" else r[name],
                                    FONT_NAME, body_size,
                                    (route_w if name == "路線" else col[name]) - 6)
                    for name in order if name != "運費"
                }
                amount_width = pdfmetrics.stringWidth(
                    f"$ {fmt_money(parse_amount(r['運費']))}", FONT_NAME, body_size)
                if amount_width > col["運費"] - 6:
                    total_h = float("inf")
                rk = merged_remark(r)
                remark_lines = (
                    wrap_text("備註：" + rk, FONT_NAME, remark_size,
                              content_w - col["日期"] - 12)
                    if rk else []
                )
                n_main = max(map(len, cell_lines.values()))
                h = n_main * line_h + len(remark_lines) * remark_line_h + 2
                per_row.append((r, cell_lines, remark_lines, h))
                total_h += h
            subtotal_text = (
                f"{fmt_date_cn(date)}　共 {len(grp)} 趟"
                f"　小計  $ {fmt_money(sum(parse_amount(r['運費']) for r in grp))}"
            )
            if pdfmetrics.stringWidth(subtotal_text, FONT_NAME, subtotal_size) > content_w - 4:
                total_h = float("inf")
            total_h += subtotal_h
            per_group.append((date, grp, per_row))
        meta = dict(row_gap=row_gap, remark_size=remark_size,
                    subtotal_size=subtotal_size, line_h=line_h,
                    remark_line_h=remark_line_h, subtotal_h=subtotal_h)
        return total_h, per_group, meta

    for step in range(9):
        body_size = 13 - step * 0.5
        total_h, per_group, meta = layout_metrics(body_size)
        if total_h <= avail_rows_h:
            break
    else:
        raise ValueError("資料過多或文字過長，無法在 9pt 下完整放入單頁；請減少資料或縮短文字")

    # === 開始繪製 ===
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    c = canvas.Canvas(os.fspath(out_path), pagesize=A4)
    y = page_h - margin_top

    # 標題
    c.setFillGray(0)
    c.setFont(FONT_NAME, title_size)
    for title_line in title_lines:
        c.drawString(margin_x, y - title_size, title_line)
        y -= title_size + 4
    y -= 10

    # KPI 大數字區
    c.setFont(FONT_NAME, kpi_label_size)
    c.setFillGray(0.35)
    c.drawString(margin_x, y - kpi_label_size, "本月趟次")
    kpi2_x = margin_x + content_w * 0.5
    c.drawString(kpi2_x, y - kpi_label_size, "本月運費")
    c.setFillGray(0)
    c.setFont(FONT_NAME, kpi_num_size)
    c.drawString(margin_x, y - kpi_label_size - kpi_num_size - 2,
                 f"{trip_count} 趟")
    c.drawString(kpi2_x, y - kpi_label_size - kpi_num_size - 2,
                 f"$ {fmt_money(total)}")
    y -= kpi_label_size + kpi_num_size + 18

    # 粗分隔線
    c.setLineWidth(1.2)
    c.setStrokeGray(0.25)
    c.line(margin_x, y, margin_x + content_w, y)
    y -= 10

    # 表頭
    def col_x(name):
        x = margin_x
        for k in order:
            if k == name:
                return x
            x += col.get(k, route_w) if k != "路線" else route_w
        return x

    c.setFont(FONT_NAME, header_size)
    c.setFillGray(0)
    hy = y - header_size
    c.drawString(col_x("日期"), hy, "日期")
    c.drawString(col_x("司機"), hy, "司機")
    c.drawString(col_x("車號"), hy, "車號")
    c.drawString(col_x("路線"), hy, "路線")
    # 運費右對齊
    c.drawRightString(margin_x + content_w, hy, "運費")
    y = hy - 6
    c.setLineWidth(0.6)
    c.setStrokeGray(0.55)
    c.line(margin_x, y, margin_x + content_w, y)
    y -= 6

    line_h = meta["line_h"]
    remark_size = meta["remark_size"]
    remark_line_h = meta["remark_line_h"]
    subtotal_size = meta["subtotal_size"]
    subtotal_h = meta["subtotal_h"]

    # 明細列（依日期分組，每組後接「逐日小計」）
    for date, grp, per_row in per_group:
        for r, cell_lines, remark_lines, h in per_row:
            row_top = y
            c.setFont(FONT_NAME, body_size)
            c.setFillGray(0)
            base = y - body_size
            # 各欄分別換行，避免長姓名、車號或路線覆蓋相鄰欄位
            for name, lines in cell_lines.items():
                for index, text in enumerate(lines):
                    c.drawString(col_x(name), base - index * line_h, text)
            c.drawRightString(margin_x + content_w, base,
                              f"$ {fmt_money(parse_amount(r['運費']))}")
            yy = base - max(map(len, cell_lines.values())) * line_h
            # 備註（小字、灰）
            if remark_lines:
                c.setFont(FONT_NAME, remark_size)
                c.setFillGray(0.4)
                for rl in remark_lines:
                    c.drawString(col_x("司機"), yy, rl)
                    yy -= remark_line_h
            # 列間細灰線
            y = row_top - h
            c.setLineWidth(0.3)
            c.setStrokeGray(0.85)
            c.line(margin_x, y + 2, margin_x + content_w, y + 2)

        # 逐日小計（灰底帶狀、略大字，方便逐日對帳）
        day_total = sum(parse_amount(r["運費"]) for r in grp)
        band_top = y
        band_h = subtotal_h - 2
        c.setFillGray(0.92)
        c.rect(margin_x, band_top - band_h, content_w, band_h,
               stroke=0, fill=1)
        c.setFillGray(0.15)
        c.setFont(FONT_NAME, subtotal_size)
        sub_base = band_top - band_h + (band_h - subtotal_size) / 2 + 1
        c.drawString(col_x("日期") + 2, sub_base,
                     f"{fmt_date_cn(date)}　共 {len(grp)} 趟")
        c.drawRightString(margin_x + content_w - 2, sub_base,
                          f"小計  $ {fmt_money(day_total)}")
        y = band_top - subtotal_h

    # 本月合計（雙線 + 大字）
    y -= 6
    c.setLineWidth(1.4)
    c.setStrokeGray(0.2)
    c.line(margin_x, y, margin_x + content_w, y)
    c.line(margin_x, y - 3, margin_x + content_w, y - 3)
    y -= 10
    c.setFont(FONT_NAME, 17)
    c.setFillGray(0)
    c.drawString(margin_x, y - 17, f"本月合計　共 {trip_count} 趟")
    c.drawRightString(margin_x + content_w, y - 17, f"$ {fmt_money(total)}")
    y -= 17 + 8
    c.setLineWidth(1.4)
    c.setStrokeGray(0.2)
    c.line(margin_x, y, margin_x + content_w, y)
    c.line(margin_x, y - 3, margin_x + content_w, y - 3)
    y -= 10

    # 頁尾
    c.setFont(FONT_NAME, 9)
    c.setFillGray(0.5)
    c.drawString(margin_x, margin_bottom - 4,
                 f"本明細為系統自動產出　內文字級：{body_size:.1f}pt")

    c.showPage()
    c.save()
    return out_path, trip_count, total, body_size


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    csv_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        here, "data", "freight_wide_sample.csv")
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        here, "output", "freight_statement_sample.pdf")
    try:
        rows = load_rows(csv_path)
        out, n, total, size = build_pdf(rows, out_path)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        sys.exit(1)
    print(f"OK  → {out}")
    print(f"趟次={n}  合計={fmt_money(total)}  內文字級={size:.1f}pt")


if __name__ == "__main__":
    main()
