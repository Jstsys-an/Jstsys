# 車主運費明細 PDF 樣張（driver-freight）

company-os 的第一塊產出。用途：司機在 LINE 報趟 → 內勤填 Google 試算表（寬表）→ 程式讀表 → 產出「單一車主、單月」的運費明細 PDF，給年長車主看，重點是「一張就看得懂」。

目前範圍：只做「產出 PDF」這一端，用**假資料**（1 位車主 × 1 個月）。

## 檔案

| 檔案 | 說明 |
| --- | --- |
| `generate_pdf.py` | 讀寬表 CSV，產出單一車主、單月、A4 直式、**單頁**的運費明細 PDF |
| `data/freight_wide_sample.csv` | 假資料（寬表）。欄位：日期／車主／司機／車號／路線／運費／備註1~7 |
| `output/freight_statement_sample.pdf` | 產出的樣張 PDF |
| `preview.html` | 版面預覽（A4 比例卡片，淺色底、繁中），和 PDF 版面一致，可直接用瀏覽器開 |

## 執行

```bash
cd company-os/driver-freight
python3 generate_pdf.py
# 或指定輸入/輸出：
python3 generate_pdf.py data/freight_wide_sample.csv output/freight_statement_sample.pdf
```

需求：`reportlab`（繁中使用內建 CJK 字型 `STSong-Light`，免安裝外部字型）。

## 版面規則（對象為年長者）

- 大標題、關鍵數字（本月趟次／本月運費）置頂特大。
- 明細按日期分組，每天結束後加一條「逐日小計」灰底帶（略大、加粗），方便逐日對帳。
- 底部「本月合計」雙線 + 大字。
- 「有值才印」：單一車主 → 車主不重複印在每列（放標題）；空的備註不佔版面；整月全空的備註欄省略。
- 一張到底、不分頁；筆數多時自動縮字級／縮行高壓進一頁，但維持可讀（內文下限約 9pt，目前樣張為 11pt）。
- 黑白為主，僅少量灰階分隔線，印出來清楚。

## 已知限制

- PDF 的繁中使用 `STSong-Light` 標準 CID 字型，需檢視器具備對應字型（Adobe Reader、瀏覽器、macOS 預覽程式皆可）。某些精簡版 `poppler`（如 `pdftoppm`）缺 Adobe-GB1 語言包，轉圖時中文不顯示，屬該工具限制，非 PDF 問題。
- 逐日小計每天多佔一列，天數越多越佔版面；假資料刻意把趟次集中在 7 天（含單趟日）以在一頁內維持大字級。
