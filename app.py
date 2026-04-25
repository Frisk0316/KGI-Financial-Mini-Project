import argparse
import os
import re
from threading import Semaphore, Thread

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

from database import (
    create_generation_batch,
    create_generation_job,
    get_all_domains,
    get_documents_by_ids,
    get_document_with_modules,
    get_generation_history,
    get_generation_job,
    init_db,
    insert_document,
    save_generated_content,
    update_generation_job,
)
from file_parser import build_safe_preview, build_safe_text, extract_text
from llm import LLMConfigurationError, LLMServiceError, generate_batch_micro_modules

load_dotenv()

DEFAULT_TRAINER_ID = 'trainer_001'
TRAINER_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{3,64}$')

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['INLINE_GENERATION_JOBS'] = False
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'txt', 'md'}

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')

with app.app_context():
    init_db()
    os.makedirs(UPLOADS_DIR, exist_ok=True)


def _read_positive_int_env(name, default):
    raw_value = os.environ.get(name, '').strip()
    if not raw_value:
        return default

    try:
        parsed = int(raw_value)
    except ValueError:
        return default

    return parsed if parsed > 0 else default


MAX_PARALLEL_GENERATION_WORKERS = _read_positive_int_env('MAX_PARALLEL_GENERATION_WORKERS', 1)
GENERATION_WORKER_SEMAPHORE = Semaphore(MAX_PARALLEL_GENERATION_WORKERS)


def _normalize_trainer_id(value):
    trainer_id = str(value or DEFAULT_TRAINER_ID).strip()
    if not TRAINER_ID_PATTERN.fullmatch(trainer_id):
        raise ValueError(
            'trainer_id must be 3-64 characters and use only letters, numbers, underscores, or hyphens.'
        )
    return trainer_id


def _resolve_trainer_id(data=None):
    if data is None:
        data = {}

    trainer_id = (
        request.headers.get('X-Trainer-Id')
        or data.get('trainer_id')
        or request.args.get('trainer_id')
        or request.form.get('trainer_id')
        or DEFAULT_TRAINER_ID
    )
    return _normalize_trainer_id(trainer_id)


def _normalize_domain_ids(domain_ids):
    if not isinstance(domain_ids, list):
        raise ValueError('domain_ids must be an array of integers.')

    normalized = []
    seen = set()
    try:
        for domain_id in domain_ids:
            parsed = int(domain_id)
            if parsed not in seen:
                normalized.append(parsed)
                seen.add(parsed)
    except (TypeError, ValueError) as exc:
        raise ValueError('domain_ids must contain valid integers.') from exc

    if not normalized:
        raise ValueError('At least one domain tag must be selected before generating.')
    if len(normalized) > 20:
        raise ValueError('A maximum of 20 domain tags can be selected per batch.')

    return normalized


def _normalize_custom_prompt(value):
    custom_prompt = str(value or '').strip()
    if len(custom_prompt) > 4000:
        raise ValueError('custom_prompt must be 4000 characters or fewer.')
    return custom_prompt


def _normalize_doc_ids(data):
    doc_ids = data.get('doc_ids')
    if doc_ids is None:
        doc_id = data.get('doc_id')
        if doc_id is None:
            raise ValueError('doc_ids is required.')
        doc_ids = [doc_id]

    if not isinstance(doc_ids, list):
        raise ValueError('doc_ids must be an array of integers.')

    normalized = []
    seen = set()
    try:
        for doc_id in doc_ids:
            parsed = int(doc_id)
            if parsed not in seen:
                normalized.append(parsed)
                seen.add(parsed)
    except (TypeError, ValueError) as exc:
        raise ValueError('doc_ids must contain valid integers.') from exc

    if not normalized:
        raise ValueError('At least one uploaded document is required before generating.')

    return normalized


