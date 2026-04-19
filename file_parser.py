import io
import re


def _clean_text(text: str) -> str:
    """Collapse excessive whitespace and control characters."""
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[^\S\n]+', ' ', text)
    return text.strip()


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
