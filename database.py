import json
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), 'knowledge_shredder.db')

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS KnowledgeDomains (
    domain_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_name TEXT NOT NULL UNIQUE,
    description TEXT
);

CREATE TABLE IF NOT EXISTS SourceDocuments (
    doc_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trainer_id       TEXT NOT NULL DEFAULT 'trainer_001',
    file_name        TEXT NOT NULL,
    raw_text         TEXT NOT NULL,
    upload_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS Document_Domain_Map (
    map_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id    INTEGER NOT NULL REFERENCES SourceDocuments(doc_id) ON DELETE CASCADE,
    domain_id INTEGER NOT NULL REFERENCES KnowledgeDomains(domain_id) ON DELETE CASCADE,
    UNIQUE(doc_id, domain_id)
);

CREATE TABLE IF NOT EXISTS GenerationBatches (
    batch_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    trainer_id              TEXT NOT NULL,
    requested_domains_json  TEXT NOT NULL,
    requested_custom_prompt TEXT NOT NULL DEFAULT '',
    combined_summary        TEXT,
    created_timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_timestamp     DATETIME
);

CREATE TABLE IF NOT EXISTS Batch_Document_Map (
    batch_document_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id          INTEGER NOT NULL REFERENCES GenerationBatches(batch_id) ON DELETE CASCADE,
    doc_id            INTEGER NOT NULL REFERENCES SourceDocuments(doc_id) ON DELETE CASCADE,
    UNIQUE(batch_id, doc_id)
);

CREATE TABLE IF NOT EXISTS MicroModules (
    module_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id               INTEGER NOT NULL REFERENCES SourceDocuments(doc_id) ON DELETE CASCADE,
    batch_id             INTEGER REFERENCES GenerationBatches(batch_id) ON DELETE CASCADE,
    module_title         TEXT,
    module_content       TEXT NOT NULL,
    key_takeaway         TEXT,
    reading_time_minutes REAL DEFAULT 2.0,
    sequence_order       INTEGER
);

CREATE TABLE IF NOT EXISTS Module_SourceDocument_Map (
    map_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id INTEGER NOT NULL REFERENCES MicroModules(module_id) ON DELETE CASCADE,
    doc_id    INTEGER NOT NULL REFERENCES SourceDocuments(doc_id) ON DELETE CASCADE,
    UNIQUE(module_id, doc_id)
);

CREATE TABLE IF NOT EXISTS GenerationJobs (
    job_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id                 INTEGER NOT NULL REFERENCES SourceDocuments(doc_id) ON DELETE CASCADE,
    batch_id               INTEGER REFERENCES GenerationBatches(batch_id) ON DELETE CASCADE,
    trainer_id             TEXT NOT NULL,
    status                 TEXT NOT NULL CHECK(status IN ('queued', 'running', 'completed', 'failed')),
    requested_domains_json TEXT NOT NULL,
    result_json            TEXT,
    error_message          TEXT,
    created_timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_timestamp    DATETIME
);
"""

SEED_DOMAINS = [
    ('LifeInsurance', 'Life insurance products, policy structure, beneficiaries, and coverage discussions.'),
    ('InvestmentLinked', 'Investment-linked products, funds, asset allocation, and risk-return concepts.'),
    ('CRM', 'Client relationship management, service follow-up, and communication quality.'),
    ('Compliance', 'Financial compliance, AML/KYC checks, disclosures, and operating controls.'),
    ('WealthManagement', 'Wealth planning, succession, trust topics, and broader asset management decisions.'),
    ('TaxRegulations', 'Tax rules, filing requirements, withholding, and tax planning considerations.'),
    ('Other', 'Use when the material does not fit the predefined financial knowledge domains.'),
]


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def _column_exists(conn, table_name, column_name):
    columns = conn.execute(f'PRAGMA table_info({table_name})').fetchall()
    return any(column['name'] == column_name for column in columns)


def init_db():
    with get_db_connection() as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executemany(
            'INSERT OR IGNORE INTO KnowledgeDomains (domain_name, description) VALUES (?, ?)',
            SEED_DOMAINS,
        )
        conn.executemany(
            'UPDATE KnowledgeDomains SET description = ? WHERE domain_name = ?',
            [(description, domain_name) for domain_name, description in SEED_DOMAINS],
        )

        if not _column_exists(conn, 'MicroModules', 'key_takeaway'):
            conn.execute('ALTER TABLE MicroModules ADD COLUMN key_takeaway TEXT')
        if not _column_exists(conn, 'MicroModules', 'batch_id'):
            conn.execute('ALTER TABLE MicroModules ADD COLUMN batch_id INTEGER REFERENCES GenerationBatches(batch_id)')
        if not _column_exists(conn, 'GenerationJobs', 'batch_id'):
            conn.execute('ALTER TABLE GenerationJobs ADD COLUMN batch_id INTEGER REFERENCES GenerationBatches(batch_id)')

        conn.commit()


def get_all_domains():
    with get_db_connection() as conn:
        rows = conn.execute(
            'SELECT domain_id, domain_name, description FROM KnowledgeDomains ORDER BY domain_name'
        ).fetchall()
    return [dict(row) for row in rows]


def insert_document(trainer_id, file_name, raw_text):
    with get_db_connection() as conn:
        cur = conn.execute(
            'INSERT INTO SourceDocuments (trainer_id, file_name, raw_text) VALUES (?, ?, ?)',
            (trainer_id, file_name, raw_text),
        )
        conn.commit()
        return cur.lastrowid


def get_documents_by_ids(doc_ids, trainer_id=None):
    normalized_doc_ids = [int(doc_id) for doc_id in doc_ids]
    if not normalized_doc_ids:
        return []

    placeholders = ', '.join('?' for _ in normalized_doc_ids)
    query = f"""
        SELECT doc_id, trainer_id, file_name, raw_text, upload_timestamp
          FROM SourceDocuments
         WHERE doc_id IN ({placeholders})
    """
    params = list(normalized_doc_ids)
    if trainer_id is not None:
        query += ' AND trainer_id = ?'
        params.append(trainer_id)

    with get_db_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    rows_by_id = {row['doc_id']: dict(row) for row in rows}
    return [rows_by_id[doc_id] for doc_id in normalized_doc_ids if doc_id in rows_by_id]


def create_generation_batch(doc_ids, trainer_id, domain_ids, domain_names, custom_prompt=''):
    payload = json.dumps({'domain_ids': domain_ids, 'domains': domain_names}, ensure_ascii=False)
    with get_db_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO GenerationBatches (trainer_id, requested_domains_json, requested_custom_prompt)
            VALUES (?, ?, ?)
            """,
            (trainer_id, payload, custom_prompt),
        )
        batch_id = cur.lastrowid
        conn.executemany(
            'INSERT INTO Batch_Document_Map (batch_id, doc_id) VALUES (?, ?)',
            [(batch_id, int(doc_id)) for doc_id in doc_ids],
        )
        conn.commit()
        return batch_id


