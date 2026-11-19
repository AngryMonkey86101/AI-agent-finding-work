"""Извлечение текста из резюме в форматах PDF, DOCX и DOC."""
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc"}


class ResumeError(ValueError):
    """Файл нельзя прочитать как резюме."""


def extract_text(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ResumeError("Поддерживаются только файлы PDF, DOC и DOCX.")
    if ext == ".pdf":
        text = _from_pdf(data)
    elif ext == ".docx":
        text = _from_docx(data)
    else:
        text = _from_doc(data)
    text = _normalize(text)
    if not text:
        raise ResumeError(
            "Не удалось извлечь текст из файла. Если это скан, "
            "сохраните резюме как текстовый PDF или DOCX."
        )
    return text


def _from_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # noqa: BLE001 - pypdf бросает разные исключения
        raise ResumeError(f"Не удалось прочитать PDF: {exc}") from exc


def _from_docx(data: bytes) -> str:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ResumeError(f"Не удалось прочитать DOCX: {exc}") from exc
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def _from_doc(data: bytes) -> str:
    """Старый формат Word. Пробуем antiword, catdoc, затем LibreOffice."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "resume.doc"
        src.write_bytes(data)
        for tool in ("antiword", "catdoc"):
            if shutil.which(tool):
                result = subprocess.run(
                    [tool, str(src)], capture_output=True, timeout=60
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.decode("utf-8", errors="replace")
        office = shutil.which("soffice") or shutil.which("libreoffice")
        if office:
            subprocess.run(
                [office, "--headless", "--convert-to", "txt:Text", "--outdir", tmp, str(src)],
                capture_output=True,
                timeout=120,
            )
            out = Path(tmp) / "resume.txt"
            if out.exists():
                return out.read_text(encoding="utf-8", errors="replace")
    raise ResumeError(
        "Не удалось прочитать DOC. Сохраните резюме в DOCX или PDF и загрузите снова."
    )


def _normalize(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\r", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()
