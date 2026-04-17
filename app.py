import json
import os

import anthropic
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.utils import secure_filename

from database import (
    get_all_domains,
    get_document_with_modules,
    init_db,
    insert_document,
    insert_micro_modules,
    tag_document,
)
from file_parser import extract_text
from llm import generate_micro_modules

load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB
ALLOWED_EXTENSIONS = {'pdf', 'docx', 'doc', 'txt'}

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')

with app.app_context():
    init_db()
    os.makedirs(UPLOADS_DIR, exist_ok=True)


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
        trainer_id='trainer_001',
        file_name=secure_filename(file.filename),
        raw_text=raw_text,
    )

    return jsonify({
        'doc_id': doc_id,
        'file_name': file.filename,
        'raw_text': raw_text,
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

    doc_id = data.get('doc_id')
    domain_ids = data.get('domain_ids', [])

    if not doc_id:
        return jsonify({'error': 'doc_id is required.'}), 400
    if not domain_ids:
        return jsonify({'error': 'At least one domain tag must be selected before generating.'}), 400

    # Resolve domain names
    all_domains = {d['domain_id']: d['domain_name'] for d in get_all_domains()}
    domain_names = []
    for did in domain_ids:
        if did not in all_domains:
            return jsonify({'error': f'Unknown domain_id: {did}'}), 400
        domain_names.append(all_domains[did])

    # Fetch document
    doc = get_document_with_modules(doc_id)
    if not doc:
        return jsonify({'error': 'Document not found.'}), 404

    try:
        llm_result = generate_micro_modules(doc['raw_text'], domain_names)
    except json.JSONDecodeError:
        return jsonify({'error': 'The AI returned an unexpected response. Please try again.'}), 502
    except anthropic.APIError as exc:
        return jsonify({'error': f'Claude API error: {exc}'}), 502
    except RuntimeError as exc:
        return jsonify({'error': str(exc)}), 500

    tag_document(doc_id, domain_ids)
    insert_micro_modules(doc_id, llm_result.get('modules', []))

    return jsonify({
        'doc_id': doc_id,
        'document_summary': llm_result.get('document_summary', ''),
        'domains': domain_names,
        'modules': llm_result.get('modules', []),
    }), 200


# ---------------------------------------------------------------------------
# Fetch saved document + modules
# ---------------------------------------------------------------------------

@app.route('/api/document/<int:doc_id>')
def api_document(doc_id):
    doc = get_document_with_modules(doc_id)
    if not doc:
        return jsonify({'error': 'Document not found.'}), 404
    return jsonify(doc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app.run(debug=True, port=5000)
