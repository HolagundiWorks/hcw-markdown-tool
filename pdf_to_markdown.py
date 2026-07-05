"""
PDF Converter — Desktop App (Markdown & CSV)
============================================

A modern Tkinter/ttkbootstrap desktop application that analyzes PDF files and
exports them as Markdown or CSV. Scales to large documents (~1000 pages) by
processing pages in batches with live page-by-page progress.

Workflow: **Add files -> Analyze -> Export.** The Analyze step detects whether
each PDF is prose or tabular data — including tables whose header repeats on
every page — and recommends the best export format. CSV export is offered only
when tables are found.

- Markdown extraction uses `pymupdf4llm` (headings, lists, tables, bold/italic).
- Table detection parses pymupdf4llm's Markdown tables (robust for text-aligned
  tables with no grid lines); CSV is written with the stdlib `csv` module.

Run
---
    python pdf_to_markdown.py
"""

import csv
import os
import queue
import re
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import ttkbootstrap as tb
from ttkbootstrap.scrolled import ScrolledText


APP_TITLE = "HCW Markdown Tool"
APP_TAGLINE = "Developed by Holagundi Consulting Wurkz"
BATCH_PAGES = 25          # pages processed per pass (bounds memory for big PDFs)
OCR_DPI = 300             # render resolution for OCR (higher = more accurate)


