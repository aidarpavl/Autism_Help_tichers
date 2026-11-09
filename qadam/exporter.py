"""Экспорт Markdown-текста планов и отчётов в Word."""
from __future__ import annotations

import io
import re

from docx import Document


def _inline(paragraph, text: str) -> None:
    for i, chunk in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if chunk:
            run = paragraph.add_run(chunk.replace("*", ""))
            run.bold = i % 2 == 1


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def markdown_to_docx(markdown: str) -> bytes:
    doc = Document()
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.lstrip().startswith("|"):
            block = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                if not re.fullmatch(r"[\s|:\-]+", lines[i]):
                    block.append(_cells(lines[i]))
                i += 1
            if block:
                width = max(len(r) for r in block)
                table = doc.add_table(rows=len(block), cols=width)
                table.style = "Table Grid"
                for r, row in enumerate(block):
                    for c, value in enumerate(row):
                        cell = table.cell(r, c).paragraphs[0]
                        _inline(cell, value)
                doc.add_paragraph()
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)", line)
        bullet = re.match(r"^\s*[-*]\s+(.*)", line)
        numbered = re.match(r"^\s*\d+[.)]\s+(.*)", line)
        if heading:
            doc.add_heading(heading.group(2).replace("*", ""), level=min(len(heading.group(1)), 4))
        elif bullet:
            _inline(doc.add_paragraph(style="List Bullet"), bullet.group(1))
        elif numbered:
            _inline(doc.add_paragraph(style="List Number"), numbered.group(1))
        elif line.strip() and line.strip() != "---":
            _inline(doc.add_paragraph(), line)
        i += 1
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