def _serialize_document(doc):
    if not doc:
        return None

    return {
        'doc_id': doc['doc_id'],
        'trainer_id': doc['trainer_id'],
        'file_name': doc['file_name'],
        'upload_timestamp': doc['upload_timestamp'],
        'char_count': len(doc['raw_text']),
        'preview_text': build_safe_preview(doc['raw_text']),
        'safe_full_text': build_safe_text(doc['raw_text']),
        'domains': doc['domains'],
        'modules': doc['modules'],
    }


def _normalize_search_text(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip().lower()


def _split_into_source_paragraphs(text, max_length=700):
    normalized = str(text or '').replace('\r\n', '\n').strip()
    if not normalized:
        return ['No source text available.']

    primary_chunks = [
        chunk.strip()
        for chunk in re.split(r'\n\s*\n+', normalized)
        if chunk.strip()
    ]

    paragraphs = []
    chunks = primary_chunks or [normalized]
    for chunk in chunks:
        if len(chunk) <= max_length:
            paragraphs.append(chunk)
            continue

        sentences = [
            sentence.strip()
            for sentence in re.split(r'(?<=[.!?。！？；;])\s+|\n+', chunk)
            if sentence.strip()
        ]
        if not sentences:
            paragraphs.append(chunk)
            continue

        buffer = ''
        for sentence in sentences:
            next_value = f'{buffer} {sentence}'.strip() if buffer else sentence
            if len(next_value) > max_length and buffer:
                paragraphs.append(buffer.strip())
                buffer = sentence
            else:
                buffer = next_value

        if buffer.strip():
            paragraphs.append(buffer.strip())

    return paragraphs or [normalized]


def _build_match_phrases(module):
    raw_segments = []
    for field_name in ('title', 'content', 'key_takeaway'):
        value = str(module.get(field_name, '') or '').strip()
        if not value:
            continue
        raw_segments.append(value)
        raw_segments.extend(re.split(r'[\n\r]+', value))
        raw_segments.extend(re.split(r'[。！？!?；;:：,，()（）]', value))

    seen = set()
    phrases = []
    for segment in raw_segments:
        cleaned = segment.strip(' -•\t')
        normalized = _normalize_search_text(cleaned)
        if not normalized or normalized in seen:
            continue

        has_cjk = bool(re.search(r'[\u3400-\u9fff]', normalized))
        if has_cjk and len(normalized) < 2:
            continue
        if not has_cjk and len(normalized) < 4:
            continue

        seen.add(normalized)
        phrases.append((normalized, min(max(len(normalized), 3), 30)))

    return sorted(phrases, key=lambda item: item[1], reverse=True)[:24]


def _build_match_tokens(module):
    raw_text = ' '.join(
        str(module.get(field_name, '') or '')
        for field_name in ('title', 'content', 'key_takeaway')
    )
    raw_tokens = re.split(r'[^0-9A-Za-z\u3400-\u9fff]+', raw_text)
    tokens = []
    seen = set()

    for token in raw_tokens:
        normalized = _normalize_search_text(token)
        if not normalized or normalized in seen:
            continue

        has_cjk = bool(re.search(r'[\u3400-\u9fff]', normalized))
        if has_cjk and len(normalized) < 2:
            continue
        if not has_cjk and len(normalized) < 4:
            continue

        seen.add(normalized)
        tokens.append(normalized)

    return sorted(tokens, key=len, reverse=True)[:32]


def _split_into_source_paragraphs_safe(text, max_length=700):
    normalized = str(text or '').replace('\r\n', '\n').strip()
    if not normalized:
        return ['No source text available.']

    primary_chunks = [
        chunk.strip()
        for chunk in re.split(r'\n\s*\n+', normalized)
        if chunk.strip()
    ]

    paragraphs = []
    for chunk in primary_chunks or [normalized]:
        if len(chunk) <= max_length:
            paragraphs.append(chunk)
            continue

        sentences = [
            sentence.strip()
            for sentence in re.split(r'(?<=[.!?\u3002\uff01\uff1f;\uff1b])\s+|\n+', chunk)
            if sentence.strip()
        ]
        if not sentences:
            paragraphs.append(chunk)
            continue

        buffer = ''
        for sentence in sentences:
            next_value = f'{buffer} {sentence}'.strip() if buffer else sentence
            if len(next_value) > max_length and buffer:
                paragraphs.append(buffer.strip())
                buffer = sentence
            else:
                buffer = next_value

        if buffer.strip():
            paragraphs.append(buffer.strip())

    return paragraphs or [normalized]


def _build_match_phrases_safe(module):
    raw_segments = []
    for field_name in ('title', 'content', 'key_takeaway'):
        value = str(module.get(field_name, '') or '').strip()
        if not value:
            continue
        raw_segments.append(value)
        raw_segments.extend(re.split(r'[\n\r]+', value))
        raw_segments.extend(re.split(r'[\u3002\uff01\uff1f!?;\uff1b:\uff1a,\uff0c()\uff08\uff09]', value))

    phrases = []
    seen = set()
    for segment in raw_segments:
        normalized = _normalize_search_text(segment.strip(' -\t'))
        if not normalized or normalized in seen:
            continue

        has_cjk = bool(re.search(r'[\u3400-\u9fff]', normalized))
        if has_cjk and len(normalized) < 2:
            continue
        if not has_cjk and len(normalized) < 4:
            continue

        seen.add(normalized)
        phrases.append((normalized, min(max(len(normalized), 3), 30)))

    return sorted(phrases, key=lambda item: item[1], reverse=True)[:24]


def _score_source_paragraph(paragraph, phrases, tokens):
    normalized_paragraph = _normalize_search_text(paragraph)
    if not normalized_paragraph:
        return 0, []

    score = 0
    matched_terms = []
    seen_terms = set()

    for phrase, weight in phrases:
        if phrase in normalized_paragraph:
            score += weight
            if len(matched_terms) < 4 and phrase not in seen_terms:
                matched_terms.append(phrase)
                seen_terms.add(phrase)

    token_hits = 0
    for token in tokens:
        if token in normalized_paragraph:
            token_hits += 1
            if len(matched_terms) < 6 and token not in seen_terms:
                matched_terms.append(token)
                seen_terms.add(token)

    return score + token_hits * 2, matched_terms


def _build_source_evidence(module, docs_by_id):
    source_doc_ids = []
    seen = set()
    for raw_doc_id in module.get('source_doc_ids', []):
        try:
            doc_id = int(raw_doc_id)
        except (TypeError, ValueError):
            continue
        if doc_id in seen:
            continue
        source_doc_ids.append(doc_id)
        seen.add(doc_id)

    phrases = _build_match_phrases_safe(module)
    tokens = _build_match_tokens(module)
    evidence = []

    for doc_id in source_doc_ids:
        doc = docs_by_id.get(doc_id)
        if not doc:
            continue

        paragraphs = _split_into_source_paragraphs_safe(build_safe_text(doc['raw_text']))
        best_index = 0
        best_score = -1
        best_terms = []

        for index, paragraph in enumerate(paragraphs):
            score, matched_terms = _score_source_paragraph(paragraph, phrases, tokens)
            if score > best_score:
                best_index = index
                best_score = score
                best_terms = matched_terms

        matched_paragraph = paragraphs[best_index] if paragraphs else ''
        evidence.append({
            'doc_id': doc_id,
            'file_name': doc['file_name'],
            'matched_paragraph_index': best_index,
            'matched_text': matched_paragraph,
            'matched_excerpt': build_safe_preview(matched_paragraph),
            'matched_terms': best_terms[:5],
            'match_score': max(best_score, 0),
        })

    evidence.sort(key=lambda item: (-item['match_score'], source_doc_ids.index(item['doc_id'])))
    return evidence


def _build_generation_result(batch_id, docs, llm_result):
    docs_by_id = {int(doc['doc_id']): doc for doc in docs}
    documents_payload = [
        {
            'doc_id': doc['doc_id'],
            'file_name': doc['file_name'],
            'preview_text': build_safe_preview(doc['raw_text']),
            'safe_full_text': build_safe_text(doc['raw_text']),
            'char_count': len(doc['raw_text']),
        }
        for doc in docs
    ]

    modules_payload = []
    for module in llm_result.get('modules', []):
        source_doc_ids = [int(doc_id) for doc_id in module.get('source_doc_ids', [])]
        source_evidence = _build_source_evidence(module, docs_by_id)
        modules_payload.append({
            'sequence_order': module.get('sequence_order'),
            'title': module.get('title', ''),
            'content': module.get('content', ''),
            'key_takeaway': module.get('key_takeaway', ''),
            'reading_time_minutes': module.get('reading_time_minutes', 2.0),
            'source_doc_ids': source_doc_ids,
            'source_files': [
                docs_by_id[doc_id]['file_name']
                for doc_id in source_doc_ids
                if doc_id in docs_by_id
            ],
            'primary_source_doc_id': (
                source_evidence[0]['doc_id']
                if source_evidence
                else (source_doc_ids[0] if source_doc_ids else None)
            ),
            'source_evidence': source_evidence,
        })

    return {
        'batch_id': batch_id,
        'doc_ids': [doc['doc_id'] for doc in docs],
        'documents': documents_payload,
        'document_summary': llm_result.get('document_summary', ''),
        'domains': llm_result.get('domains', []),
        'modules': modules_payload,
    }


def _run_generation_job(job_id, batch_id, docs, domain_ids, domain_names, custom_prompt):
    try:
        with GENERATION_WORKER_SEMAPHORE:
            update_generation_job(job_id, 'running')
            llm_result = generate_batch_micro_modules(docs, domain_names, custom_prompt=custom_prompt)

        save_generated_content(
            batch_id,
            [doc['doc_id'] for doc in docs],
            domain_ids,
            llm_result.get('document_summary', ''),
            llm_result.get('modules', []),
        )
        update_generation_job(job_id, 'completed', result_payload=_build_generation_result(batch_id, docs, llm_result))
    except LLMConfigurationError as exc:
        update_generation_job(job_id, 'failed', error_message=str(exc))
    except LLMServiceError as exc:
        update_generation_job(job_id, 'failed', error_message=str(exc))
    except ValueError as exc:
        update_generation_job(job_id, 'failed', error_message=f'The AI returned invalid module data: {exc}')
    except Exception:
        update_generation_job(
            job_id,
            'failed',
            error_message='An unexpected error occurred while generating the micro-modules.',
        )


def _start_generation_job(job_id, batch_id, docs, domain_ids, domain_names, custom_prompt):
    if app.config.get('INLINE_GENERATION_JOBS'):
        _run_generation_job(job_id, batch_id, docs, domain_ids, domain_names, custom_prompt)
        return

    worker = Thread(
        target=_run_generation_job,
        args=(job_id, batch_id, docs, domain_ids, domain_names, custom_prompt),
        daemon=True,
    )
    worker.start()


@app.errorhandler(413)
def too_large(_error):
    return jsonify({'error': 'File exceeds the 16 MB upload limit.'}), 413


@app.errorhandler(404)
def not_found(_error):
    return jsonify({'error': 'Route not found.'}), 404


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/domains')
def api_domains():
    return jsonify(get_all_domains())


@app.route('/api/upload', methods=['POST'])
def api_upload():
    try:
        trainer_id = _resolve_trainer_id()
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided.'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected.'}), 400

    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({'error': f'File type .{ext} is not supported. Please upload PDF, DOCX, TXT, or MD.'}), 415

    file_bytes = file.read()

    try:
        raw_text = extract_text(secure_filename(file.filename), file_bytes)
    except Exception as exc:
        return jsonify({'error': f'Failed to parse file: {exc}'}), 422

    if len(raw_text.strip()) < 50:
        return jsonify({'error': 'Document appears to be empty or contains no extractable text (e.g. image-only PDF).'}), 422

    doc_id = insert_document(
        trainer_id=trainer_id,
        file_name=secure_filename(file.filename),
        raw_text=raw_text,
    )

    return jsonify({
        'doc_id': doc_id,
        'trainer_id': trainer_id,
        'file_name': file.filename,
        'preview_text': build_safe_preview(raw_text),
        'char_count': len(raw_text),
    }), 201


