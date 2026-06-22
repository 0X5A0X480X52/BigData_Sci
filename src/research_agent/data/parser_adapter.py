"""Parser adapter — strategy pattern for PDF text extraction.

Priority: PyMuPDF (fitz) > pypdf > raw text fallback.
Detects parse quality (printable-char ratio) and degrades on low quality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PageContent:
    page_number: int
    text: str
    sections: List[str] = field(default_factory=list)

@dataclass
class ParsedDocument:
    pages: List[PageContent]
    metadata: Dict[str, Any] = field(default_factory=dict)
    parser_name: str = ""


class ParserAdapter:
    """Parse a PDF file with automatic fallback."""

    def __init__(self, preferred: str = "pymupdf") -> None:
        self.preferred = preferred

    def parse(self, pdf_path: Path) -> Optional[ParsedDocument]:
        """Parse *pdf_path*, trying preferred parser then fallbacks."""
        if not pdf_path.exists():
            return None

        # Try preferred
        if self.preferred == "pymupdf":
            doc = self._try_pymupdf(pdf_path)
            if doc and _quality_ok(doc):
                return doc

        # Try pypdf
        doc = self._try_pypdf(pdf_path)
        if doc and _quality_ok(doc):
            return doc

        # Raw text extraction
        return self._try_raw(pdf_path)

    def _try_pymupdf(self, path: Path) -> Optional[ParsedDocument]:
        try:
            import fitz
        except ImportError:
            return None
        try:
            pdf = fitz.open(str(path))
            pages = []
            for i, page in enumerate(pdf):
                text = page.get_text()
                pages.append(PageContent(
                    page_number=i + 1, text=text,
                    sections=self._detect_sections(text),
                ))
            return ParsedDocument(
                pages=pages,
                metadata={"total_pages": len(pages)},
                parser_name="pymupdf",
            )
        except Exception:
            return None

    def _try_pypdf(self, path: Path) -> Optional[ParsedDocument]:
        try:
            from pypdf import PdfReader
        except ImportError:
            return None
        try:
            reader = PdfReader(str(path))
            pages = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                pages.append(PageContent(
                    page_number=i + 1, text=text,
                    sections=self._detect_sections(text),
                ))
            return ParsedDocument(
                pages=pages,
                metadata={"total_pages": len(pages)},
                parser_name="pypdf",
            )
        except Exception:
            return None

    def _try_raw(self, path: Path) -> Optional[ParsedDocument]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
        return ParsedDocument(
            pages=[PageContent(page_number=1, text=text,
                               sections=self._detect_sections(text))],
            metadata={"total_pages": 1},
            parser_name="raw_text",
        )

    @staticmethod
    def _detect_sections(text: str) -> List[str]:
        """Crude section detection via numbered/labeled headings."""
        sections: List[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if (stripped[0].isdigit() and "." in stripped[:6]) or stripped.isupper():
                sections.append(stripped[:120])
        return sections[:20]


def _quality_ok(doc: ParsedDocument, min_printable_ratio: float = 0.7) -> bool:
    """Check that at least *min_printable_ratio* of chars are printable."""
    total_text = "".join(p.text for p in doc.pages)
    if len(total_text) < 50:
        return False
    printable = sum(1 for c in total_text if c.isprintable() or c in "\n\r\t")
    return printable / max(len(total_text), 1) >= min_printable_ratio
