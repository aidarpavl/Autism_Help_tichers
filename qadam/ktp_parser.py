"""Разбор календарно-тематического плана из xlsx, csv, docx, pdf и txt."""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

HEADERS = {
    "topic": ("тақыры", "содержание урока"),
    "goals": ("цел", "мақсат", "задач", "ожидаемый результат", "результат"),
    "lesson_date": ("дата", "күн", "мерзім", "срок"),
    "hours": ("час", "сағат", "кол-во", "количество"),
    "section": ("раздел", "бөлім", "сквозная", "модуль", "блок"),
}
TOPIC_WORD_RE = re.compile(r"(?<![а-яё])тем[аыу](?![а-яё])")  # не «тематический»
LINE_RE = re.compile(r"^\s*(?:урок\s*|сабақ\s*)?(\d{1,3})\s*[.):\-–—]\s*(.{3,})$", re.I)
SECTION_RE = re.compile(r"^\s*(раздел|бөлім|модуль|четверть|тоқсан)\b", re.I)


class ParseError(ValueError):
    pass


def parse(filename: str, data: bytes) -> list[dict]:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    readers = {"xlsx": _xlsx, "csv": _csv, "docx": _docx, "pdf": _pdf, "txt": _txt}
    if ext not in readers:
        raise ParseError("Поддерживаются файлы .xlsx, .csv, .docx, .pdf и .txt.")
    try:
        topics = readers[ext](data)
    except ParseError:
        raise
    except Exception as e:
        raise ParseError("Не удалось прочитать файл. Возможно, он повреждён или защищён паролем.") from e
    if not topics:
        raise ParseError("Темы не найдены. Проверьте, что в таблице есть столбец «Тема», "
                         "или добавьте темы вручную.")
    return topics


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.strftime("%d.%m.%Y")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return re.sub(r"\s+", " ", str(v)).strip()


def _map_header(row: list[str]) -> dict[str, int] | None:
    if sum(1 for c in row if c) < 2:  # заголовок документа, а не шапка таблицы
        return None
    mapping: dict[str, int] = {}
    for i, raw in enumerate(row):
        text = raw.lower()
        if not text:
            continue
        # «раздел/тема» и «тема урока» — это тема; чистый «раздел» — раздел.
        for field in ("topic", "goals", "lesson_date", "hours", "section"):
            matched = any(k in text for k in HEADERS[field])
            if field == "topic":
                matched = matched or bool(TOPIC_WORD_RE.search(text))
            if field not in mapping and matched:
                mapping[field] = i
                break
    return mapping if "topic" in mapping else None


def _table(rows: list[list[str]]) -> list[dict]:
    rows = [[_cell(c) for c in r] for r in rows]
    start, mapping = None, None
    for i, r in enumerate(rows[:25]):
        mapping = _map_header(r)
        if mapping:
            start = i
            break
    if mapping is None or start is None:
        return []
    topics, section = [], ""
    t_col = mapping["topic"]
    for r in rows[start + 1:]:
        filled = [c for c in r if c]
        if not filled:
            continue
        if len(filled) == 1 and not re.fullmatch(r"\d+", filled[0]):
            section = filled[0]  # объединённая строка-заголовок раздела
            continue
        get = lambda f: r[mapping[f]] if f in mapping and mapping[f] < len(r) else ""
        topic = r[t_col] if t_col < len(r) else ""
        if not topic or re.fullmatch(r"[\d\s.,]+", topic):
            continue  # строка нумерации столбцов или итогов
        if topic.lower().startswith(("итого", "всего", "барлығы")):
            continue
        section = get("section") or section
        topics.append({"section": section, "topic": topic, "goals": get("goals"),
                       "lesson_date": get("lesson_date"), "hours": get("hours")})
    return topics


def _xlsx(data: bytes) -> list[dict]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    for ws in wb.worksheets:
        found = _table([list(r) for r in ws.iter_rows(values_only=True)])
        if found:
            return found
    return []


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _csv(data: bytes) -> list[dict]:
    text = _decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    return _table(rows) or _lines([" ".join(r) for r in rows])


def _docx(data: bytes) -> list[dict]:
    from docx import Document

    doc = Document(io.BytesIO(data))
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells, seen = [], set()
            for c in row.cells:  # объединённые ячейки повторяются — берём один раз
                cells.append("" if id(c._tc) in seen else c.text)
                seen.add(id(c._tc))
            rows.append(cells)
        found = _table(rows)
        if found:
            return found
    return _lines([p.text for p in doc.paragraphs])


def _pdf(data: bytes) -> list[dict]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not text.strip():
        raise ParseError("В PDF нет текста (похоже на скан). Загрузите Word или Excel.")
    return _lines(text.splitlines())


def _txt(data: bytes) -> list[dict]:
    return _lines(_decode(data).splitlines())


def _lines(lines: list[str]) -> list[dict]:
    topics, section = [], ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if SECTION_RE.match(line):
            section = line
            continue
        m = LINE_RE.match(line)
        if m:
            topics.append({"section": section, "topic": m.group(2).strip(), "goals": "",
                           "lesson_date": "", "hours": ""})
    return topics
