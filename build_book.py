#!/usr/bin/env python3
"""Build PROJECT_BOOK.pdf from PROJECT_BOOK.md.

Pure-Python pipeline (no system dependencies): Markdown -> HTML -> PDF.
Run with the project venv:
    "property-triage-system/.venv/bin/python" build_book.py
"""
import os
import sys

import markdown
from xhtml2pdf import pisa

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "PROJECT_BOOK.md")
OUT = os.path.join(HERE, "PROJECT_BOOK.pdf")

CSS = """
@page { size: a4; margin: 2.2cm 2cm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 11pt; color: #1a1a1a; line-height: 1.5; }
h1 { font-size: 22pt; color: #14213d; margin: 0 0 6pt 0; }
h2 { font-size: 15pt; color: #14213d; border-bottom: 1px solid #cccccc; padding-bottom: 3pt; margin-top: 16pt; }
h3 { font-size: 12pt; color: #3a3a3a; margin-top: 10pt; }
p { margin: 5pt 0; }
ul, ol { margin: 5pt 0 5pt 14pt; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; }
th, td { border: 1px solid #bbbbbb; padding: 4pt 6pt; font-size: 10pt; text-align: left; }
th { background-color: #14213d; color: #ffffff; }
code { font-family: Courier, monospace; background-color: #f2f2f2; font-size: 9.5pt; }
em { color: #666666; }
.cover { text-align: center; padding-top: 5cm; }
.cover h1 { font-size: 28pt; }
.pagebreak { page-break-after: always; }
"""


def main() -> int:
    with open(SRC, encoding="utf-8") as fh:
        md_text = fh.read()
    body = markdown.markdown(md_text, extensions=["extra", "toc", "sane_lists"])
    html = (
        "<html><head><meta charset='utf-8'>"
        f"<style>{CSS}</style></head><body>{body}</body></html>"
    )
    with open(OUT, "wb") as out:
        result = pisa.CreatePDF(src=html, dest=out, encoding="utf-8")
    if result.err:
        print(f"PDF generation reported {result.err} error(s).", file=sys.stderr)
        return 1
    size_kb = os.path.getsize(OUT) / 1024
    print(f"OK: wrote {OUT} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
