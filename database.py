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

CREATE TABLE IF NOT EXISTS MicroModules (
    module_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id               INTEGER NOT NULL REFERENCES SourceDocuments(doc_id) ON DELETE CASCADE,
    module_title         TEXT,
    module_content       TEXT NOT NULL,
    key_takeaway         TEXT,
    reading_time_minutes REAL DEFAULT 2.0,
    sequence_order       INTEGER
);

CREATE TABLE IF NOT EXISTS GenerationJobs (
    job_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id                 INTEGER NOT NULL REFERENCES SourceDocuments(doc_id) ON DELETE CASCADE,
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

SEED_SQL = """
INSERT OR IGNORE INTO KnowledgeDomains (domain_name, description) VALUES
  ('LifeInsurance',    '定期、終身及儲蓄型壽險商品'),
  ('InvestmentLinked', '投資型保單與基金選擇'),
  ('CRM',              '客戶關係管理與服務策略'),
  ('Compliance',       'FSC法規、AML、KYC及內部合規政策'),
  ('WealthManagement', '高資產客戶規劃、投資組合與遺產規劃'),
  ('TaxRegulations',   '保險稅務、資本利得及遺產稅處理');
"""


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    with get_db_connection() as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executescript(SEED_SQL)
        # Migration: add key_takeaway column for existing databases
        try:
            conn.execute('ALTER TABLE MicroModules ADD COLUMN key_takeaway TEXT')
            conn.commit()
        except sqlite3.OperationalError:
            pass


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


def create_generation_job(doc_id, trainer_id, domain_ids, domain_names):
    payload = json.dumps(
        {'domain_ids': domain_ids, 'domains': domain_names},
        ensure_ascii=False,
    )
    with get_db_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO GenerationJobs (doc_id, trainer_id, status, requested_domains_json)
            VALUES (?, ?, 'queued', ?)
            """,
            (doc_id, trainer_id, payload),
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


def replace_document_domains(conn, doc_id, domain_ids):
    conn.execute('DELETE FROM Document_Domain_Map WHERE doc_id = ?', (doc_id,))
    if domain_ids:
        conn.executemany(
            'INSERT INTO Document_Domain_Map (doc_id, domain_id) VALUES (?, ?)',
            [(doc_id, domain_id) for domain_id in domain_ids],
        )


def replace_document_modules(conn, doc_id, modules):
    conn.execute('DELETE FROM MicroModules WHERE doc_id = ?', (doc_id,))
    conn.executemany(
        """
        INSERT INTO MicroModules
            (doc_id, module_title, module_content, key_takeaway, reading_time_minutes, sequence_order)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                doc_id,
                module.get('title', ''),
                module.get('content', ''),
                module.get('key_takeaway', ''),
                module.get('reading_time_minutes', 2.0),
                module.get('sequence_order', index + 1),
            )
            for index, module in enumerate(modules)
        ],
    )


def save_generated_content(doc_id, domain_ids, modules):
    with get_db_connection() as conn:
        replace_document_domains(conn, doc_id, domain_ids)
        replace_document_modules(conn, doc_id, modules)
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
            SELECT module_id, module_title, module_content, key_takeaway, reading_time_minutes, sequence_order
              FROM MicroModules
             WHERE doc_id = ?
             ORDER BY sequence_order
            """,
            (doc_id,),
        ).fetchall()

    return {
        **dict(doc),
        'domains': [dict(domain) for domain in domains],
        'modules': [dict(module) for module in modules],
    }


def get_generation_job(job_id, trainer_id=None):
    with get_db_connection() as conn:
        query = """
            SELECT job_id, doc_id, trainer_id, status, requested_domains_json, result_json,
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
    payload['result'] = json.loads(result_json) if result_json else None
    return payload
