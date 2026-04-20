import argparse
import os
import re
from threading import Thread

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

from database import (
    create_generation_job,
    get_all_domains,
    get_document_with_modules,
    get_generation_job,
    init_db,
    insert_document,
    save_generated_content,
    update_generation_job,
)
from file_parser import build_safe_preview, extract_text
from llm import LLMConfigurationError, LLMServiceError, generate_micro_modules

load_dotenv()

DEFAULT_TRAINER_ID = 'trainer_001'
TRAINER_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{3,64}$')

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB
app.config['INLINE_GENERATION_JOBS'] = False
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'txt'}

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')

with app.app_context():
    init_db()
    os.makedirs(UPLOADS_DIR, exist_ok=True)


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
        raise ValueError('A maximum of 20 domain tags can be selected per document.')

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
        'domains': doc['domains'],
        'modules': doc['modules'],
    }


def _build_generation_result(doc_id, llm_result):
    return {
        'doc_id': doc_id,
        'document_summary': llm_result.get('document_summary', ''),
        'domains': llm_result.get('domains', []),
        'modules': llm_result.get('modules', []),
    }


def _run_generation_job(job_id, doc_id, domain_ids, domain_names, raw_text):
    update_generation_job(job_id, 'running')
    try:
        llm_result = generate_micro_modules(raw_text, domain_names)
        save_generated_content(doc_id, domain_ids, llm_result['modules'])
        update_generation_job(job_id, 'completed', result_payload=_build_generation_result(doc_id, llm_result))
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


def _start_generation_job(job_id, doc_id, domain_ids, domain_names, raw_text):
    if app.config.get('INLINE_GENERATION_JOBS'):
        _run_generation_job(job_id, doc_id, domain_ids, domain_names, raw_text)
        return

    worker = Thread(
        target=_run_generation_job,
        args=(job_id, doc_id, domain_ids, domain_names, raw_text),
        daemon=True,
    )
    worker.start()


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(413)
def too_large(_e):
    return jsonify({'error': 'File exceeds the 16 MB upload limit.'}), 413


@app.errorhandler(404)
def not_found(_e):
    return jsonify({'error': 'Route not found.'}), 404


# ---------------------------------------------------------------------------
# HTML shell
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------

@app.route('/api/domains')
def api_domains():
    return jsonify(get_all_domains())


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

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
        return jsonify({'error': f'File type .{ext} is not supported. Please upload PDF, DOCX, or TXT.'}), 415

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


# ---------------------------------------------------------------------------
# Generate micro-modules
# ---------------------------------------------------------------------------

@app.route('/api/generate', methods=['POST'])
def api_generate():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Request body must be JSON.'}), 400

    try:
        trainer_id = _resolve_trainer_id(data=data)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    doc_id = data.get('doc_id')
    if not doc_id:
        return jsonify({'error': 'doc_id is required.'}), 400

    try:
        domain_ids = _normalize_domain_ids(data.get('domain_ids', []))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    all_domains = {domain['domain_id']: domain['domain_name'] for domain in get_all_domains()}
    domain_names = []
    for domain_id in domain_ids:
        if domain_id not in all_domains:
            return jsonify({'error': f'Unknown domain_id: {domain_id}'}), 400
        domain_names.append(all_domains[domain_id])

    doc = get_document_with_modules(doc_id, trainer_id=trainer_id)
    if not doc:
        return jsonify({'error': 'Document not found for this trainer.'}), 404

    job_id = create_generation_job(doc_id, trainer_id, domain_ids, domain_names)
    _start_generation_job(job_id, doc_id, domain_ids, domain_names, doc['raw_text'])

    return jsonify({
        'job_id': job_id,
        'doc_id': doc_id,
        'status': get_generation_job(job_id, trainer_id=trainer_id)['status'],
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


# ---------------------------------------------------------------------------
# Fetch saved document + modules
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run the Knowledge Shredder development server.')
    parser.add_argument('--port', type=int, default=5000, help='Port to bind the Flask server to.')
    parser.add_argument('--debug', action='store_true', help='Enable Flask debug mode.')
    args = parser.parse_args()
    app.run(debug=args.debug, port=args.port)
