"""Elasticsearch sync — reads denormalized documents from MySQL and indexes into ES.

4 indexes: works_index, authors_index, venues_index, institutions_index.
Feature-flagged: enabled only when ``FeatureFlags.es_sync=True``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ESSyncStats:
    indexed: int = 0
    updated: int = 0
    errors: int = 0


# ── Index mappings (minimal) ─────────────────────────────────

INDEX_MAPPINGS: Dict[str, Dict[str, Any]] = {
    "works_index": {
        "properties": {
            "title": {"type": "text", "analyzer": "standard"},
            "abstract": {"type": "text", "analyzer": "standard"},
            "publication_year": {"type": "integer"},
            "cited_by_count": {"type": "integer"},
            "doi": {"type": "keyword"},
            "authors": {"type": "nested", "properties": {
                "display_name": {"type": "text"},
                "orcid": {"type": "keyword"},
            }},
            "institutions": {"type": "nested", "properties": {
                "display_name": {"type": "text"},
                "country_code": {"type": "keyword"},
            }},
            "concepts": {"type": "nested", "properties": {
                "display_name": {"type": "text"},
                "level": {"type": "integer"},
                "score": {"type": "float"},
            }},
            "venues": {"type": "nested", "properties": {
                "display_name": {"type": "text"},
                "issn": {"type": "keyword"},
            }},
            "oa_url": {"type": "keyword"},
            "source": {"type": "keyword"},
        }
    },
    "authors_index": {
        "properties": {
            "display_name": {"type": "text"},
            "orcid": {"type": "keyword"},
            "research_areas": {"type": "keyword"},
            "paper_count": {"type": "integer"},
        }
    },
    "venues_index": {
        "properties": {
            "display_name": {"type": "text"},
            "issn": {"type": "keyword"},
            "publisher": {"type": "text"},
            "is_open_access": {"type": "boolean"},
            "paper_count": {"type": "integer"},
        }
    },
    "institutions_index": {
        "properties": {
            "display_name": {"type": "text"},
            "type": {"type": "keyword"},
            "country_code": {"type": "keyword"},
            "paper_count": {"type": "integer"},
        }
    },
}


class ESSyncManager:
    """Syncs MySQL data to Elasticsearch for full-text search.

    Uses bulk indexing for efficient writes.  Gracefully degrades when
    Elasticsearch is not reachable.
    """

    def __init__(self, config: Any, mysql_repo: Any) -> None:
        self._config = config
        self._mysql = mysql_repo
        self._es = None

    def _ensure_es(self) -> bool:
        if self._es is not None:
            return True
        try:
            from elasticsearch import Elasticsearch
            self._es = Elasticsearch(
                hosts=self._config.hosts,
                request_timeout=self._config.connection_timeout,
            )
            return self._es.ping()
        except Exception:
            self._es = None
            return False

    # ── Public API ───────────────────────────────────────────

    def sync_all(self) -> Dict[str, ESSyncStats]:
        """Full sync: all 4 indexes."""
        if not self._ensure_es():
            return {"error": ESSyncStats(errors=1)}

        results: Dict[str, ESSyncStats] = {}
        for index_name in INDEX_MAPPINGS:
            results[index_name] = self.sync_index(index_name)
        return results

    def sync_index(self, index_name: str, batch_size: int = 1000) -> ESSyncStats:
        """Ensure the index exists and sync documents."""
        if not self._ensure_es():
            return ESSyncStats(errors=1)

        self._ensure_index_exists(index_name)

        docs = self._build_documents(index_name)
        if not docs:
            return ESSyncStats()

        return self._bulk_index(index_name, docs, batch_size)

    def sync_by_corpus(self, corpus_id: str, batch_size: int = 1000) -> Dict[str, ESSyncStats]:
        """Sync only works (and related entities) from a specific corpus."""
        if not self._ensure_es():
            return {"error": ESSyncStats(errors=1)}

        work_ids = self._mysql.get_corpus_members(corpus_id)
        if not work_ids:
            return {}

        self._ensure_index_exists("works_index")
        stats = self._sync_works_by_ids(work_ids, batch_size)
        return {"works_index": stats}

    def health_check(self) -> bool:
        return self._ensure_es()

    # ── Index management ─────────────────────────────────────

    def _ensure_index_exists(self, index_name: str) -> None:
        if not self._es:
            return
        mapping = INDEX_MAPPINGS.get(index_name, {})
        if not self._es.indices.exists(index=index_name):
            self._es.indices.create(index=index_name, body={"mappings": mapping})

    # ── Document builders ────────────────────────────────────

    def _build_documents(self, index_name: str) -> List[Dict[str, Any]]:
        builders = {
            "works_index": self._build_work_documents,
            "authors_index": self._build_author_documents,
            "venues_index": self._build_venue_documents,
            "institutions_index": self._build_institution_documents,
        }
        builder = builders.get(index_name)
        if not builder:
            return []
        return builder()

    def _build_work_documents(self) -> List[Dict[str, Any]]:
        try:
            cur = self._mysql._execute(
                """SELECT openalex_id, title, abstract, publication_year, cited_by_count,
                   doi, open_access_pdf_url, source FROM works LIMIT 50000"""
            )
            rows = cur.fetchall() if cur else []
        except Exception:
            return []

        docs = []
        for row in rows:
            doc = {
                "_id": row.get("openalex_id", ""),
                "_index": "works_index",
                "title": row.get("title", ""),
                "abstract": row.get("abstract", ""),
                "publication_year": row.get("publication_year"),
                "cited_by_count": row.get("cited_by_count", 0),
                "doi": row.get("doi"),
                "oa_url": row.get("open_access_pdf_url"),
                "source": row.get("source", "openalex"),
                "authors": self._get_work_authors(row.get("openalex_id", "")),
                "institutions": self._get_work_institutions(row.get("openalex_id", "")),
                "concepts": self._get_work_concepts(row.get("openalex_id", "")),
            }
            docs.append(doc)
        return docs

    def _build_author_documents(self) -> List[Dict[str, Any]]:
        try:
            cur = self._mysql._execute("SELECT openalex_id, display_name, orcid FROM authors LIMIT 50000")
            rows = cur.fetchall() if cur else []
        except Exception:
            return []
        return [
            {"_id": r.get("openalex_id", ""), "_index": "authors_index",
             "display_name": r.get("display_name", ""), "orcid": r.get("orcid")}
            for r in rows
        ]

    def _build_venue_documents(self) -> List[Dict[str, Any]]:
        try:
            cur = self._mysql._execute(
                "SELECT openalex_id, display_name, issn, publisher, is_open_access FROM venues LIMIT 50000"
            )
            rows = cur.fetchall() if cur else []
        except Exception:
            return []
        return [
            {"_id": r.get("openalex_id", ""), "_index": "venues_index",
             "display_name": r.get("display_name", ""), "issn": r.get("issn"),
             "publisher": r.get("publisher"), "is_open_access": r.get("is_open_access", False)}
            for r in rows
        ]

    def _build_institution_documents(self) -> List[Dict[str, Any]]:
        try:
            cur = self._mysql._execute(
                "SELECT openalex_id, display_name, type, country_code FROM institutions LIMIT 50000"
            )
            rows = cur.fetchall() if cur else []
        except Exception:
            return []
        return [
            {"_id": r.get("openalex_id", ""), "_index": "institutions_index",
             "display_name": r.get("display_name", ""), "type": r.get("type"),
             "country_code": r.get("country_code")}
            for r in rows
        ]

    def _get_work_authors(self, work_id: str) -> List[Dict[str, Any]]:
        try:
            cur = self._mysql._execute(
                """SELECT a.display_name, a.orcid
                   FROM work_authors wa JOIN authors a ON wa.author_id_fk = a.author_id
                   WHERE wa.work_openalex_id = %s ORDER BY wa.author_order LIMIT 50""",
                (work_id,),
            )
            return cur.fetchall() if cur else []
        except Exception:
            return []

    def _get_work_concepts(self, work_id: str) -> List[Dict[str, Any]]:
        try:
            cur = self._mysql._execute(
                """SELECT c.display_name, c.level, wc.score
                   FROM work_concepts wc JOIN concepts c ON wc.concept_id_fk = c.concept_id
                   WHERE wc.work_openalex_id = %s ORDER BY wc.score DESC LIMIT 20""",
                (work_id,),
            )
            return cur.fetchall() if cur else []
        except Exception:
            return []


    def _get_work_institutions(self, work_id: str) -> List[Dict[str, Any]]:
        try:
            cur = self._mysql._execute(
                """SELECT i.display_name, i.type, i.country_code
                   FROM work_institutions wi JOIN institutions i ON wi.institution_id_fk = i.institution_id
                   WHERE wi.work_openalex_id = %s ORDER BY i.display_name LIMIT 50""",
                (work_id,),
            )
            return cur.fetchall() if cur else []
        except Exception:
            return []
    # ── Bulk indexing ────────────────────────────────────────

    def _bulk_index(self, index_name: str, docs: List[Dict[str, Any]],
                    batch_size: int) -> ESSyncStats:
        stats = ESSyncStats()
        if not self._es:
            return ESSyncStats(errors=1)

        for i in range(0, len(docs), batch_size):
            batch = docs[i:i + batch_size]
            actions = []
            for doc in batch:
                actions.append({"index": {"_index": index_name, "_id": doc.pop("_id", None)}})
                doc.pop("_index", None)
                actions.append(doc)
            try:
                from elasticsearch.helpers import bulk
                success, errors = bulk(self._es, actions, raise_on_error=False, stats_only=True)
                if success:
                    stats.indexed += success
                if errors:
                    stats.errors += len(errors) if isinstance(errors, list) else errors
            except Exception:
                stats.errors += len(batch)
        return stats

    def _sync_works_by_ids(self, work_ids: List[str], batch_size: int) -> ESSyncStats:
        if not self._es:
            return ESSyncStats(errors=1)
        stats = ESSyncStats()
        for i in range(0, len(work_ids), batch_size):
            chunk = work_ids[i:i + batch_size]
            placeholders = ",".join(["%s"] * len(chunk))
            try:
                cur = self._mysql._execute(
                    f"""SELECT openalex_id, title, abstract, publication_year, cited_by_count,
                        doi, open_access_pdf_url, source FROM works WHERE openalex_id IN ({placeholders})""",
                    tuple(chunk),
                )
                rows = cur.fetchall() if cur else []
                docs = [{"_id": r.get("openalex_id", ""), "title": r.get("title", ""),
                         "abstract": r.get("abstract", ""), "publication_year": r.get("publication_year"),
                         "cited_by_count": r.get("cited_by_count", 0), "doi": r.get("doi"),
                         "oa_url": r.get("open_access_pdf_url"), "source": r.get("source", "openalex"),
                         "authors": self._get_work_authors(r.get("openalex_id", "")),
                         "institutions": self._get_work_institutions(r.get("openalex_id", "")),
                         "concepts": self._get_work_concepts(r.get("openalex_id", ""))}
                        for r in rows]
                stats = self._merge_stats(stats, self._bulk_index("works_index", docs, batch_size))
            except Exception:
                stats.errors += 1
        return stats

    @staticmethod
    def _merge_stats(a: ESSyncStats, b: ESSyncStats) -> ESSyncStats:
        a.indexed += b.indexed
        a.updated += b.updated
        a.errors += b.errors
        return a