@app.route('/api/generate', methods=['POST'])
def api_generate():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Request body must be JSON.'}), 400

    try:
        trainer_id = _resolve_trainer_id(data=data)
        doc_ids = _normalize_doc_ids(data)
        domain_ids = _normalize_domain_ids(data.get('domain_ids', []))
        custom_prompt = _normalize_custom_prompt(data.get('custom_prompt'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    all_domains = {domain['domain_id']: domain['domain_name'] for domain in get_all_domains()}
    domain_names = []
    for domain_id in domain_ids:
        if domain_id not in all_domains:
            return jsonify({'error': f'Unknown domain_id: {domain_id}'}), 400
        domain_names.append(all_domains[domain_id])

    docs = get_documents_by_ids(doc_ids, trainer_id=trainer_id)
    if len(docs) != len(doc_ids):
        found_doc_ids = {doc['doc_id'] for doc in docs}
        missing_doc_ids = [doc_id for doc_id in doc_ids if doc_id not in found_doc_ids]
        return jsonify({'error': f'Documents not found for this trainer: {missing_doc_ids}'}), 404

    batch_id = create_generation_batch(doc_ids, trainer_id, domain_ids, domain_names, custom_prompt=custom_prompt)
    job_id = create_generation_job(batch_id, docs[0]['doc_id'], trainer_id, domain_ids, domain_names, custom_prompt=custom_prompt)
    _start_generation_job(job_id, batch_id, docs, domain_ids, domain_names, custom_prompt)
    job = get_generation_job(job_id, trainer_id=trainer_id)

    return jsonify({
        'job_id': job_id,
        'batch_id': batch_id,
        'doc_ids': doc_ids,
        'custom_prompt': custom_prompt,
        'status': job['status'],
        'jobs': [{
            'job_id': job_id,
            'batch_id': batch_id,
            'status': job['status'],
        }],
        'total_jobs': 1,
    }), 202


@app.route('/api/jobs/<int:job_id>')
def api_job(job_id):
    try:
        trainer_id = _resolve_trainer_id()
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    job = get_generation_job(job_id, trainer_id=trainer_id)
    if not job:
        return jsonify({'error': 'Job not found for this trainer.'}), 404
    return jsonify(job)


@app.route('/api/history')
def api_history():
    try:
        trainer_id = _resolve_trainer_id()
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    try:
        limit = int(request.args.get('limit', 10))
    except ValueError:
        return jsonify({'error': 'limit must be an integer.'}), 400

    if limit <= 0:
        return jsonify({'error': 'limit must be greater than 0.'}), 400

    history = get_generation_history(trainer_id=trainer_id, limit=limit)
    return jsonify({
        'trainer_id': trainer_id,
        'count': len(history),
        'history': history,
    })


@app.route('/api/document/<int:doc_id>')
def api_document(doc_id):
    try:
        trainer_id = _resolve_trainer_id()
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    doc = get_document_with_modules(doc_id, trainer_id=trainer_id)
    if not doc:
        return jsonify({'error': 'Document not found for this trainer.'}), 404
    return jsonify(_serialize_document(doc))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run the Knowledge Shredder development server.')
    parser.add_argument('--port', type=int, default=5000, help='Port to bind the Flask server to.')
    parser.add_argument('--debug', action='store_true', help='Enable Flask debug mode.')
    args = parser.parse_args()
    app.run(debug=args.debug, port=args.port)
