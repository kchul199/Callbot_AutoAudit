"""
cp2_knowledge_base/file_loader.py
업로드 파일 → 텍스트 추출 (고객사 지식 구축 F-20 파일 업로드).

지원 포맷:
  - 텍스트(stdlib): txt, md, markdown, rst, log, text, csv, tsv, json, html, htm, xml, yaml, yml
  - 문서(선택 라이브러리): pdf(pypdf), docx(python-docx), xlsx/xlsm(openpyxl)

설계:
  - 텍스트 계열은 의존성 없이 항상 지원 (utf-8 → cp949 → latin-1 폴백 디코딩)
  - 문서 계열은 라이브러리 미설치 시 명확한 안내 메시지(ValueError)로 우아하게 처리
  - 파일당 크기 상한(기본 5MB) + 추출 텍스트 길이 상한으로 과대 입력 방어
"""
from __future__ import annotations

import csv
import io
import json
from html.parser import HTMLParser
from pathlib import Path

MAX_FILE_BYTES = 5 * 1024 * 1024      # 5MB
MAX_TEXT_CHARS = 200_000              # 추출 텍스트 상한 (약 1000 청크)

# 확장자 → (카테고리, 표시용 source_type)
_EXT_TYPE: dict[str, str] = {
    ".txt": "텍스트", ".text": "텍스트", ".log": "텍스트", ".rst": "텍스트",
    ".md": "Markdown", ".markdown": "Markdown",
    ".csv": "CSV", ".tsv": "CSV",
    ".json": "JSON", ".xml": "XML",
    ".yaml": "YAML", ".yml": "YAML",
    ".html": "HTML", ".htm": "HTML",
    ".pdf": "PDF", ".docx": "Word",
    ".xlsx": "Excel", ".xlsm": "Excel",
}

SUPPORTED_EXTENSIONS: list[str] = sorted(_EXT_TYPE.keys())


def _decode(data: bytes) -> str:
    """바이트를 텍스트로 디코딩 (utf-8 → cp949(한국어) → latin-1 폴백)."""
    for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


class _TextHTMLParser(HTMLParser):
    """HTML 태그 제거 — 본문 텍스트만 수집 (script/style 제외)."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(self._parts)


def _from_html(data: bytes) -> str:
    p = _TextHTMLParser()
    p.feed(_decode(data))
    return p.text()


def _from_csv(data: bytes, delimiter: str = ",") -> str:
    """CSV/TSV → 행을 ' | '로 구분한 가독 텍스트."""
    text = _decode(data)
    rows = csv.reader(io.StringIO(text), delimiter=delimiter)
    lines = [" | ".join(cell.strip() for cell in row) for row in rows if any(c.strip() for c in row)]
    return "\n".join(lines)


def _from_json(data: bytes) -> str:
    text = _decode(data)
    try:
        obj = json.loads(text)
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return text  # 유효하지 않으면 원문 그대로


def _from_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF 파싱 라이브러리(pypdf)가 설치되어 있지 않습니다.") from exc
    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(p for p in pages if p)


def _from_docx(data: bytes) -> str:
    try:
        import docx
    except ImportError as exc:
        raise ValueError("Word 파싱 라이브러리(python-docx)가 설치되어 있지 않습니다.") from exc
    document = docx.Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _from_xlsx(data: bytes) -> str:
    try:
        import openpyxl
    except ImportError as exc:
        raise ValueError("Excel 파싱 라이브러리(openpyxl)가 설치되어 있지 않습니다.") from exc
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    for ws in wb.worksheets:
        parts.append(f"[시트: {ws.title}]")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
            if cells:
                parts.append(" | ".join(cells))
    wb.close()
    return "\n".join(parts)


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    """
    업로드 파일에서 텍스트와 추정 source_type을 추출.

    반환: (text, source_type)
    예외: ValueError (미지원 포맷 / 빈 파일 / 크기 초과 / 파싱 실패 / 빈 추출)
    """
    if not data:
        raise ValueError("빈 파일입니다.")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"파일이 너무 큽니다 (최대 {MAX_FILE_BYTES // (1024 * 1024)}MB).")

    ext = Path(filename or "").suffix.lower()
    if ext not in _EXT_TYPE:
        raise ValueError(
            f"지원하지 않는 형식입니다: '{ext or '확장자 없음'}'. "
            f"지원: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    if ext in (".pdf",):
        text = _from_pdf(data)
    elif ext == ".docx":
        text = _from_docx(data)
    elif ext in (".xlsx", ".xlsm"):
        text = _from_xlsx(data)
    elif ext in (".html", ".htm"):
        text = _from_html(data)
    elif ext == ".tsv":
        text = _from_csv(data, delimiter="\t")
    elif ext == ".csv":
        text = _from_csv(data)
    elif ext == ".json":
        text = _from_json(data)
    else:  # txt/md/log/rst/xml/yaml 등 일반 텍스트
        text = _decode(data)

    text = (text or "").strip()
    if not text:
        raise ValueError("파일에서 텍스트를 추출하지 못했습니다 (이미지/스캔 PDF일 수 있음).")
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
    return text, _EXT_TYPE[ext]
