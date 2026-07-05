# HCW Markdown Tool

*Developed by Holagundi Consulting Wurkz.*

A modern desktop app (Python + `ttkbootstrap`) that **analyzes** PDF files and
exports them as **Markdown** or **CSV**. It detects whether a PDF is prose or a
table — including tables whose header repeats on every page — and recommends the
right format. Built to handle large documents (**~1000 pages**) with live,
page-by-page progress.

- Markdown extraction uses [`pymupdf4llm`](https://pypi.org/project/pymupdf4llm/)
  (headings, lists, tables, bold/italic, code).
- Table detection parses the Markdown tables pymupdf4llm produces — this
  reliably catches text-aligned tables that have no ruled grid lines (where
  geometry-based detection fails). CSV is written with the stdlib `csv`.

## Workflow

1. **Add PDFs** to the list.
2. Click **Analyze** — the app reports each file's type in the *Detected* banner
   and preview pane (e.g. *"Table — header repeats across 3 pages"*).
3. Click **Export Markdown** and/or **Export CSV**. The CSV button enables only
   when tables are detected.

## Running it

### Option A — Windows installer (recommended)

Run **`installer\HCW-Markdown-Tool-Setup-1.0.0.exe`**. It installs the app,
creates Start Menu (and optional desktop) shortcuts, and registers an
uninstaller under *Apps & features*. You can install for all users or just
yourself.

### Option B — Portable app (no install, no Python needed)

Double-click **`dist\HCW Markdown Tool.exe`**. It's a single self-contained file
(~104 MB) that bundles Python and every dependency — copy it anywhere and run.
(First launch is a few seconds slower while it unpacks.)

### Option C — From source

Double-click **`Run PDF to Markdown.bat`**, or from a terminal:

```
pip install -r requirements.txt
python pdf_to_markdown.py
```

If `python` isn't on your PATH, use the full interpreter path:

```
%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe pdf_to_markdown.py
```

## Building the portable .exe

Double-click **`build_exe.bat`** (uses PyInstaller). Output lands in
`dist\HCW Markdown Tool.exe`. The build excludes heavy unused libraries (torch,
OpenCV, pandas, …) to keep the file small.

## Building the Windows installer

After building the .exe, double-click **`build_installer.bat`** (uses
[Inno Setup 6](https://jrsoftware.org/isdl.php)). Output lands in
`installer\HCW-Markdown-Tool-Setup-1.0.0.exe`. If Inno Setup isn't installed:

```
winget install JRSoftware.InnoSetup
```

The installer layout lives in `installer.iss` (app name, version, shortcuts).

## Features

- **Analyze** — classifies each PDF as *text*, *table*, or *mixed*, and detects
  tables whose header repeats across pages (merged into one logical table).
- **Export Markdown** — into a **`Markdown`** subfolder next to each PDF.
- **Export CSV** — into a **`CSV`** subfolder next to each PDF.
- **Combine into one table** (on by default) — stacks every table across all
  pages/sections into a single `<name>.csv` under one header. With it off, each
  distinct table is written separately (`<name>_table1.csv`, `<name>_table2.csv`,
  …).
- **Flatten item numbers** (on by default) — collapses hierarchical numbering
  columns (e.g. `8.1` / `8.1.1` / `8.1.1.1`, including numbers embedded at the
  start of a description) into a single clean **Item No.** column. This also
  normalizes sections with different header shapes so they combine cleanly. The
  Preview / Analysis pane reflects both toggles live so you see the result
  before exporting.
- **Batch** — add and process many PDFs at once.
- **Reset** — one click returns the app to its just-launched state: clears the
  file list, restores every option to its default, and wipes the preview,
  status, and progress bar.
- **Page ranges** — e.g. `1-5, 8, 10-12` (leave blank for all pages).
- **Odd / even pages** — the *Include* selector filters pages (All / Odd / Even)
  and combines with the range, e.g. `1-10` + *Odd* → pages 1, 3, 5, 7, 9.
- **Output folder** — leave blank (default) for the per-PDF subfolders above, or
  pick an explicit folder to send everything to one place.
- **Image extraction** — optionally save embedded images to a `<name>_images`
  folder and link them from the Markdown.
- **OCR scanned pages** — the *OCR scanned pages* toggle reads text from
  image-only / scanned PDFs (no text layer). Powered by the Tesseract engine
  **bundled inside PyMuPDF** plus a small `tessdata/eng.traineddata` file, so it
  works with **no separate Tesseract install** — even in the portable .exe.
  Pages that already have real text are passed through untouched; only scanned
  pages are OCR'd (at 300 dpi). *Note:* OCR reliably recovers **text/Markdown**
  from scans; reconstructing **tables/CSV** from a scanned image is best-effort,
  because scanned tables have no ruled lines for the detector to follow. For
  clean CSVs, use PDFs that have a real text layer.
- **Large-document ready** — pages are processed in batches of 25, so a
  ~1000-page PDF stays responsive and shows a real page-by-page progress bar
  with a percentage (not just a spinner). Memory stays bounded.
- **Modern UI** — flat `ttkbootstrap` theme, toggle switches, and a scrollable
  preview. Work runs on a background thread so the window never freezes.

By default, `C:\Docs\foo.pdf` produces `C:\Docs\Markdown\foo.md` and, if it has
tables, `C:\Docs\CSV\foo.csv`.

## Requirements (source install)

- Python 3.9+ (tested on 3.14) with Tkinter (bundled with standard CPython).
- Packages in `requirements.txt`: `pymupdf4llm`, `pymupdf`, `ttkbootstrap`
  (and `pyinstaller` to build the .exe).

  ```
  pip install -r requirements.txt
  ```

## Files

| File | Purpose |
|------|---------|
| `dist\HCW Markdown Tool.exe` | The portable app — run this |
| `pdf_to_markdown.py` | Source code |
| `tessdata\eng.traineddata` | OCR language data (English) — needed for OCR |
| `build_exe.bat` | Rebuild the portable .exe |
| `installer.iss` | Inno Setup script for the Windows installer |
| `build_installer.bat` | Build the Windows installer (needs Inno Setup) |
| `Run PDF to Markdown.bat` | Run from source (dev) |
| `requirements.txt` | Python dependencies |

To OCR other languages, drop the matching `<lang>.traineddata` into `tessdata\`
(from the [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) repo).