def resource_dir():
    """Base directory for bundled resources (works frozen and from source)."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def tessdata_dir():
    """Path to the bundled Tesseract language data, or None if absent.

    PyMuPDF bundles the Tesseract engine itself; it only needs the language
    files (eng.traineddata). We ship those in a local `tessdata` folder so OCR
    works with no separate Tesseract installation.
    """
    d = os.path.join(resource_dir(), "tessdata")
    return d if os.path.isfile(os.path.join(d, "eng.traineddata")) else None


def ocr_available():
    return tessdata_dir() is not None


def ocr_to_searchable(path, dpi=OCR_DPI, min_text=8, progress=None):
    """Return a path to a temporary searchable PDF produced by OCR.

    Pages that already carry a real text layer are copied through unchanged;
    image-only (scanned) pages are rasterized and OCR'd into a text layer. The
    result flows through the normal Markdown/table pipeline like any text PDF.
    """
    import pymupdf

    tess = tessdata_dir()
    if tess is None:
        raise RuntimeError(
            "OCR language data not found. Expected tessdata/eng.traineddata "
            "next to the application."
        )

    src = pymupdf.open(path)
    out = pymupdf.open()
    try:
        total = src.page_count
        for i in range(total):
            page = src.load_page(i)
            if len(page.get_text().strip()) >= min_text:
                out.insert_pdf(src, from_page=i, to_page=i)   # keep native text
            else:
                pix = page.get_pixmap(dpi=dpi)
                ocr_bytes = pix.pdfocr_tobytes(language="eng", tessdata=tess)
                ocr_doc = pymupdf.open("pdf", ocr_bytes)
                out.insert_pdf(ocr_doc)
                ocr_doc.close()
            if progress:
                progress(i + 1, total)
        fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="ocr_")
        os.close(fd)
        out.save(tmp)
        return tmp
    finally:
        out.close()
        src.close()


# ======================================================================
#  Backend: page selection, table detection, flattening, CSV export
# ======================================================================
def select_pages(spec: str, parity: str, page_count: int):
    """Resolve which pages to convert into a sorted list of 0-based indices.

    - `spec` is a 1-based range string like "1-5, 8, 10-12" (blank = all pages).
    - `parity` is "all", "odd", or "even" and filters by the page's 1-based number.

    Returns a sorted list of unique 0-based page indices, or None to mean
    "all pages". Raises ValueError on bad input or an empty selection.
    """
    spec = (spec or "").strip()
    parity = (parity or "all").strip().lower()

    if not spec:
        pages = set(range(1, page_count + 1))
        explicit = False
    else:
        pages = set()
        explicit = True
        for chunk in spec.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "-" in chunk:
                start_s, _, end_s = chunk.partition("-")
                start, end = int(start_s), int(end_s)
                if start > end:
                    start, end = end, start
                for p in range(start, end + 1):
                    pages.add(p)
            else:
                pages.add(int(chunk))

    pages = {p for p in pages if 1 <= p <= page_count}
    if parity == "odd":
        pages = {p for p in pages if p % 2 == 1}
    elif parity == "even":
        pages = {p for p in pages if p % 2 == 0}

    if not explicit and parity == "all":
        return None
    if not pages:
        raise ValueError("Page selection does not include any valid pages.")
    return sorted(p - 1 for p in pages)


def _norm_header(cells):
    """Normalize a header row for comparison across pages."""
    return tuple((str(c) if c is not None else "").strip().lower() for c in cells)


def _clean_cell(cell):
    """Strip Markdown formatting from a table cell into plain text."""
    cell = cell.replace("<br>", " ").replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", cell).strip()


def _split_md_row(line):
    """Split a Markdown table row `| a | b |` into raw cell strings, or None."""
    line = line.strip()
    if not line.startswith("|"):
        return None
    inner = line[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return inner.split("|")


def _is_separator_row(cells):
    """True for a Markdown header separator like `|---|:--:|`."""
    joined = "".join(cells)
    return bool(cells) and "-" in joined and all(re.fullmatch(r"[\s:-]*", c) for c in cells)


def parse_markdown_tables(md_text):
    """Extract GitHub-style pipe tables from Markdown text.

    Returns a list of {"header": [...], "rows": [[...], ...]} with all cell
    formatting stripped. More robust than PDF geometry detection for
    text-aligned tables, because it reuses whatever pymupdf4llm rendered.
    """
    lines = md_text.splitlines()
    n = len(lines)
    tables = []
    i = 0
    while i < n:
        row = _split_md_row(lines[i])
        nxt = _split_md_row(lines[i + 1]) if i + 1 < n else None
        if row is not None and nxt is not None and _is_separator_row([_clean_cell(c) for c in nxt]):
            header = [_clean_cell(c) for c in row]
            rows = []
            j = i + 2
            while j < n:
                r = _split_md_row(lines[j])
                if r is None:
                    break
                cells = [_clean_cell(c) for c in r]
                if not _is_separator_row(cells):
                    rows.append(cells)
                j += 1
            tables.append({"header": header, "rows": rows})
            i = j
        else:
            i += 1
    return tables


def analyze_pdf(path, progress=None):
    """Inspect a PDF and report whether it is prose or tabular data.

    Processes pages in batches (so 1000-page PDFs stay responsive and bounded in
    memory) and calls `progress(done, total)` after each batch. Tables sharing a
    header across pages are merged into one logical table.
    """
    import pymupdf
    import pymupdf4llm

    doc = pymupdf.open(path)
    try:
        page_count = doc.page_count
        text_chars = 0
        groups = []
        index = {}

        for start in range(0, page_count, BATCH_PAGES):
            batch = list(range(start, min(start + BATCH_PAGES, page_count)))
            chunks = pymupdf4llm.to_markdown(doc, pages=batch, page_chunks=True)
            for offset, chunk in enumerate(chunks):
                page_no = batch[offset] + 1
                md = chunk.get("text", "")
                for line in md.splitlines():
                    s = line.strip()
                    if s and not s.startswith("|"):
                        text_chars += len(s.lstrip("#").strip())
                for tbl in parse_markdown_tables(md):
                    header, body = tbl["header"], tbl["rows"]
                    if not header and not body:
                        continue
                    key = _norm_header(header)
                    grp = index.get(key)
                    if grp is None:
                        grp = {"header": header, "rows": [], "pages": []}
                        index[key] = grp
                        groups.append(grp)
                    grp["rows"].extend(body)
                    if page_no not in grp["pages"]:
                        grp["pages"].append(page_no)
            if progress:
                progress(min(start + BATCH_PAGES, page_count), page_count)
    finally:
        doc.close()

    has_tables = bool(groups)
    repeated_header = any(len(g["pages"]) > 1 for g in groups)
    table_cells = sum(len(g["header"]) * (len(g["rows"]) + 1) for g in groups)

    if not has_tables:
        kind = "text"
    elif text_chars > max(400, table_cells * 12):
        kind = "mixed"
    else:
        kind = "table"
    recommendation = "csv" if kind == "table" else "markdown"

    if not has_tables:
        summary = f"Text document — {page_count} page(s), no tables detected."
    else:
        total_rows = sum(len(g["rows"]) for g in groups)
        parts = [f"{len(groups)} table(s), {total_rows} data row(s)"]
        if repeated_header:
            spanning = max(groups, key=lambda g: len(g["pages"]))
            parts.append(f"header repeats across {len(spanning['pages'])} pages")
        label = {"table": "Table", "mixed": "Mixed text + tables"}[kind]
        summary = f"{label} — {'; '.join(parts)}."

    return {
        "page_count": page_count,
        "text_chars": text_chars,
        "tables": groups,
        "has_tables": has_tables,
        "repeated_header": repeated_header,
        "kind": kind,
        "recommendation": recommendation,
        "summary": summary,
    }


def convert_pdf(path, out_dir=None, page_spec="", parity="all",
                extract_images=False, progress=None, read_path=None):
    """Convert a single PDF to Markdown. Returns (md_path, markdown_text).

    Batched over pages with a `progress(done, total)` callback. When `out_dir`
    is empty the Markdown is written to a "Markdown" subfolder next to the PDF.
    `read_path` overrides where content is read from (e.g. an OCR'd temp copy)
    while `path` still governs output naming/location.
    """
    import pymupdf
    import pymupdf4llm

    doc = pymupdf.open(read_path or path)
    tmp_img_dir = None
    try:
        page_count = doc.page_count
        pages = select_pages(page_spec, parity, page_count)
        plist = pages if pages is not None else list(range(page_count))

        base = os.path.splitext(os.path.basename(path))[0]
        target_dir = out_dir or os.path.join(os.path.dirname(path), "Markdown")
        os.makedirs(target_dir, exist_ok=True)
        md_path = os.path.join(target_dir, base + ".md")

        # Image extraction: pymupdf4llm mangles spaces in the image path, so we
        # let it write to a space-free temp folder, then move the files into the
        # real "<base>_images" folder and rewrite the Markdown links. This makes
        # extraction work regardless of spaces in the PDF name or output path.
        img_kwargs = {}
        final_img_dir = None
        safe_base = re.sub(r"\s+", "_", base) or "images"
        if extract_images:
            final_img_dir = os.path.join(target_dir, safe_base + "_images")
            os.makedirs(final_img_dir, exist_ok=True)
            tmp_img_dir = _short_path(tempfile.mkdtemp(prefix="pdfimg_"))
            img_kwargs = {
                "write_images": True,
                "image_path": tmp_img_dir,
                "image_format": "png",
                "filename": safe_base,   # image names derive from the real PDF
            }

        total = len(plist)
        parts = []
        for i in range(0, total, BATCH_PAGES):
            batch = plist[i:i + BATCH_PAGES]
            parts.append(pymupdf4llm.to_markdown(doc, pages=batch, **img_kwargs))
            if progress:
                progress(min(i + BATCH_PAGES, total), total)
        markdown_text = "\n".join(parts)

        if extract_images:
            markdown_text = _relocate_images(
                markdown_text, tmp_img_dir, final_img_dir, safe_base + "_images"
            )
    finally:
        doc.close()
        if tmp_img_dir and os.path.isdir(tmp_img_dir):
            import shutil
            shutil.rmtree(tmp_img_dir, ignore_errors=True)

    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(markdown_text)
    return md_path, markdown_text


def _short_path(path):
    """Return the Windows 8.3 short path (no spaces) for an existing path.

    Falls back to the original path off Windows or if unavailable. Used so
    pymupdf4llm — which sanitizes spaces out of image paths — writes to a
    location that actually exists.
    """
    if os.name != "nt":
        return path
    try:
        import ctypes
        from ctypes import wintypes
        _get = ctypes.windll.kernel32.GetShortPathNameW
        _get.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        buf = ctypes.create_unicode_buffer(1024)
        n = _get(path, buf, 1024)
        return buf.value if 0 < n < 1024 else path
    except Exception:  # noqa: BLE001
        return path


def _relocate_images(markdown_text, tmp_img_dir, final_img_dir, rel_folder):
    """Move images out of the temp folder into `final_img_dir` and rewrite the
    Markdown `![](...)` links to a relative `<rel_folder>/<name>` path.

    Links are matched by file basename, so it works no matter what absolute (or
    space-mangled) path pymupdf4llm embedded.
    """
    import shutil

    if not tmp_img_dir or not os.path.isdir(tmp_img_dir):
        return markdown_text
    moved = set()
    for name in os.listdir(tmp_img_dir):
        shutil.move(os.path.join(tmp_img_dir, name), os.path.join(final_img_dir, name))
        moved.add(name)

    def repl(m):
        target = m.group(1)
        bname = os.path.basename(target.replace("\\", "/"))
        if bname in moved:
            return f"![]({rel_folder}/{bname})"
        return m.group(0)

    return re.sub(r"!\[\]\(([^)]*)\)", repl, markdown_text)


# An item number like 8, 8.1, 8.14.1.1, 0013, or (a)/(b).
_ITEM_TOKEN = re.compile(r"^\(?[0-9]+(?:\.[0-9]+)*\)?$|^\([a-zA-Z]\)$")
_LEAD_ITEM = re.compile(r"^\s*(\(?[0-9]+(?:\.[0-9]+)*\)?|\([a-zA-Z]\))[.)]?\s+(.*)$", re.S)


def _split_leading_item(text):
    """Split a leading item number off the front of a description string."""
    m = _LEAD_ITEM.match(text)
    if m:
        return m.group(1), m.group(2).strip()
    if _ITEM_TOKEN.match(text.strip()):
        return text.strip(), ""
    return "", text.strip()


def flatten_table(header, rows):
    """Collapse leading hierarchy/number columns into one "Item No." column.

    Returns (new_header, new_rows, changed). `changed` is False (inputs returned
    unchanged) when the table has no leading columns worth collapsing.
    """
    hl = [(h or "").strip().lower() for h in header]

    def find_kw(keys):
        for i, h in enumerate(hl):
            if any(k in h for k in keys):
                return i
        return None

    desc_idx = find_kw(["description", "particular", "nomenclature", "item of work", "name of work"])
    if desc_idx is None or desc_idx < 2:
        return header, rows, False

    unit_idx = find_kw(["unit"])
    rate_idx = None
    for i, h in enumerate(hl):
        if any(k in h for k in ["rate", "amount", "price", "cost"]):
            rate_idx = i
    tail_start = next((c for c in (unit_idx, rate_idx) if c is not None and c > desc_idx), len(header))

    new_header = ["Item No.", header[desc_idx] or "Description"] + list(header[tail_start:])
    width = len(header)
    new_rows = []
    for row in rows:
        r = list(row) + [""] * (width - len(row))
        item_cells = r[:desc_idx]
        desc_cells = r[desc_idx:tail_start]
        tail_cells = r[tail_start:]

        item = ""
        extra = []
        for c in item_cells:
            cs = (c or "").strip()
            if not cs:
                continue
            if not item and _ITEM_TOKEN.match(cs):
                item = cs
            else:
                extra.append(cs)

        content = " ".join(extra + [(c or "").strip() for c in desc_cells if (c or "").strip()])
        if not item:
            item, content = _split_leading_item(content)
        new_rows.append([item, content] + [(c or "").strip() for c in tail_cells])

    return new_header, new_rows, True


def combine_groups(groups, flatten=True):
    """Merge every detected table into a single (header, rows) table."""
    prepared = []
    for grp in groups:
        header, rows = grp["header"], grp["rows"]
        if flatten:
            header, rows, _ = flatten_table(header, rows)
        prepared.append((header, rows))

    width = max(
        [len(h) for h, _ in prepared] + [len(r) for _, rs in prepared for r in rs] or [0]
    )
    flattened = [h for h, _ in prepared if h and str(h[0]).strip().lower() == "item no."]
    if flatten and flattened:
        header = max(flattened, key=len)
    elif prepared:
        header = max(prepared, key=lambda hr: len(hr[1]))[0]
    else:
        header = []
    header = [("" if c is None else str(c)) for c in header]
    header += [""] * (width - len(header))

    all_rows = []
    for _h, rows in prepared:
        for r in rows:
            cells = ["" if c is None else str(c) for c in r]
            if not any(c.strip() for c in cells):
                continue
            all_rows.append(cells + [""] * (width - len(cells)))
    return header, all_rows


def export_csv(path, analysis=None, out_dir=None, flatten=False, combine=False):
    """Write detected tables to CSV file(s). Returns list of written paths."""
    if analysis is None:
        analysis = analyze_pdf(path)
    groups = analysis["tables"]
    if not groups:
        raise ValueError("No tables were detected in this PDF.")

    base = os.path.splitext(os.path.basename(path))[0]
    target_dir = out_dir or os.path.join(os.path.dirname(path), "CSV")
    os.makedirs(target_dir, exist_ok=True)

    def write_csv(csv_path, header, rows):
        width = max([len(header)] + [len(r) for r in rows] or [0])

        def pad(cells):
            cells = ["" if c is None else str(c) for c in cells]
            return cells + [""] * (width - len(cells))

        with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            if any((c or "").strip() for c in header):
                writer.writerow(pad(header))
            for row in rows:
                writer.writerow(pad(row))

    if combine:
        header, rows = combine_groups(groups, flatten)
        csv_path = os.path.join(target_dir, base + ".csv")
        write_csv(csv_path, header, rows)
        return [csv_path]

    written = []
    single = len(groups) == 1
    for i, grp in enumerate(groups, start=1):
        header, rows = grp["header"], grp["rows"]
        if flatten:
            header, rows, _ = flatten_table(header, rows)
        name = base if single else f"{base}_table{i}"
        csv_path = os.path.join(target_dir, name + ".csv")
        write_csv(csv_path, header, rows)
        written.append(csv_path)
    return written


# ======================================================================
#  UI
# ======================================================================
class App(tb.Window):
    def __init__(self):
        super().__init__(themename="flatly", title=APP_TITLE, size=(1000, 700), minsize=(860, 620))

        self.files = []
        self.analyses = {}
        self.out_dir = tk.StringVar(value="")
        self.page_spec = tk.StringVar(value="")
        self.parity = tk.StringVar(value="All pages")
        self.extract_images = tk.BooleanVar(value=False)
        self.flatten = tk.BooleanVar(value=True)
        self.combine = tk.BooleanVar(value=True)
        self.ocr = tk.BooleanVar(value=False)
        self.ocr_cache = {}   # original path -> temp searchable PDF (this session)
        self.status = tk.StringVar(value="Ready. Add PDF files to begin.")
        self.detected = tk.StringVar(value="Not analyzed yet — click Analyze.")
        self.percent = tk.StringVar(value="")

        self._msg_queue = queue.Queue()
        self._build_ui()
        self.after(80, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self._clear_ocr_cache()
        self.destroy()

    # ---------- UI construction ----------
    def _build_ui(self):
        # Header band
        header = tb.Frame(self, padding=(18, 14))
        header.pack(fill="x")
        tb.Label(header, text=APP_TITLE, font=("Segoe UI Semibold", 20)).pack(anchor="w")
        tb.Label(header, text=f"{APP_TAGLINE}  —  Analyze PDFs, then export to Markdown or CSV.",
                 bootstyle="secondary").pack(anchor="w")

        body = tb.Frame(self, padding=(18, 0, 18, 8))
        body.pack(fill="both", expand=True)

        # Files card
        files_card = tb.Labelframe(body, text="  Files  ", padding=10, bootstyle="secondary")
        files_card.pack(fill="x", pady=(0, 10))
        row = tb.Frame(files_card)
        row.pack(fill="x")
        listwrap = tb.Frame(row)
        listwrap.pack(side="left", fill="both", expand=True)
        self.listbox = tk.Listbox(listwrap, height=6, activestyle="none",
                                  highlightthickness=0, borderwidth=1, relief="flat")
        self.listbox.pack(side="left", fill="both", expand=True)
        lb_scroll = tb.Scrollbar(listwrap, orient="vertical", command=self.listbox.yview, bootstyle="round")
        lb_scroll.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=lb_scroll.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        fbtns = tb.Frame(row)
        fbtns.pack(side="left", fill="y", padx=(10, 0))
        tb.Button(fbtns, text="Add PDFs…", command=self.add_files, bootstyle="primary", width=12).pack(fill="x", pady=2)
        tb.Button(fbtns, text="Remove", command=self.remove_selected, bootstyle="secondary-outline", width=12).pack(fill="x", pady=2)
        tb.Button(fbtns, text="Clear", command=self.clear_files, bootstyle="secondary-outline", width=12).pack(fill="x", pady=2)

        # Options card
        opts = tb.Labelframe(body, text="  Options  ", padding=10, bootstyle="secondary")
        opts.pack(fill="x", pady=(0, 10))

        r1 = tb.Frame(opts)
        r1.pack(fill="x", pady=3)
        tb.Label(r1, text="Output folder:").pack(side="left")
        tb.Entry(r1, textvariable=self.out_dir).pack(side="left", fill="x", expand=True, padx=8)
        tb.Button(r1, text="Browse…", command=self.choose_out_dir, bootstyle="secondary-outline").pack(side="left")
        tb.Button(r1, text="Default", command=lambda: self.out_dir.set(""), bootstyle="secondary-outline").pack(side="left", padx=(6, 0))

        r2 = tb.Frame(opts)
        r2.pack(fill="x", pady=3)
        tb.Label(r2, text="Pages:").pack(side="left")
        tb.Entry(r2, textvariable=self.page_spec, width=16).pack(side="left", padx=(6, 12))
        tb.Label(r2, text="Include:").pack(side="left")
        tb.Combobox(r2, textvariable=self.parity, width=11, state="readonly",
                    values=["All pages", "Odd pages", "Even pages"]).pack(side="left", padx=(6, 0))

        r3 = tb.Frame(opts)
        r3.pack(fill="x", pady=(6, 2))
        tb.Checkbutton(r3, text="Extract images", variable=self.extract_images, bootstyle="round-toggle").pack(side="left", padx=(0, 16))
        tb.Checkbutton(r3, text="Flatten item numbers", variable=self.flatten,
                       command=self._refresh_report, bootstyle="round-toggle").pack(side="left", padx=(0, 16))
        tb.Checkbutton(r3, text="Combine into one table", variable=self.combine,
                       command=self._refresh_report, bootstyle="round-toggle").pack(side="left", padx=(0, 16))
        self.ocr_toggle = tb.Checkbutton(r3, text="OCR scanned pages", variable=self.ocr,
                                         command=self._on_ocr_toggle, bootstyle="round-toggle")
        self.ocr_toggle.pack(side="left")
        if not ocr_available():
            self.ocr_toggle.configure(state="disabled")
            tb.Label(r3, text="(language data missing)", bootstyle="warning").pack(side="left", padx=(6, 0))

        # Actions
        actions = tb.Frame(body)
        actions.pack(fill="x", pady=(0, 10))
        self.analyze_btn = tb.Button(actions, text="Analyze", command=self.start_analyze, bootstyle="primary", width=14)
        self.analyze_btn.pack(side="left")
        self.md_btn = tb.Button(actions, text="Export Markdown", command=self.start_markdown, bootstyle="info", width=16)
        self.md_btn.pack(side="left", padx=(8, 0))
        self.csv_btn = tb.Button(actions, text="Export CSV", command=self.start_csv, bootstyle="success", width=14, state="disabled")
        self.csv_btn.pack(side="left", padx=(8, 0))
        tb.Label(actions, textvariable=self.detected, bootstyle="secondary").pack(side="left", padx=14)

        # Preview
        prev = tb.Labelframe(body, text="  Preview / Analysis  ", padding=6, bootstyle="secondary")
        prev.pack(fill="both", expand=True)
        self.preview = ScrolledText(prev, autohide=True, font=("Cascadia Mono", 10), wrap="word")
        self.preview.pack(fill="both", expand=True)

        # Status + progress, with the company wordmark in the bottom-right corner
        bottom = tb.Frame(self, padding=(18, 6, 18, 10))
        bottom.pack(fill="x", side="bottom")

        self._make_logo(bottom).pack(side="right", anchor="se", padx=(14, 0))

        left = tb.Frame(bottom)
        left.pack(side="left", fill="x", expand=True)
        prow = tb.Frame(left)
        prow.pack(fill="x")
        self.progress = tb.Progressbar(prow, mode="determinate", bootstyle="success-striped")
        self.progress.pack(side="left", fill="x", expand=True)
        tb.Label(prow, textvariable=self.percent, width=6, anchor="e").pack(side="left", padx=(8, 0))
        tb.Label(left, textvariable=self.status, bootstyle="secondary").pack(fill="x", pady=(4, 0))

    def _make_logo(self, parent):
        """Render the Holagundi Consulting Wurkz wordmark as a small canvas.

        Reproduced as vector text (rather than a bundled image) so it stays crisp
        and needs no extra asset or Pillow dependency when packaged as an .exe.
        """
        try:
            bg = self.style.colors.bg
        except Exception:  # noqa: BLE001 - fall back to a neutral canvas bg
            bg = "white"
        fg = "#111111"
        canvas = tk.Canvas(parent, width=196, height=46, bg=bg,
                           highlightthickness=0, bd=0)
        canvas.create_text(194, 6, text="HOLAGUNDI", anchor="ne",
                           font=("Arial Black", 16, "bold"), fill=fg)
        # Letter-spaced subtitle to match the wordmark's tracked lower line.
        canvas.create_text(194, 30, text="C O N S U L T I N G   W U R K Z", anchor="ne",
                           font=("Segoe UI", 8), fill=fg)
        return canvas

    # ---------- File list ----------
    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select PDF files",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                self.listbox.insert("end", os.path.basename(p))
        self._files_changed()

    def remove_selected(self):
        for idx in reversed(self.listbox.curselection()):
            self.listbox.delete(idx)
            del self.files[idx]
        self._files_changed()

    def clear_files(self):
        self.listbox.delete(0, "end")
        self.files.clear()
        self._files_changed()

    def choose_out_dir(self):
        d = filedialog.askdirectory(title="Choose output folder")
        if d:
            self.out_dir.set(d)

    def _update_status(self):
        n = len(self.files)
        self.status.set(f"{n} file{'s' if n != 1 else ''} queued." if n else "Ready. Add PDF files to begin.")

    def _on_select(self, _event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        path = self.files[sel[0]]
        analysis = self.analyses.get(path)
        if analysis:
            self.detected.set("Detected: " + analysis["summary"])
            self._show_report(path, analysis)
        else:
            self.detected.set("Not analyzed yet — click Analyze.")

    def _files_changed(self):
        self.analyses.clear()
        self._clear_ocr_cache()
        self.csv_btn.configure(state="disabled")
        self.detected.set("Not analyzed yet — click Analyze.")
        self._update_status()

    def _clear_ocr_cache(self):
        for tmp in self.ocr_cache.values():
            try:
                os.remove(tmp)
            except OSError:
                pass
        self.ocr_cache.clear()

    def _on_ocr_toggle(self):
        # OCR changes the extracted content, so any prior analysis is stale.
        if self._busy():
            return
        self.analyses.clear()
        self._clear_ocr_cache()
        self.csv_btn.configure(state="disabled")
        self.detected.set("OCR changed — click Analyze again." if self.ocr.get()
                          else "Not analyzed yet — click Analyze.")

    def _refresh_report(self):
        if not self._busy():
            self._on_select()

    # ---------- Job dispatch ----------
    def _busy(self):
        return str(self.analyze_btn["state"]) == "disabled"

    def _start_job(self, kind):
        if self._busy():
            return
        if not self.files:
            messagebox.showinfo(APP_TITLE, "Add at least one PDF file first.")
            return
        for b in (self.analyze_btn, self.md_btn, self.csv_btn):
            b.configure(state="disabled")

        self.progress.configure(mode="determinate", value=0, maximum=100)
        self.percent.set("0%")

        opts = {
            "kind": kind,
            "files": list(self.files),
            "out_dir": self.out_dir.get().strip() or None,
            "page_spec": self.page_spec.get(),
            "parity": self.parity.get().split()[0].lower(),
            "extract": self.extract_images.get(),
            "flatten": self.flatten.get(),
            "combine": self.combine.get(),
            "ocr": self.ocr.get(),
        }
        threading.Thread(target=self._job_worker, args=(opts,), daemon=True).start()

    def start_analyze(self):
        self._start_job("analyze")

    def start_markdown(self):
        self._start_job("markdown")

    def start_csv(self):
        self._start_job("csv")

    def _job_worker(self, opts):
        kind = opts["kind"]
        files = opts["files"]
        n_files = len(files)
        done = 0
        errors = []
        outputs = []
        verb = {"analyze": "Analyzing", "markdown": "Converting", "csv": "Exporting CSV from"}[kind]

        for i, path in enumerate(files, start=1):
            name = os.path.basename(path)
            self._post(("status", f"{verb} ({i}/{n_files}): {name}"))

            def progress(cur, total, _i=i, _name=name):
                self._post(("pageprog", (f"{verb} ({_i}/{n_files}): {_name} — page {cur}/{total}", cur, total)))

            try:
                # For OCR jobs, first build/reuse a searchable copy of the PDF.
                read_path = path
                if opts["ocr"] and kind in ("analyze", "markdown"):
                    read_path = self._ocr_source(path, i, name, n_files)

                if kind == "analyze":
                    analysis = analyze_pdf(read_path, progress=progress)
                    self._post(("analysis", (path, analysis)))
                    done += 1
                elif kind == "markdown":
                    md_path, text = convert_pdf(
                        path, opts["out_dir"], opts["page_spec"], opts["parity"],
                        opts["extract"], progress=progress, read_path=read_path,
                    )
                    outputs.append(md_path)
                    self._post(("preview", (path, text)))
                    done += 1
                elif kind == "csv":
                    written = export_csv(
                        path, self.analyses.get(path), opts["out_dir"],
                        opts["flatten"], opts["combine"],
                    )
                    outputs.extend(written)
                    done += 1
                    self._post(("fileprog", (f"Exported CSV ({i}/{n_files}): {name}", i, n_files)))
            except Exception as exc:  # noqa: BLE001 - surface failures to the user
                errors.append(f"{name}: {exc}")

        self._post(("finished", (kind, done, n_files, errors, outputs)))

    def _post(self, msg):
        self._msg_queue.put(msg)

    def _ocr_source(self, path, i, name, n_files):
        """Return a searchable OCR'd copy of `path`, building it once per file."""
        cached = self.ocr_cache.get(path)
        if cached and os.path.exists(cached):
            return cached

        def prog(cur, total):
            self._post(("pageprog", (f"OCR ({i}/{n_files}): {name} — page {cur}/{total}", cur, total)))

        tmp = ocr_to_searchable(path, progress=prog)
        self.ocr_cache[path] = tmp
        return tmp

    def _show_report(self, path, analysis):
        flatten = self.flatten.get()
        combine = self.combine.get()
        note = "  (flattened)" if flatten else ""
        lines = [f"# Analysis: {os.path.basename(path)}", "", analysis["summary"], ""]

        def render_table(title, header, rows):
            lines.append(title)
            if any((c or "").strip() for c in header):
                lines.append(" | ".join(str(c) for c in header))
                lines.append(" | ".join("---" for _ in header))
            for row in rows[:15]:
                lines.append(" | ".join("" if c is None else str(c) for c in row))
            extra = len(rows) - 15
            if extra > 0:
                lines.append(f"... (+{extra} more rows)")
            lines.append("")

        if combine and analysis["tables"]:
            header, rows = combine_groups(analysis["tables"], flatten)
            pages = sorted({p for g in analysis["tables"] for p in g["pages"]})
            render_table(f"## Combined table  ({len(rows)} rows, pages {', '.join(map(str, pages))}){note}", header, rows)
        else:
            for i, grp in enumerate(analysis["tables"], start=1):
                header, rows = grp["header"], grp["rows"]
                if flatten:
                    header, rows, _ = flatten_table(header, rows)
                render_table(f"## Table {i}  (pages {', '.join(map(str, grp['pages']))}){note}", header, rows)

        self._set_preview("\n".join(lines))

    def _set_preview(self, text):
        self.preview.text.delete("1.0", "end")
        self.preview.text.insert("1.0", text)

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self._msg_queue.get_nowait()
                if kind == "status":
                    self.status.set(payload)
                elif kind in ("pageprog", "fileprog"):
                    label, cur, total = payload
                    self.status.set(label)
                    if total:
                        pct = int(cur * 100 / total)
                        self.progress.configure(mode="determinate", maximum=total, value=cur)
                        self.percent.set(f"{pct}%")
                elif kind == "analysis":
                    path, analysis = payload
                    self.analyses[path] = analysis
                    if analysis["has_tables"]:
                        self.csv_btn.configure(state="normal")
                    self.detected.set("Detected: " + analysis["summary"])
                    self._show_report(path, analysis)
                elif kind == "preview":
                    _path, text = payload
                    self._set_preview(text)
                elif kind == "finished":
                    job, done, total, errors, outputs = payload
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                    self.percent.set("")
                    for b in (self.analyze_btn, self.md_btn):
                        b.configure(state="normal")
                    if any(a["has_tables"] for a in self.analyses.values()):
                        self.csv_btn.configure(state="normal")
                    self._finish_status(job, done, total, errors, outputs)
        except queue.Empty:
            pass
        self.after(80, self._drain_queue)

    def _finish_status(self, job, done, total, errors, outputs):
        noun = {"analyze": "analyzed", "markdown": "converted", "csv": "exported"}[job]
        if errors:
            self.status.set(f"Done: {done}/{total} {noun}, {len(errors)} failed.")
            messagebox.showerror(APP_TITLE, "Some files failed:\n\n" + "\n".join(errors))
        else:
            extra = f"  →  {len(outputs)} file(s) written" if outputs else ""
            self.status.set(f"Done: {done}/{total} {noun}.{extra}")


def _selftest(pdf_path, out_dir):
    """Headless conversion used to validate a packaged build. Writes a result
    file (windowed exes have no console) and exits with 0 on success."""
    import sys
    os.makedirs(out_dir, exist_ok=True)
    result = os.path.join(out_dir, "selftest_result.txt")
    try:
        analysis = analyze_pdf(pdf_path)
        md_path, _ = convert_pdf(pdf_path, out_dir=out_dir)
        csvs = export_csv(pdf_path, analysis, out_dir=out_dir, flatten=True, combine=True) \
            if analysis["has_tables"] else []
        # Validate the OCR path in the packaged build too.
        ocr_ok = ocr_available()
        ocr_text_len = -1
        if ocr_ok:
            import pymupdf
            tmp = ocr_to_searchable(pdf_path)
            ocr_text_len = len(pymupdf.open(tmp).load_page(0).get_text().strip())
            os.remove(tmp)
        with open(result, "w", encoding="utf-8") as fh:
            fh.write(f"OK\nkind={analysis['kind']}\nmd={md_path}\ncsv={csvs}\n"
                     f"ocr_available={ocr_ok}\nocr_text_len={ocr_text_len}\n")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        with open(result, "w", encoding="utf-8") as fh:
            fh.write(f"FAIL\n{exc!r}\n")
        sys.exit(1)


def main():
    import sys
    if len(sys.argv) >= 4 and sys.argv[1] == "--selftest":
        _selftest(sys.argv[2], sys.argv[3])
        return
    try:
        import pymupdf4llm  # noqa: F401
    except ImportError:
        tb.Window()  # minimal root so the dialog can show
        messagebox.showwarning(
            APP_TITLE,
            "The 'pymupdf4llm' package is not installed.\n\n"
            "Install it with:  pip install pymupdf4llm",
        )
        return
    App().mainloop()


if __name__ == "__main__":
    main()
