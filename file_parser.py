import io
import re


EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
TW_ID_PATTERN = re.compile(r'\b[A-Z][12]\d{8}\b')
PHONE_PATTERN = re.compile(
    r'\b(?:\+886[-\s]?)?(?:0?9\d{2}[-\s]?\d{3}[-\s]?\d{3}|0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{3,4})\b'
)
CARD_PATTERN = re.compile(r'\b(?:\d[ -]?){13,19}\b')


def _clean_text(text: str) -> str:
    """Collapse excessive whitespace and control characters."""
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[^\S\n]+', ' ', text)
    return text.strip()


def redact_sensitive_text(text: str) -> str:
    """Mask common personal or sensitive identifiers before returning previews to the UI."""
    redacted = EMAIL_PATTERN.sub('[REDACTED_EMAIL]', text)
    redacted = TW_ID_PATTERN.sub('[REDACTED_TW_ID]', redacted)
    redacted = PHONE_PATTERN.sub('[REDACTED_PHONE]', redacted)
    redacted = CARD_PATTERN.sub('[REDACTED_NUMBER]', redacted)
    return redacted


def build_safe_preview(text: str, max_chars: int = 4000) -> str:
    preview = redact_sensitive_text(text)
    if len(preview) <= max_chars:
        return preview
    return f"{preview[:max_chars].rstrip()}\n\n...[Preview truncated for safety]..."


def parse_pdf(file_bytes: bytes) -> str:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        pages = [page.extract_text() or '' for page in pdf.pages]
    return _clean_text('\n\n'.join(pages))


def parse_docx(file_bytes: bytes) -> str:
    import docx
    doc = docx.Document(io.BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return _clean_text('\n\n'.join(paragraphs))


def parse_txt(file_bytes: bytes) -> str:
    text = file_bytes.decode('utf-8', errors='replace')
    return _clean_text(text)


def extract_text(filename: str, file_bytes: bytes) -> str:
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext == 'pdf':
        return parse_pdf(file_bytes)
    elif ext == 'docx':
        return parse_docx(file_bytes)
    elif ext == 'txt':
        return parse_txt(file_bytes)
    else:
        raise ValueError(f'Unsupported file type: .{ext}. Please upload PDF, DOCX, or TXT.')
