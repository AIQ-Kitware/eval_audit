#!/usr/bin/env python
"""Render EXPERIMENTS_AND_RESULTS.md to PDF.

There is no LaTeX, pandoc, wkhtmltopdf, or headless browser on the analysis
hosts, so this goes markdown -> HTML -> PDF through xhtml2pdf/reportlab, which
is pure Python and needs no system libraries.

Setup and use (throwaway env - do NOT install into the project venv):

    uv venv /tmp/pdfenv --python 3.12
    uv pip install --python /tmp/pdfenv/bin/python markdown xhtml2pdf pygments
    cd docs/experiment-dump-2026-07-27
    /tmp/pdfenv/bin/python render_pdf.py EXPERIMENTS_AND_RESULTS.md \
                                         EXPERIMENTS_AND_RESULTS.pdf

Images resolve relative to the markdown file. Three quirks are worked around
below, each marked at its site: core PDF fonts have no emoji, reportlab aborts
the whole build on an empty table cell, and long unbroken tokens overflow their
column without CJK-mode wrapping.
"""
import sys
import os
import html
import re

import markdown
from xhtml2pdf import pisa

CSS = """
@page {
  size: letter portrait;
  margin: 0.7in 0.65in 0.75in 0.65in;
  @frame footer { -pdf-frame-content: footerContent; bottom: 0.35in;
                  margin-left: 0.65in; margin-right: 0.65in; height: 0.3in; }
}
body { font-family: Helvetica, sans-serif; font-size: 8.6pt; line-height: 1.34;
       color: #16191d; }
h1 { font-size: 19pt; color: #12354f; margin: 0 0 4pt 0; padding-bottom: 3pt;
     border-bottom: 2pt solid #12354f; }
h2 { font-size: 13.5pt; color: #12354f; margin: 17pt 0 5pt 0;
     padding-bottom: 2pt; border-bottom: 0.7pt solid #b8c4cc; }
h3 { font-size: 11pt; color: #1f4f70; margin: 13pt 0 4pt 0; }
h4 { font-size: 9.4pt; color: #37474f; margin: 10pt 0 3pt 0; }
p  { margin: 0 0 5pt 0; }
ul, ol { margin: 0 0 5pt 15pt; }
li { margin-bottom: 1.6pt; }
a { color: #14568c; text-decoration: none; }
code { font-family: Courier, monospace; font-size: 7.6pt;
       background-color: #f1f3f5; color: #8a2540; }
pre { font-family: Courier, monospace; font-size: 7.2pt;
      background-color: #f5f6f7; border-left: 2.5pt solid #b8c4cc;
      padding: 4pt 6pt; margin: 0 0 6pt 0; }
pre code { background-color: transparent; color: #16191d; }
blockquote { margin: 5pt 0 7pt 0; padding: 5pt 8pt;
             background-color: #fff8e6; border-left: 2.5pt solid #d8a417;
             color: #4a3c12; }
blockquote p { margin: 0 0 3pt 0; }
table { margin: 3pt 0 9pt 0; width: 100%; }
th { background-color: #12354f; color: #ffffff; font-size: 7.4pt;
     padding: 3pt 4pt; text-align: left; -pdf-word-wrap: CJK; }
td { font-size: 7.4pt; padding: 2.6pt 4pt;
     border-bottom: 0.35pt solid #dde3e8; -pdf-word-wrap: CJK; }
tr { -pdf-keep-with-next: false; }
img { -pdf-image-resolution: 168dpi; }
.figure { margin: 5pt 0 9pt 0; text-align: center; }
hr { border: none; border-top: 0.6pt solid #ccd4da; margin: 11pt 0; }
#footerContent { font-size: 7pt; color: #7a848c; text-align: center; }
"""


def build(md_path: str, pdf_path: str) -> int:
    md_dir = os.path.dirname(os.path.abspath(md_path))
    text = open(md_path, encoding="utf-8").read()

    # Emoji and box-drawing glyphs are absent from the core PDF fonts and would
    # render as black boxes; map the few we use to ASCII equivalents.
    for bad, good in [("⚠️", "[!]"), ("✅", "[yes]"), ("❌", "[no]"),
                      ("⚠", "[!]"), ("→", "-&gt;"), ("↔", "&lt;-&gt;"),
                      ("″", '"'), ("·", "&middot;"), ("×", "x"),
                      ("κ", "kappa"), ("τ", "tau"), ("ρ", "rho"),
                      ("σ", "sigma"), ("Δ", "delta"), ("≈", "~"),
                      ("≥", "&gt;=") , ("≤", "&lt;="), ("—", "&mdash;"),
                      ("–", "&ndash;"), ("…", "..."), ("’", "'"),
                      ("“", '"'), ("”", '"')]:
        text = text.replace(bad, good)

    body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "codehilite", "toc", "sane_lists"],
        extension_configs={"codehilite": {"noclasses": True,
                                          "pygments_style": "friendly"}},
    )

    # Absolute-path the local images so pisa can find them, and centre them.
    def fix_img(m):
        src = m.group(1)
        if src.startswith(("http://", "https://", "/")):
            full = src
        else:
            full = os.path.join(md_dir, src)
        if not os.path.isfile(full):
            return f'<p><i>[missing image: {html.escape(src)}]</i></p>'
        return (f'<div class="figure"><img src="{html.escape(full)}" '
                f'style="max-width:6.6in;"/></div>')

    body = re.sub(r'<img[^>]*src="([^"]+)"[^>]*/?>', fix_img, body)

    # reportlab computes a negative available width for a truly empty table
    # cell (width 8pt == leftPadding+rightPadding, then float error tips it
    # under zero) and aborts the whole build. Markdown's `| x |||||` section
    # separators produce exactly that. Give every empty cell a non-breaking
    # space so it has content to measure.
    body = re.sub(r'<(td|th)([^>]*)>\s*</\1>', r'<\1\2>&nbsp;</\1>', body)
    # A bare image inside a <p> leaves an empty paragraph behind.
    body = re.sub(r'<p>\s*(<div class="figure">.*?</div>)\s*</p>', r'\1',
                  body, flags=re.S)

    title = os.path.basename(md_path)
    doc = (f'<html><head><meta charset="utf-8"/><style>{CSS}</style></head>'
           f'<body><div id="footerContent">{html.escape(title)} '
           f'&mdash; page <pdf:pagenumber> of <pdf:pagecount></div>'
           f'{body}</body></html>')

    with open(pdf_path, "wb") as fh:
        result = pisa.CreatePDF(doc, dest=fh, encoding="utf-8")
    return 1 if result.err else 0


if __name__ == "__main__":
    sys.exit(build(sys.argv[1], sys.argv[2]))
