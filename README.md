# Knowledge Shredder

Knowledge Shredder is a Flask-based demo that turns internal training material into short, structured learning sprints for financial services teams. Users can upload one or more source documents, select relevant knowledge domains, and generate 2-minute micro-learning modules with an LLM.

The project is designed to show a practical end-to-end workflow:

- upload source files in `PDF`, `DOCX`, `TXT`, or `MD`
- assign one or more business domain tags
- optionally add a custom prompt for tone or focus
- preview redacted source text before generation
- generate structured learning sprints asynchronously for all uploaded documents
- review the result side by side with the source preview

## What It Demonstrates

- Multi-document upload flow in the UI
- Many-to-many mapping between documents and knowledge domains
- Background job polling for LLM generation
- Safer previews with basic masking for email, phone, Taiwan ID, and card-like numbers
- Structured JSON output validation from the LLM

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
6. Generate learning sprints for the full uploaded set and monitor job status.
7. Review one result block per document with its preview and generated modules.

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
| `POST` | `/api/generate` | Start generation jobs for one or more documents |
| `GET` | `/api/jobs/<job_id>` | Check background job status |
| `GET` | `/api/document/<doc_id>` | Fetch a saved document and its generated modules |

## Notes and Limitations

- The backend upload API accepts one file per request. The UI handles multiple files by uploading them sequentially.
- The generate API accepts `doc_ids` and starts one background job per document, which keeps the many-to-many document-domain model intact.
- The UI includes a `Custom Prompt` field, and the backend injects it into the prompt alongside the selected domains.
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