def create_generation_job(batch_id, primary_doc_id, trainer_id, domain_ids, domain_names, custom_prompt=''):
    payload = json.dumps(
        {'domain_ids': domain_ids, 'domains': domain_names, 'custom_prompt': custom_prompt},
        ensure_ascii=False,
    )
    with get_db_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO GenerationJobs (doc_id, batch_id, trainer_id, status, requested_domains_json)
            VALUES (?, ?, ?, 'queued', ?)
            """,
            (primary_doc_id, batch_id, trainer_id, payload),
        )
        conn.commit()
        return cur.lastrowid


def update_generation_job(job_id, status, result_payload=None, error_message=None):
    serialized_result = None
    if result_payload is not None:
        serialized_result = json.dumps(result_payload, ensure_ascii=False)

    completed_fragment = 'completed_timestamp = CURRENT_TIMESTAMP,' if status in {'completed', 'failed'} else ''
    with get_db_connection() as conn:
        conn.execute(
            f"""
            UPDATE GenerationJobs
               SET status = ?,
                   result_json = COALESCE(?, result_json),
                   error_message = ?,
                   {completed_fragment}
                   updated_timestamp = CURRENT_TIMESTAMP
             WHERE job_id = ?
            """,
            (status, serialized_result, error_message, job_id),
        )
        conn.commit()


def update_generation_batch(batch_id, combined_summary=None, completed=False):
    summary_fragment = 'combined_summary = COALESCE(?, combined_summary),'
    completed_fragment = 'completed_timestamp = CURRENT_TIMESTAMP,' if completed else ''
    with get_db_connection() as conn:
        conn.execute(
            f"""
            UPDATE GenerationBatches
               SET {summary_fragment}
                   {completed_fragment}
                   updated_timestamp = CURRENT_TIMESTAMP
             WHERE batch_id = ?
            """,
            (combined_summary, batch_id),
        )
        conn.commit()


def replace_document_domains(conn, doc_ids, domain_ids):
    for doc_id in doc_ids:
        conn.execute('DELETE FROM Document_Domain_Map WHERE doc_id = ?', (doc_id,))
        if domain_ids:
            conn.executemany(
                'INSERT INTO Document_Domain_Map (doc_id, domain_id) VALUES (?, ?)',
                [(doc_id, domain_id) for domain_id in domain_ids],
            )


def replace_batch_modules(conn, batch_id, fallback_doc_id, modules):
    existing_module_ids = conn.execute(
        'SELECT module_id FROM MicroModules WHERE batch_id = ?',
        (batch_id,),
    ).fetchall()
    if existing_module_ids:
        conn.executemany(
            'DELETE FROM Module_SourceDocument_Map WHERE module_id = ?',
            [(row['module_id'],) for row in existing_module_ids],
        )
    conn.execute('DELETE FROM MicroModules WHERE batch_id = ?', (batch_id,))

    for index, module in enumerate(modules):
        source_doc_ids = [int(doc_id) for doc_id in module.get('source_doc_ids', []) if doc_id is not None]
        representative_doc_id = source_doc_ids[0] if source_doc_ids else fallback_doc_id
        cur = conn.execute(
            """
            INSERT INTO MicroModules
                (doc_id, batch_id, module_title, module_content, key_takeaway, reading_time_minutes, sequence_order)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                representative_doc_id,
                batch_id,
                module.get('title', ''),
                module.get('content', ''),
                module.get('key_takeaway', ''),
                module.get('reading_time_minutes', 2.0),
                module.get('sequence_order', index + 1),
            ),
        )
        module_id = cur.lastrowid
        mapped_source_doc_ids = source_doc_ids or [fallback_doc_id]
        conn.executemany(
            'INSERT OR IGNORE INTO Module_SourceDocument_Map (module_id, doc_id) VALUES (?, ?)',
            [(module_id, doc_id) for doc_id in mapped_source_doc_ids],
        )


