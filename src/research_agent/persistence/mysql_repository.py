"""MySQL implementation of ResearchRepository."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from research_agent.core.models import Corpus, GraphSnapshot, MCPResult, ResearchRun, TaskResult, to_dict
from research_agent.core.utils import stable_hash, utc_now_iso

from .repository import ResearchRepository


# ── DDL statements ──────────────────────────────────────────

_DDL_STATEMENTS = [
    # ── Core run tracking ──
    """CREATE TABLE IF NOT EXISTS analysis_runs (
        run_id          VARCHAR(64) PRIMARY KEY,
        question        TEXT NOT NULL,
        config_json     JSON NOT NULL,
        agent_mode      VARCHAR(32) NOT NULL DEFAULT 'react',
        status          VARCHAR(16) NOT NULL DEFAULT 'created',
        trace_json      JSON,
        artifacts_json  JSON,
        task_results_json JSON,
        created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        completed_at    DATETIME(3) NULL,
        INDEX idx_status (status),
        INDEX idx_created (created_at DESC)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS analysis_tasks (
        run_id          VARCHAR(64) NOT NULL,
        task_id         VARCHAR(32) NOT NULL,
        skill           VARCHAR(64) NOT NULL,
        title           VARCHAR(256) NOT NULL,
        depends_on_json JSON,
        parameters_json JSON,
        status          VARCHAR(16) NOT NULL DEFAULT 'pending',
        retries         INT NOT NULL DEFAULT 0,
        result_json     JSON,
        error           TEXT,
        started_at      DATETIME(3) NULL,
        completed_at    DATETIME(3) NULL,
        PRIMARY KEY (run_id, task_id),
        INDEX idx_run_status (run_id, status),
        FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS mcp_tool_calls (
        tool_call_id    VARCHAR(64) PRIMARY KEY,
        run_id          VARCHAR(64) NOT NULL,
        task_id         VARCHAR(32) NOT NULL,
        provider        VARCHAR(32) NOT NULL,
        tool            VARCHAR(64) NOT NULL,
        args_json       JSON,
        status          VARCHAR(16) NOT NULL,
        result_type     VARCHAR(32),
        summary_json    JSON,
        preview_json    JSON,
        artifact_id     VARCHAR(64),
        warnings_json   JSON,
        error           TEXT,
        called_at       DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        duration_ms     INT,
        INDEX idx_run (run_id),
        INDEX idx_task (run_id, task_id),
        INDEX idx_provider_tool (provider, tool),
        FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    # ── OpenAlex normalized entity store ──
    """CREATE TABLE IF NOT EXISTS countries (
        country_code    VARCHAR(8) PRIMARY KEY,
        display_name    VARCHAR(256),
        raw_json        JSON,
        created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS work_types (
        type_id         BIGINT AUTO_INCREMENT PRIMARY KEY,
        type_name       VARCHAR(128) NOT NULL,
        source          VARCHAR(32) NOT NULL DEFAULT 'openalex',
        raw_json        JSON,
        created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
        UNIQUE INDEX uq_work_type_name (type_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS institutions (
        institution_id  BIGINT AUTO_INCREMENT PRIMARY KEY,
        openalex_id     VARCHAR(64) NOT NULL,
        ror             VARCHAR(128),
        display_name    VARCHAR(512) NOT NULL,
        type            VARCHAR(64),
        country_code    VARCHAR(8),
        source          VARCHAR(32) NOT NULL DEFAULT 'openalex',
        raw_json        JSON,
        created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
        UNIQUE INDEX uq_institution_openalex (openalex_id),
        INDEX idx_institution_country (country_code),
        FOREIGN KEY (country_code) REFERENCES countries(country_code) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS authors (
        author_id       BIGINT AUTO_INCREMENT PRIMARY KEY,
        openalex_id     VARCHAR(64) NOT NULL,
        orcid           VARCHAR(128),
        display_name    VARCHAR(512) NOT NULL,
        source          VARCHAR(32) NOT NULL DEFAULT 'openalex',
        raw_json        JSON,
        created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
        UNIQUE INDEX uq_author_openalex (openalex_id),
        INDEX idx_author_orcid (orcid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS venues (
        venue_id        BIGINT AUTO_INCREMENT PRIMARY KEY,
        openalex_id     VARCHAR(64) NOT NULL,
        issn_l          VARCHAR(32),
        issn            VARCHAR(32),
        issn_json       JSON,
        display_name    VARCHAR(512) NOT NULL,
        publisher       VARCHAR(512),
        is_open_access  BOOLEAN NOT NULL DEFAULT FALSE,
        source          VARCHAR(32) NOT NULL DEFAULT 'openalex',
        raw_json        JSON,
        created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
        UNIQUE INDEX uq_venue_openalex (openalex_id),
        INDEX idx_venue_issn_l (issn_l)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS concepts (
        concept_id      BIGINT AUTO_INCREMENT PRIMARY KEY,
        openalex_id     VARCHAR(64) NOT NULL,
        display_name    VARCHAR(512) NOT NULL,
        level           INT NOT NULL DEFAULT 0,
        source          VARCHAR(32) NOT NULL DEFAULT 'openalex',
        raw_json        JSON,
        created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
        UNIQUE INDEX uq_concept_openalex (openalex_id),
        INDEX idx_concept_level (level)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS works (
        work_id             BIGINT AUTO_INCREMENT PRIMARY KEY,
        openalex_id         VARCHAR(64) NOT NULL,
        doi                 VARCHAR(512),
        title               TEXT NOT NULL,
        abstract            MEDIUMTEXT,
        publication_year    INT,
        cited_by_count      INT NOT NULL DEFAULT 0,
        type_id             BIGINT,
        primary_venue_id    BIGINT,
        open_access_pdf_url TEXT,
        source              VARCHAR(32) NOT NULL DEFAULT 'openalex',
        raw_json            JSON,
        created_at          DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at          DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
        UNIQUE INDEX uq_work_openalex (openalex_id),
        INDEX idx_work_doi (doi(191)),
        INDEX idx_work_year (publication_year),
        INDEX idx_work_type (type_id),
        INDEX idx_work_primary_venue (primary_venue_id),
        FOREIGN KEY (type_id) REFERENCES work_types(type_id) ON DELETE SET NULL,
        FOREIGN KEY (primary_venue_id) REFERENCES venues(venue_id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS work_authors (
        work_author_id       BIGINT AUTO_INCREMENT PRIMARY KEY,
        work_id_fk           BIGINT NOT NULL,
        author_id_fk         BIGINT NOT NULL,
        work_openalex_id     VARCHAR(64) NOT NULL,
        author_openalex_id   VARCHAR(64) NOT NULL,
        author_order         INT NOT NULL DEFAULT 0,
        is_corresponding     BOOLEAN NOT NULL DEFAULT FALSE,
        raw_author_position_json JSON,
        created_at           DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at           DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
        UNIQUE INDEX uq_work_author_order (work_id_fk, author_id_fk, author_order),
        INDEX idx_work_authors_work_openalex (work_openalex_id),
        INDEX idx_work_authors_author_openalex (author_openalex_id),
        FOREIGN KEY (work_id_fk) REFERENCES works(work_id) ON DELETE CASCADE,
        FOREIGN KEY (author_id_fk) REFERENCES authors(author_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS author_institutions (
        author_id_fk             BIGINT NOT NULL,
        institution_id_fk        BIGINT NOT NULL,
        author_openalex_id       VARCHAR(64) NOT NULL,
        institution_openalex_id  VARCHAR(64) NOT NULL,
        relationship_source      VARCHAR(64) NOT NULL DEFAULT 'openalex_authorship',
        first_seen_at            DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        last_seen_at             DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        PRIMARY KEY (author_id_fk, institution_id_fk),
        INDEX idx_author_inst_author_openalex (author_openalex_id),
        INDEX idx_author_inst_institution_openalex (institution_openalex_id),
        FOREIGN KEY (author_id_fk) REFERENCES authors(author_id) ON DELETE CASCADE,
        FOREIGN KEY (institution_id_fk) REFERENCES institutions(institution_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS work_institutions (
        work_id_fk               BIGINT NOT NULL,
        institution_id_fk        BIGINT NOT NULL,
        work_openalex_id         VARCHAR(64) NOT NULL,
        institution_openalex_id  VARCHAR(64) NOT NULL,
        source                   VARCHAR(64) NOT NULL DEFAULT 'openalex_authorship',
        created_at               DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        PRIMARY KEY (work_id_fk, institution_id_fk),
        INDEX idx_work_inst_work_openalex (work_openalex_id),
        INDEX idx_work_inst_institution_openalex (institution_openalex_id),
        FOREIGN KEY (work_id_fk) REFERENCES works(work_id) ON DELETE CASCADE,
        FOREIGN KEY (institution_id_fk) REFERENCES institutions(institution_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS work_author_affiliations (
        work_author_id           BIGINT NOT NULL,
        institution_id_fk        BIGINT NOT NULL,
        institution_openalex_id  VARCHAR(64) NOT NULL,
        raw_affiliation_string   TEXT,
        created_at               DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        PRIMARY KEY (work_author_id, institution_id_fk),
        INDEX idx_waa_institution_openalex (institution_openalex_id),
        FOREIGN KEY (work_author_id) REFERENCES work_authors(work_author_id) ON DELETE CASCADE,
        FOREIGN KEY (institution_id_fk) REFERENCES institutions(institution_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS work_concepts (
        work_id_fk          BIGINT NOT NULL,
        concept_id_fk       BIGINT NOT NULL,
        work_openalex_id    VARCHAR(64) NOT NULL,
        concept_openalex_id VARCHAR(64) NOT NULL,
        score               DOUBLE NOT NULL DEFAULT 0,
        source              VARCHAR(64) NOT NULL DEFAULT 'openalex',
        created_at          DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        PRIMARY KEY (work_id_fk, concept_id_fk),
        INDEX idx_work_concepts_work_openalex (work_openalex_id),
        INDEX idx_work_concepts_concept_openalex (concept_openalex_id),
        FOREIGN KEY (work_id_fk) REFERENCES works(work_id) ON DELETE CASCADE,
        FOREIGN KEY (concept_id_fk) REFERENCES concepts(concept_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS work_venues (
        work_id_fk       BIGINT NOT NULL,
        venue_id_fk      BIGINT NOT NULL,
        work_openalex_id VARCHAR(64) NOT NULL,
        venue_openalex_id VARCHAR(64) NOT NULL,
        is_primary       BOOLEAN NOT NULL DEFAULT TRUE,
        created_at       DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        PRIMARY KEY (work_id_fk, venue_id_fk),
        INDEX idx_work_venues_work_openalex (work_openalex_id),
        INDEX idx_work_venues_venue_openalex (venue_openalex_id),
        FOREIGN KEY (work_id_fk) REFERENCES works(work_id) ON DELETE CASCADE,
        FOREIGN KEY (venue_id_fk) REFERENCES venues(venue_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS citations (
        citation_id             BIGINT AUTO_INCREMENT PRIMARY KEY,
        citing_work_id_fk       BIGINT,
        cited_work_id_fk        BIGINT,
        citing_work_openalex_id VARCHAR(64) NOT NULL,
        cited_work_openalex_id  VARCHAR(64) NOT NULL,
        source                  VARCHAR(64) NOT NULL DEFAULT 'openalex_referenced_works',
        created_at              DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        UNIQUE INDEX uq_citation_openalex (citing_work_openalex_id, cited_work_openalex_id),
        INDEX idx_citing_fk (citing_work_id_fk),
        INDEX idx_cited_fk (cited_work_id_fk),
        INDEX idx_citing_openalex (citing_work_openalex_id),
        INDEX idx_cited_openalex (cited_work_openalex_id),
        FOREIGN KEY (citing_work_id_fk) REFERENCES works(work_id) ON DELETE SET NULL,
        FOREIGN KEY (cited_work_id_fk) REFERENCES works(work_id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS external_work_refs (
        openalex_id      VARCHAR(64) PRIMARY KEY,
        first_seen_from  VARCHAR(64),
        source           VARCHAR(64) NOT NULL DEFAULT 'citation',
        status           VARCHAR(16) NOT NULL DEFAULT 'pending',
        created_at       DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        updated_at       DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    # ── Corpus management ──
    """CREATE TABLE IF NOT EXISTS analysis_corpora (
        corpus_id       VARCHAR(64) PRIMARY KEY,
        run_id          VARCHAR(64) NOT NULL,
        query           TEXT NOT NULL,
        query_hash      VARCHAR(64) NOT NULL,
        paper_count     INT NOT NULL DEFAULT 0,
        source          VARCHAR(32) NOT NULL DEFAULT 'openalex',
        data_cutoff     DATETIME(3) NOT NULL,
        warnings_json   JSON,
        created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        UNIQUE INDEX uq_query_hash (query_hash),
        INDEX idx_run (run_id),
        FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS corpus_membership (
        corpus_id       VARCHAR(64) NOT NULL,
        work_id         VARCHAR(64) NOT NULL,
        work_id_fk      BIGINT,
        source          VARCHAR(32) NOT NULL,
        added_at        DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        PRIMARY KEY (corpus_id, work_id),
        INDEX idx_work (work_id),
        INDEX idx_work_fk (work_id_fk),
        FOREIGN KEY (corpus_id) REFERENCES analysis_corpora(corpus_id) ON DELETE CASCADE,
        FOREIGN KEY (work_id_fk) REFERENCES works(work_id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS crawl_frontier (
        work_id         VARCHAR(64) NOT NULL,
        corpus_id       VARCHAR(64) NOT NULL,
        depth           INT NOT NULL DEFAULT 0,
        source          VARCHAR(32) NOT NULL DEFAULT 'openalex',
        status          VARCHAR(16) NOT NULL DEFAULT 'pending',
        error           TEXT,
        attempted_at    DATETIME(3) NULL,
        completed_at    DATETIME(3) NULL,
        PRIMARY KEY (corpus_id, work_id),
        INDEX idx_status (corpus_id, status),
        INDEX idx_depth (corpus_id, depth),
        FOREIGN KEY (corpus_id) REFERENCES analysis_corpora(corpus_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    # ── Graph ──
    """CREATE TABLE IF NOT EXISTS graph_snapshots (
        graph_snapshot_id   VARCHAR(64) PRIMARY KEY,
        corpus_id           VARCHAR(64) NOT NULL,
        algorithm_version   VARCHAR(32) NOT NULL,
        parameters_json     JSON,
        node_count          INT NOT NULL DEFAULT 0,
        edge_count          INT NOT NULL DEFAULT 0,
        node_types_json     JSON,
        edge_types_json     JSON,
        created_at          DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        INDEX idx_corpus (corpus_id),
        FOREIGN KEY (corpus_id) REFERENCES analysis_corpora(corpus_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS graph_algorithm_runs (
        algo_run_id     VARCHAR(64) PRIMARY KEY,
        graph_snapshot_id VARCHAR(64) NOT NULL,
        algorithm       VARCHAR(64) NOT NULL,
        parameters_json JSON,
        results_json    JSON,
        duration_ms     INT,
        warnings_json   JSON,
        created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        INDEX idx_snapshot (graph_snapshot_id),
        FOREIGN KEY (graph_snapshot_id) REFERENCES graph_snapshots(graph_snapshot_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    # ── Evidence / PDF ──
    """CREATE TABLE IF NOT EXISTS materialization_jobs (
        job_id          VARCHAR(64) PRIMARY KEY,
        run_id          VARCHAR(64) NOT NULL,
        work_id         VARCHAR(64) NOT NULL,
        status          VARCHAR(16) NOT NULL DEFAULT 'pending',
        pdf_url         TEXT,
        pdf_sha256      VARCHAR(64),
        parser_name     VARCHAR(32),
        page_count      INT,
        error           TEXT,
        created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        completed_at    DATETIME(3) NULL,
        UNIQUE INDEX uq_run_work (run_id, work_id),
        INDEX idx_status (status),
        INDEX idx_sha256 (pdf_sha256),
        FOREIGN KEY (run_id) REFERENCES analysis_runs(run_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS paper_files (
        work_id         VARCHAR(64) NOT NULL,
        pdf_sha256      VARCHAR(64) NOT NULL,
        storage_key     VARCHAR(512) NOT NULL,
        file_size_bytes BIGINT,
        downloaded_at   DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        PRIMARY KEY (work_id, pdf_sha256),
        INDEX idx_sha256 (pdf_sha256)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS chunk_runs (
        chunk_run_id    VARCHAR(64) PRIMARY KEY,
        work_id         VARCHAR(64) NOT NULL,
        embedder_backend VARCHAR(32) NOT NULL,
        embedder_model  VARCHAR(128),
        parent_count    INT NOT NULL DEFAULT 0,
        child_count     INT NOT NULL DEFAULT 0,
        vector_dim      INT,
        created_at      DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        UNIQUE INDEX uq_work_backend (work_id, embedder_backend),
        INDEX idx_work (work_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    # ── Crawl jobs ──
    """CREATE TABLE IF NOT EXISTS crawl_jobs (
        job_id          VARCHAR(64) PRIMARY KEY,
        corpus_id       VARCHAR(64) NOT NULL,
        job_type        VARCHAR(32) NOT NULL,
        status          VARCHAR(16) NOT NULL DEFAULT 'pending',
        total_works     INT NOT NULL DEFAULT 0,
        completed_works INT NOT NULL DEFAULT 0,
        failed_works    INT NOT NULL DEFAULT 0,
        parameters_json JSON,
        started_at      DATETIME(3) NULL,
        completed_at    DATETIME(3) NULL,
        INDEX idx_corpus (corpus_id),
        FOREIGN KEY (corpus_id) REFERENCES analysis_corpora(corpus_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    # ── Graph nodes/edges ──
    """CREATE TABLE IF NOT EXISTS graph_nodes (
        node_id             VARCHAR(128) NOT NULL,
        graph_snapshot_id   VARCHAR(64) NOT NULL,
        node_type           VARCHAR(32) NOT NULL,
        label               VARCHAR(512) NOT NULL,
        properties_json     JSON,
        PRIMARY KEY (graph_snapshot_id, node_id),
        INDEX idx_type (graph_snapshot_id, node_type),
        FOREIGN KEY (graph_snapshot_id) REFERENCES graph_snapshots(graph_snapshot_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    """CREATE TABLE IF NOT EXISTS graph_edges (
        edge_id             VARCHAR(128) NOT NULL,
        graph_snapshot_id   VARCHAR(64) NOT NULL,
        source_node_id      VARCHAR(128) NOT NULL,
        target_node_id      VARCHAR(128) NOT NULL,
        edge_type           VARCHAR(32) NOT NULL,
        weight              DOUBLE NOT NULL DEFAULT 1.0,
        properties_json     JSON,
        PRIMARY KEY (graph_snapshot_id, edge_id),
        INDEX idx_source (graph_snapshot_id, source_node_id),
        INDEX idx_target (graph_snapshot_id, target_node_id),
        INDEX idx_type (graph_snapshot_id, edge_type),
        FOREIGN KEY (graph_snapshot_id) REFERENCES graph_snapshots(graph_snapshot_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

    # ── Embedding runs ──
    """CREATE TABLE IF NOT EXISTS embedding_runs (
        embedding_run_id VARCHAR(64) PRIMARY KEY,
        corpus_id        VARCHAR(64),
        embedder_backend VARCHAR(32) NOT NULL,
        embedder_model   VARCHAR(128),
        total_chunks     INT NOT NULL DEFAULT 0,
        vector_dim       INT,
        storage_path     VARCHAR(512),
        created_at       DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
        INDEX idx_corpus (corpus_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
]


class MySQLResearchRepository(ResearchRepository):
    """MySQL-backed ResearchRepository.

    Uses *pymysql* with a simple connection-per-operation pattern.
    All writes are idempotent via ``INSERT ... ON DUPLICATE KEY UPDATE``.
    """

    def __init__(self, config: Any = None) -> None:
        self._config = config
        self._conn_kwargs: Dict[str, Any] = {}
        if config is not None:
            self._conn_kwargs = {
                "host": getattr(config, "host", "localhost"),
                "port": getattr(config, "port", 3306),
                "user": getattr(config, "user", "research_agent"),
                "password": getattr(config, "password", ""),
                "database": getattr(config, "database", "research_agent"),
                "charset": getattr(config, "charset", "utf8mb4"),
            }
        self._pymysql = None

    # ── lazy import + connection ─────────────────────────────

    def _ensure_pymysql(self) -> None:
        if self._pymysql is not None:
            return
        try:
            import pymysql
            self._pymysql = pymysql
        except ImportError:
            raise ImportError(
                "pymysql is required for MySQLResearchRepository.  Install with: pip install pymysql"
            )

    @contextmanager
    def _conn(self):
        self._ensure_pymysql()
        conn_kwargs = dict(self._conn_kwargs)
        conn_kwargs.setdefault("cursorclass", self._pymysql.cursors.DictCursor)
        conn = self._pymysql.connect(**conn_kwargs)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _execute(self, sql: str, params: tuple = ()) -> Any:
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur

    # ── Schema init ──────────────────────────────────────────

    def init_schema(self) -> None:
        """Create all tables if they don't exist."""
        for ddl in _DDL_STATEMENTS:
            self._execute(ddl)

    # ── Run lifecycle ────────────────────────────────────────

    def create_run(self, run: ResearchRun) -> None:
        self._execute(
            """INSERT INTO analysis_runs (run_id, question, config_json, agent_mode, status, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (run.run_id, run.question, json.dumps(to_dict(run.config), default=str),
             run.agent_mode, run.status, run.created_at),
        )

    def update_run_status(self, run_id: str, status: str, completed_at: Optional[str] = None) -> None:
        if completed_at:
            self._execute(
                "UPDATE analysis_runs SET status = %s, completed_at = %s WHERE run_id = %s",
                (status, completed_at, run_id),
            )
        else:
            self._execute(
                "UPDATE analysis_runs SET status = %s WHERE run_id = %s",
                (status, run_id),
            )

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        cur = self._execute("SELECT * FROM analysis_runs WHERE run_id = %s", (run_id,))
        row = cur.fetchone()
        return row if row else None

    def list_runs(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        cur = self._execute(
            "SELECT run_id, question, agent_mode, status, created_at, completed_at "
            "FROM analysis_runs ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (limit, offset),
        )
        return cur.fetchall()

    # ── Task tracking ────────────────────────────────────────

    def save_task(self, run_id: str, task: Any) -> None:
        self._execute(
            """INSERT INTO analysis_tasks (run_id, task_id, skill, title, depends_on_json, parameters_json, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE skill=VALUES(skill), title=VALUES(title)""",
            (run_id, task.task_id, task.skill, task.title,
             json.dumps(getattr(task, 'depends_on', [])),
             json.dumps(getattr(task, 'parameters', {})),
             getattr(task, 'status', 'pending')),
        )

    def update_task_status(self, run_id: str, task_id: str, status: str,
                           error: Optional[str] = None) -> None:
        self._execute(
            "UPDATE analysis_tasks SET status = %s, error = %s WHERE run_id = %s AND task_id = %s",
            (status, error, run_id, task_id),
        )

    def save_task_result(self, run_id: str, task_result: TaskResult) -> None:
        self._execute(
            """UPDATE analysis_tasks
               SET status = %s, result_json = %s, error = %s,
                   retries = %s, completed_at = %s
               WHERE run_id = %s AND task_id = %s""",
            (task_result.status.value, json.dumps(to_dict(task_result), default=str),
             task_result.error, task_result.retries,
             task_result.completed_at or utc_now_iso(),
             run_id, task_result.task_id),
        )

    # ── MCP tool calls ───────────────────────────────────────

    def save_mcp_result(self, mcp_result: MCPResult) -> None:
        self._execute(
            """INSERT INTO mcp_tool_calls
               (tool_call_id, run_id, task_id, provider, tool, args_json, status,
                result_type, summary_json, preview_json, artifact_id, warnings_json, error)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE status=VALUES(status), summary_json=VALUES(summary_json)""",
            (mcp_result.tool_call_id, mcp_result.analysis_run_id, mcp_result.task_id,
             mcp_result.provider, mcp_result.method.get("name", ""),
             json.dumps(mcp_result.provenance, default=str),
             mcp_result.status, mcp_result.result_type,
             json.dumps(mcp_result.summary, default=str),
             json.dumps(mcp_result.preview, default=str)[:65535],
             mcp_result.artifact_id,
             json.dumps(mcp_result.warnings, default=str),
             mcp_result.error),
        )

    def get_mcp_results_for_run(self, run_id: str) -> List[Dict[str, Any]]:
        cur = self._execute(
            "SELECT * FROM mcp_tool_calls WHERE run_id = %s ORDER BY called_at ASC",
            (run_id,),
        )
        return cur.fetchall()

    # ── Corpus management ────────────────────────────────────

    def create_corpus(self, corpus: Corpus, run_id: str) -> None:
        query_hash = stable_hash(f"{corpus.query}|{len(corpus.papers)}", 32)
        self._execute(
            """INSERT INTO analysis_corpora (corpus_id, run_id, query, query_hash, paper_count, source, data_cutoff)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE paper_count=VALUES(paper_count)""",
            (corpus.corpus_id, run_id, corpus.query, query_hash,
             len(corpus.papers), "openalex", corpus.data_cutoff),
        )

    def find_corpus_by_hash(self, query_hash: str) -> Optional[str]:
        cur = self._execute(
            "SELECT corpus_id FROM analysis_corpora WHERE query_hash = %s LIMIT 1",
            (query_hash,),
        )
        row = cur.fetchone()
        return row["corpus_id"] if row else None

    def get_corpus(self, corpus_id: str) -> Optional[Dict[str, Any]]:
        cur = self._execute(
            "SELECT * FROM analysis_corpora WHERE corpus_id = %s LIMIT 1",
            (corpus_id,),
        )
        row = cur.fetchone()
        return row if row else None

    def upsert_corpus_membership(self, corpus_id: str, work_id: str, source: str) -> None:
        work_pk = self.find_work_pk(work_id)
        try:
            self._execute(
                """INSERT INTO corpus_membership (corpus_id, work_id, work_id_fk, source)
                   VALUES (%s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE source = VALUES(source), work_id_fk = VALUES(work_id_fk)""",
                (corpus_id, work_id, work_pk, source),
            )
        except Exception:
            # Backward-compatible path for databases initialized before work_id_fk existed.
            self._execute(
                """INSERT INTO corpus_membership (corpus_id, work_id, source)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE source = VALUES(source)""",
                (corpus_id, work_id, source),
            )

    def find_work_pk(self, openalex_id: str) -> Optional[int]:
        try:
            cur = self._execute("SELECT work_id FROM works WHERE openalex_id = %s LIMIT 1", (openalex_id,))
            row = cur.fetchone()
            return int(row["work_id"]) if row and row.get("work_id") is not None else None
        except Exception:
            return None

    def get_corpus_members(self, corpus_id: str) -> List[str]:
        cur = self._execute(
            "SELECT work_id FROM corpus_membership WHERE corpus_id = %s",
            (corpus_id,),
        )
        return [row["work_id"] for row in cur.fetchall()]

    # ── Crawl frontier ───────────────────────────────────────

    def upsert_frontier(self, work_id: str, corpus_id: str, depth: int, source: str) -> None:
        self._execute(
            """INSERT INTO crawl_frontier (work_id, corpus_id, depth, source)
               VALUES (%s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE depth = LEAST(depth, VALUES(depth))""",
            (work_id, corpus_id, depth, source),
        )

    def update_frontier_status(self, work_id: str, corpus_id: str, status: str,
                               error: Optional[str] = None) -> None:
        self._execute(
            """UPDATE crawl_frontier SET status = %s, error = %s,
               attempted_at = NOW(3), completed_at = IF(%s IN ('completed','failed'), NOW(3), completed_at)
               WHERE work_id = %s AND corpus_id = %s""",
            (status, error, status, work_id, corpus_id),
        )

    def get_pending_frontier(self, corpus_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        cur = self._execute(
            """SELECT work_id, depth, source FROM crawl_frontier
               WHERE corpus_id = %s AND status = 'pending'
               ORDER BY depth ASC LIMIT %s""",
            (corpus_id, limit),
        )
        return cur.fetchall()

    def find_frontier_by_work(self, corpus_id: str, work_id: str) -> Optional[Dict[str, Any]]:
        cur = self._execute(
            "SELECT * FROM crawl_frontier WHERE corpus_id = %s AND work_id = %s LIMIT 1",
            (corpus_id, work_id),
        )
        row = cur.fetchone()
        return row if row else None

    # ── Graph persistence ────────────────────────────────────

    def save_graph_snapshot(self, snapshot: GraphSnapshot) -> None:
        self._execute(
            """INSERT INTO graph_snapshots (graph_snapshot_id, corpus_id, algorithm_version,
               parameters_json, node_count, edge_count)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE node_count=VALUES(node_count), edge_count=VALUES(edge_count)""",
            (snapshot.graph_snapshot_id, snapshot.corpus_id, snapshot.algorithm_version,
             json.dumps(snapshot.parameters, default=str),
             len(snapshot.nodes), len(snapshot.edges)),
        )

    def save_graph_algorithm_run(self, run_record: Dict[str, Any]) -> None:
        self._execute(
            """INSERT INTO graph_algorithm_runs (algo_run_id, graph_snapshot_id, algorithm,
               parameters_json, results_json, duration_ms, warnings_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (run_record.get("algo_run_id", ""),
             run_record.get("graph_snapshot_id", ""),
             run_record.get("algorithm", ""),
             json.dumps(run_record.get("parameters", {}), default=str),
             json.dumps(run_record.get("results", {}), default=str),
             run_record.get("duration_ms"),
             json.dumps(run_record.get("warnings", []), default=str)),
        )

    # ── Evidence / PDF ───────────────────────────────────────

    def save_materialization_job(self, job: Dict[str, Any]) -> None:
        self._execute(
            """INSERT INTO materialization_jobs (job_id, run_id, work_id, status, pdf_url)
               VALUES (%s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE status=VALUES(status), pdf_url=VALUES(pdf_url)""",
            (job.get("job_id", ""), job.get("run_id", ""), job.get("work_id", ""),
             job.get("status", "pending"), job.get("pdf_url", "")),
        )

    def update_materialization_job(self, job_id: str, status: str, **kwargs: Any) -> None:
        sets = ["status = %s"]
        params: List[Any] = [status]
        for key in ("pdf_sha256", "parser_name", "page_count", "error"):
            if key in kwargs:
                sets.append(f"{key} = %s")
                params.append(kwargs[key])
        if status in ("completed", "failed"):
            sets.append("completed_at = NOW(3)")
        params.append(job_id)
        self._execute(f"UPDATE materialization_jobs SET {', '.join(sets)} WHERE job_id = %s", tuple(params))

    def save_paper_file(self, work_id: str, sha256: str, storage_key: str, file_size: int) -> None:
        self._execute(
            """INSERT INTO paper_files (work_id, pdf_sha256, storage_key, file_size_bytes)
               VALUES (%s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE storage_key=VALUES(storage_key)""",
            (work_id, sha256, storage_key, file_size),
        )

    def get_paper_file(self, work_id: str) -> Optional[Dict[str, Any]]:
        cur = self._execute(
            "SELECT * FROM paper_files WHERE work_id = %s LIMIT 1",
            (work_id,),
        )
        row = cur.fetchone()
        return row if row else None

    def save_chunk_run(self, chunk_run: Dict[str, Any]) -> None:
        self._execute(
            """INSERT INTO chunk_runs (chunk_run_id, work_id, embedder_backend, embedder_model,
               parent_count, child_count, vector_dim)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE parent_count=VALUES(parent_count), child_count=VALUES(child_count)""",
            (chunk_run.get("chunk_run_id", ""), chunk_run.get("work_id", ""),
             chunk_run.get("embedder_backend", "hash"), chunk_run.get("embedder_model", ""),
             chunk_run.get("parent_count", 0), chunk_run.get("child_count", 0),
             chunk_run.get("vector_dim")),
        )

    def save_run_outputs(self, run: ResearchRun) -> None:
        """Persist final trace, artifacts, task results and status for a run."""
        self._execute(
            """UPDATE analysis_runs
               SET status = %s,
                   trace_json = %s,
                   artifacts_json = %s,
                   task_results_json = %s,
                   completed_at = %s
               WHERE run_id = %s""",
            (
                run.status,
                json.dumps(to_dict(run.trace), default=str),
                json.dumps(to_dict(run.artifacts), default=str),
                json.dumps(to_dict(run.task_results), default=str),
                run.completed_at or utc_now_iso(),
                run.run_id,
            ),
        )
    # ── Health ───────────────────────────────────────────────

    def health_check(self) -> bool:
        try:
            self._execute("SELECT 1")
            return True
        except Exception:
            return False


