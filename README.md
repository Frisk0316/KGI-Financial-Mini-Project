# Knowledge Shredder

Knowledge Shredder is a Flask-based demo that turns internal training material into short, structured learning sprints for financial services teams. Users can upload one or more source documents, select relevant knowledge domains, and generate 2-minute micro-learning modules with an LLM.

The project is designed to show a practical end-to-end workflow:

- upload source files in `PDF`, `DOCX`, `TXT`, or `MD`
- assign one or more business domain tags
- optionally add a custom prompt for tone or focus
- preview redacted source text before generation
- summarize the full uploaded batch first, then generate integrated learning sprints asynchronously
- review the integrated result side by side with the source previews

## What It Demonstrates

- Multi-document upload flow in the UI
- Many-to-many mapping between documents and knowledge domains
- Two-stage LLM generation: full-batch summary, then integrated module generation
- Background job polling for LLM generation
- Safer previews with basic masking for email, phone, Taiwan ID, and card-like numbers
- Structured JSON output validation from the LLM
- Traceability from generated modules back to one or more source documents

## Domain Taxonomy

The demo ships with seven example financial knowledge domains:

| Domain | Description |
| --- | --- |
| `CRM` | Client relationship management, service follow-up, and communication quality. |
| `Compliance` | Financial compliance, AML/KYC checks, disclosures, and operating controls. |
| `InvestmentLinked` | Investment-linked products, funds, asset allocation, and risk-return concepts. |
| `LifeInsurance` | Life insurance products, policy structure, beneficiaries, and coverage discussions. |
| `Other` | Use when the source material does not fit the predefined domain tags. |
| `TaxRegulations` | Tax rules, filing requirements, withholding, and tax planning considerations. |
| `WealthManagement` | Wealth planning, succession, trust topics, and broader asset management decisions. |

## Product Flow

1. Enter a `trainer_id` to scope uploads and generation jobs.
2. Upload one or more source documents.
3. Select one or more domain tags.
4. Optionally add custom prompt instructions.
5. Leave all uploaded files in the list, or remove any file you want to exclude.
6. Start one batch generation job for the selected document set.
7. The backend first summarizes all selected documents together.
8. The backend then generates integrated modules that can reference one or more source documents.
9. Review the final batch summary, source previews, and generated modules together.

## Batch Architecture

The generation pipeline now uses a two-stage batch flow:

1. `Stage 1`: send all selected document text to the LLM and request a structured JSON summary for the full batch plus one summary per source file.
2. `Stage 2`: send the stage-1 JSON summary back to the LLM and request integrated micro-modules with `source_doc_ids`.

This lets the model synthesize across related files instead of treating each document in isolation.

The database schema now reflects that batch-first design:

- `GenerationBatches` stores one integrated generation request.
- `Batch_Document_Map` links a batch to all selected source documents.
- `MicroModules` stores the generated output at the batch level.
- `Module_SourceDocument_Map` preserves many-to-many traceability from each module back to one or more documents.
- `Document_Domain_Map` still records the many-to-many taxonomy tags applied to the selected documents.

## Tech Stack

- Backend: `Flask`
- Database: `SQLite`
- Parsing: `pdfplumber`, `python-docx`
- LLM: OpenAI Responses API
- Frontend: HTML, Bootstrap 5, Vanilla JavaScript

## Project Structure

```text
app.py                 Flask routes and API handlers
database.py            SQLite schema and persistence helpers
file_parser.py         Text extraction and preview redaction
llm.py                 Prompting, structured output, and response validation
templates/index.html   Main UI
static/js/app.js       Frontend upload and generation flow
static/css/style.css   UI styling
tests/test_app.py      Automated tests
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

Create a local `.env` file:

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-5.4-mini
MOCK_LLM=false
OPENAI_MAX_RETRIES=4
MAX_PARALLEL_GENERATION_WORKERS=1
```

You can also copy `.env.example` and fill in your key.

If you want to test without calling the OpenAI API, set `MOCK_LLM=true`. The app will generate deterministic local demo output that still follows the JSON schema and selected domain tags.

When you generate many documents in one batch, the app now retries transient OpenAI failures such as `429 rate_limit_exceeded` and, by default, processes one LLM request at a time to reduce burst traffic. You can tune this with `OPENAI_MAX_RETRIES` and `MAX_PARALLEL_GENERATION_WORKERS`.

### 3. Start the app

```bash
python app.py
```

Optional:

```bash
python app.py --port 8080 --debug
```

Open `http://127.0.0.1:5000` in your browser.

## API Overview

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Render the web interface |
| `GET` | `/api/domains` | Return available domain tags |
| `POST` | `/api/upload` | Upload and parse one source document |
| `POST` | `/api/generate` | Start one integrated batch-generation job for one or more documents |
| `GET` | `/api/jobs/<job_id>` | Check background job status |
| `GET` | `/api/document/<doc_id>` | Fetch a saved document and its generated modules |

## Notes and Limitations

- The backend upload API accepts one file per request. The UI handles multiple files by uploading them sequentially.
- The generate API accepts `doc_ids` and starts one background job per selected document batch.
- The UI includes a `Custom Prompt` field, and the backend injects it into both LLM prompt stages alongside the selected domains.
- Domain tags are user-selected guidance. The `Other` tag is available when the document does not fit the predefined taxonomy.
- Safe preview masking is intentionally lightweight and should not be treated as a full DLP solution.
- `HARDENING_PLAN.md` is considered an internal planning document and is excluded from normal Git tracking.

## Running Tests

```bash
pytest -q
```

If `pytest` is unavailable, you can also use:

```bash
python -m unittest discover -s tests -q
```