def save_generated_content(batch_id, doc_ids, domain_ids, batch_summary, modules):
    with get_db_connection() as conn:
        replace_document_domains(conn, doc_ids, domain_ids)
        replace_batch_modules(conn, batch_id, int(doc_ids[0]), modules)
        conn.execute(
            """
            UPDATE GenerationBatches
               SET combined_summary = ?,
                   completed_timestamp = CURRENT_TIMESTAMP,
                   updated_timestamp = CURRENT_TIMESTAMP
             WHERE batch_id = ?
            """,
            (batch_summary, batch_id),
        )
        conn.commit()


def _fetch_document_row(conn, doc_id, trainer_id=None):
    query = """
        SELECT doc_id, trainer_id, file_name, raw_text, upload_timestamp
          FROM SourceDocuments
         WHERE doc_id = ?
    """
    params = [doc_id]
    if trainer_id is not None:
        query += ' AND trainer_id = ?'
        params.append(trainer_id)
    return conn.execute(query, params).fetchone()


def get_document_with_modules(doc_id, trainer_id=None):
    with get_db_connection() as conn:
        doc = _fetch_document_row(conn, doc_id, trainer_id=trainer_id)
        if not doc:
            return None

        domains = conn.execute(
            """
            SELECT kd.domain_id, kd.domain_name
              FROM KnowledgeDomains kd
              JOIN Document_Domain_Map ddm ON kd.domain_id = ddm.domain_id
             WHERE ddm.doc_id = ?
             ORDER BY kd.domain_name
            """,
            (doc_id,),
        ).fetchall()

        modules = conn.execute(
            """
            SELECT DISTINCT
                   mm.module_id,
                   mm.batch_id,
                   mm.module_title,
                   mm.module_content,
                   mm.key_takeaway,
                   mm.reading_time_minutes,
                   mm.sequence_order
              FROM MicroModules mm
              LEFT JOIN Module_SourceDocument_Map msdm ON mm.module_id = msdm.module_id
             WHERE mm.doc_id = ? OR msdm.doc_id = ?
             ORDER BY mm.sequence_order, mm.module_id
            """,
            (doc_id, doc_id),
        ).fetchall()

    return {
        **dict(doc),
        'domains': [dict(domain) for domain in domains],
        'modules': [dict(module) for module in modules],
    }


def get_generation_job(job_id, trainer_id=None):
    with get_db_connection() as conn:
        query = """
            SELECT job_id, doc_id, batch_id, trainer_id, status, requested_domains_json, result_json,
                   error_message, created_timestamp, updated_timestamp, completed_timestamp
              FROM GenerationJobs
             WHERE job_id = ?
        """
        params = [job_id]
        if trainer_id is not None:
            query += ' AND trainer_id = ?'
            params.append(trainer_id)

        row = conn.execute(query, params).fetchone()
        if not row:
            return None

    payload = dict(row)
    requested = json.loads(payload.pop('requested_domains_json'))
    result_json = payload.pop('result_json')
    payload['requested_domain_ids'] = requested.get('domain_ids', [])
    payload['requested_domains'] = requested.get('domains', [])
    payload['requested_custom_prompt'] = requested.get('custom_prompt', '')
    payload['result'] = json.loads(result_json) if result_json else None
    return payload
