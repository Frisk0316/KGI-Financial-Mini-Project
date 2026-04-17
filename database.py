import sqlite3
import os

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
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_db_connection() as conn:
        conn.executescript(SCHEMA_SQL)
        conn.executescript(SEED_SQL)
        # Migration: add key_takeaway column for existing databases
        try:
            conn.execute("ALTER TABLE MicroModules ADD COLUMN key_takeaway TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists


def get_all_domains():
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT domain_id, domain_name, description FROM KnowledgeDomains ORDER BY domain_name"
        ).fetchall()
    return [dict(r) for r in rows]


def insert_document(trainer_id, file_name, raw_text):
    with get_db_connection() as conn:
        cur = conn.execute(
            "INSERT INTO SourceDocuments (trainer_id, file_name, raw_text) VALUES (?, ?, ?)",
            (trainer_id, file_name, raw_text)
        )
        conn.commit()
        return cur.lastrowid


def tag_document(doc_id, domain_ids):
    with get_db_connection() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO Document_Domain_Map (doc_id, domain_id) VALUES (?, ?)",
            [(doc_id, did) for did in domain_ids]
        )
        conn.commit()


def insert_micro_modules(doc_id, modules):
    with get_db_connection() as conn:
        # Delete previous generation for this doc (idempotent re-generation)
        conn.execute("DELETE FROM MicroModules WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            """INSERT INTO MicroModules
               (doc_id, module_title, module_content, key_takeaway, reading_time_minutes, sequence_order)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (
                    doc_id,
                    m.get('title', ''),
                    m.get('content', ''),
                    m.get('key_takeaway', ''),
                    m.get('reading_time_minutes', 2.0),
                    m.get('sequence_order', i + 1),
                )
                for i, m in enumerate(modules)
            ]
        )
        conn.commit()


def get_document_with_modules(doc_id):
    with get_db_connection() as conn:
        doc = conn.execute(
            "SELECT doc_id, trainer_id, file_name, raw_text, upload_timestamp FROM SourceDocuments WHERE doc_id = ?",
            (doc_id,)
        ).fetchone()
        if not doc:
            return None

        domains = conn.execute(
            """SELECT kd.domain_id, kd.domain_name FROM KnowledgeDomains kd
               JOIN Document_Domain_Map ddm ON kd.domain_id = ddm.domain_id
               WHERE ddm.doc_id = ?
               ORDER BY kd.domain_name""",
            (doc_id,)
        ).fetchall()

        modules = conn.execute(
            """SELECT module_id, module_title, module_content, key_takeaway, reading_time_minutes, sequence_order
               FROM MicroModules WHERE doc_id = ? ORDER BY sequence_order""",
            (doc_id,)
        ).fetchall()

    return {
        **dict(doc),
        'domains': [dict(d) for d in domains],
        'modules': [dict(m) for m in modules],
    }
