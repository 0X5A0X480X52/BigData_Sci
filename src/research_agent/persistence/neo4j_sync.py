"""Neo4j graph sync — reads from MySQL and syncs to Neo4j via UNWIND MERGE.

8 node types: Paper, Author, Institution, Venue, Concept, Country, Database, WorkType
Relationship types include CITES, AUTHORED, PUBLISHED_IN, ABOUT, HAS_TOPIC,
AFFILIATED_WITH, ASSOCIATED_WITH, AFFILIATED_IN_WORK, and LOCATED_IN.

Feature-flagged: enabled only when ``FeatureFlags.neo4j_sync=True``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SyncStats:
    nodes_created: int = 0
    nodes_updated: int = 0
    relationships_created: int = 0
    errors: int = 0


# ── Cypher templates ─────────────────────────────────────────

CYPHER_MERGE_NODE: Dict[str, str] = {
    "Paper": """
        UNWIND $nodes AS node
        MERGE (n:Paper {openalex_id: node.openalex_id})
        SET n.title = node.title,
            n.year = node.publication_year,
            n.cited_by_count = node.cited_by_count,
            n.doi = node.doi
    """,
    "Author": """
        UNWIND $nodes AS node
        MERGE (n:Author {openalex_id: node.openalex_id})
        SET n.display_name = node.display_name,
            n.orcid = node.orcid
    """,
    "Institution": """
        UNWIND $nodes AS node
        MERGE (n:Institution {openalex_id: node.openalex_id})
        SET n.display_name = node.display_name,
            n.type = node.type,
            n.country_code = node.country_code
    """,
    "Venue": """
        UNWIND $nodes AS node
        MERGE (n:Venue {openalex_id: node.openalex_id})
        SET n.display_name = node.display_name,
            n.issn = node.issn,
            n.publisher = node.publisher,
            n.is_open_access = node.is_open_access
    """,
    "Concept": """
        UNWIND $nodes AS node
        MERGE (n:Concept {openalex_id: node.openalex_id})
        SET n.display_name = node.display_name,
            n.level = node.level
    """,
    "Country": """
        UNWIND $nodes AS node
        MERGE (n:Country {country_code: node.country_code})
        SET n.eng_name = node.eng_name
    """,
}

CYPHER_MERGE_REL: Dict[str, str] = {
    "CITES": """
        UNWIND $rels AS rel
        MATCH (src:Paper {openalex_id: rel.citing_work_id})
        MATCH (dst:Paper {openalex_id: rel.cited_work_id})
        MERGE (src)-[:CITES]->(dst)
    """,
    "AUTHORED": """
        UNWIND $rels AS rel
        MATCH (a:Author {openalex_id: rel.author_id})
        MATCH (p:Paper {openalex_id: rel.work_id})
        MERGE (a)-[:AUTHORED {order: rel.author_order}]->(p)
    """,
    "PUBLISHED_IN": """
        UNWIND $rels AS rel
        MATCH (p:Paper {openalex_id: rel.work_id})
        MATCH (v:Venue {openalex_id: rel.venue_id})
        MERGE (p)-[:PUBLISHED_IN]->(v)
    """,
    "ABOUT": """
        UNWIND $rels AS rel
        MATCH (p:Paper {openalex_id: rel.work_id})
        MATCH (c:Concept {openalex_id: rel.concept_id})
        MERGE (p)-[:ABOUT {score: rel.score}]->(c)
    """,
    "HAS_TOPIC": """
        UNWIND $rels AS rel
        MATCH (p:Paper {openalex_id: rel.work_id})
        MATCH (c:Concept {openalex_id: rel.concept_id})
        MERGE (p)-[:HAS_TOPIC]->(c)
    """,
    "AFFILIATED_WITH": """
        UNWIND $rels AS rel
        MATCH (a:Author {openalex_id: rel.author_id})
        MATCH (i:Institution {openalex_id: rel.institution_id})
        MERGE (a)-[:AFFILIATED_WITH]->(i)
    """,
    "ASSOCIATED_WITH": """
        UNWIND $rels AS rel
        MATCH (p:Paper {openalex_id: rel.work_id})
        MATCH (i:Institution {openalex_id: rel.institution_id})
        MERGE (p)-[:ASSOCIATED_WITH]->(i)
    """,
    "AFFILIATED_IN_WORK": """
        UNWIND $rels AS rel
        MATCH (a:Author {openalex_id: rel.author_id})
        MATCH (i:Institution {openalex_id: rel.institution_id})
        MATCH (p:Paper {openalex_id: rel.work_id})
        MERGE (a)-[r:AFFILIATED_IN_WORK {work_openalex_id: rel.work_id, author_order: rel.author_order}]->(i)
        SET r.work_title = p.title
    """,
    "LOCATED_IN": """
        UNWIND $rels AS rel
        MATCH (i:Institution {openalex_id: rel.institution_id})
        MATCH (c:Country {country_code: rel.country_code})
        MERGE (i)-[:LOCATED_IN]->(c)
    """,
}

# ── Sync dependency order ────────────────────────────────────

NODE_SYNC_ORDER = [
    "Country", "Institution", "Author", "Venue", "Concept", "Paper",
]

REL_SYNC_ORDER = [
    "LOCATED_IN", "AFFILIATED_WITH", "ASSOCIATED_WITH", "AFFILIATED_IN_WORK",
    "AUTHORED", "PUBLISHED_IN", "ABOUT", "HAS_TOPIC", "CITES",
]


class Neo4jGraphSync:
    """Syncs structured scholarly data from MySQL to Neo4j.

    Uses the UNWIND + MERGE batch pattern for efficient upserts.
    Gracefully degrades when Neo4j is unavailable.
    """

    def __init__(self, config: Any, mysql_repo: Any) -> None:
        self._config = config
        self._mysql = mysql_repo
        self._driver = None

    def _ensure_driver(self) -> bool:
        if self._driver is not None:
            return True
        try:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(
                self._config.uri,
                auth=(self._config.user, self._config.password),
            )
            # Connection test
            with self._driver.session(database=self._config.database) as s:
                s.run("RETURN 1")
            return True
        except Exception:
            self._driver = None
            return False

    # ── Public API ───────────────────────────────────────────

    def sync_all(self, corpus_id: Optional[str] = None) -> Dict[str, SyncStats]:
        """Full sync: nodes first, then relationships."""
        if not self._ensure_driver():
            return {"error": SyncStats(errors=1)}

        results: Dict[str, SyncStats] = {}

        # Sync nodes in dependency order
        for node_type in NODE_SYNC_ORDER:
            results[f"node_{node_type}"] = self.sync_nodes(node_type)

        # Sync relationships
        for rel_type in REL_SYNC_ORDER:
            results[f"rel_{rel_type}"] = self.sync_relationships(rel_type)

        return results

    def sync_nodes(self, entity_type: str, batch_size: int = 500) -> SyncStats:
        """Read entities from MySQL and sync to Neo4j."""
        if not self._ensure_driver():
            return SyncStats(errors=1)

        cypher = CYPHER_MERGE_NODE.get(entity_type)
        if not cypher:
            return SyncStats(errors=1)

        # Read from MySQL
        rows = self._read_entities(entity_type)
        if not rows:
            return SyncStats()

        stats = SyncStats()
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            try:
                with self._driver.session(database=self._config.database) as session:
                    result = session.run(cypher, nodes=batch)
                    summary = result.consume()
                    stats.nodes_created += summary.counters.nodes_created
            except Exception:
                stats.errors += 1

        return stats

    def sync_relationships(self, rel_type: str, batch_size: int = 1000) -> SyncStats:
        """Read relationships from MySQL and sync to Neo4j."""
        if not self._ensure_driver():
            return SyncStats(errors=1)

        cypher = CYPHER_MERGE_REL.get(rel_type)
        if not cypher:
            return SyncStats(errors=1)

        rows = self._read_relationships(rel_type)
        if not rows:
            return SyncStats()

        stats = SyncStats()
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            try:
                with self._driver.session(database=self._config.database) as session:
                    result = session.run(cypher, rels=batch)
                    summary = result.consume()
                    stats.relationships_created += summary.counters.relationships_created
            except Exception:
                stats.errors += 1

        return stats

    def sync_corpus(self, corpus_id: str, batch_size: int = 500) -> Dict[str, SyncStats]:
        """Sync only entities and relationships relevant to a specific corpus."""
        # Get work IDs in this corpus
        work_ids = self._mysql.get_corpus_members(corpus_id)
        if not work_ids:
            return {}

        results: Dict[str, SyncStats] = {}
        # Sync papers in this corpus
        results["node_Paper"] = self._sync_works_by_ids(work_ids, batch_size)
        # Sync citations within corpus
        results["rel_CITES"] = self._sync_citations_by_works(work_ids, batch_size)

        return results

    def health_check(self) -> bool:
        return self._ensure_driver()

    # ── MySQL readers ────────────────────────────────────────

    def _read_entities(self, entity_type: str) -> List[Dict[str, Any]]:
        table_map = {
            "Paper": "works",
            "Author": "authors",
            "Institution": "institutions",
            "Venue": "venues",
            "Concept": "concepts",
            "Country": "countries",
        }
        table = table_map.get(entity_type)
        if not table:
            return []
        try:
            cur = self._mysql._execute(f"SELECT * FROM {table} LIMIT 100000")
            return cur.fetchall() if cur else []
        except Exception:
            return []

    def _read_relationships(self, rel_type: str) -> List[Dict[str, Any]]:
        query_map = {
            "CITES": "SELECT citing_work_openalex_id AS citing_work_id, cited_work_openalex_id AS cited_work_id FROM citations LIMIT 500000",
            "AUTHORED": "SELECT work_openalex_id AS work_id, author_openalex_id AS author_id, author_order FROM work_authors LIMIT 500000",
            "PUBLISHED_IN": "SELECT work_openalex_id AS work_id, venue_openalex_id AS venue_id FROM work_venues LIMIT 500000",
            "ABOUT": "SELECT work_openalex_id AS work_id, concept_openalex_id AS concept_id, score FROM work_concepts WHERE score > 0 LIMIT 500000",
            "HAS_TOPIC": "SELECT work_openalex_id AS work_id, concept_openalex_id AS concept_id FROM work_concepts WHERE score > 0.3 LIMIT 500000",
            "AFFILIATED_WITH": "SELECT author_openalex_id AS author_id, institution_openalex_id AS institution_id FROM author_institutions LIMIT 500000",
            "ASSOCIATED_WITH": "SELECT work_openalex_id AS work_id, institution_openalex_id AS institution_id FROM work_institutions LIMIT 500000",
            "AFFILIATED_IN_WORK": (
                "SELECT wa.work_openalex_id AS work_id, wa.author_openalex_id AS author_id, "
                "waa.institution_openalex_id AS institution_id, wa.author_order "
                "FROM work_author_affiliations waa "
                "JOIN work_authors wa ON waa.work_author_id = wa.work_author_id "
                "LIMIT 500000"
            ),
            "LOCATED_IN": "SELECT openalex_id AS institution_id, country_code FROM institutions WHERE country_code IS NOT NULL LIMIT 50000",
        }
        query = query_map.get(rel_type)
        if not query:
            return []
        try:
            cur = self._mysql._execute(query)
            return cur.fetchall() if cur else []
        except Exception:
            return []

    def _sync_works_by_ids(self, work_ids: List[str], batch_size: int) -> SyncStats:
        if not self._ensure_driver():
            return SyncStats(errors=1)
        # Build work list from MySQL
        stats = SyncStats()
        for i in range(0, len(work_ids), batch_size):
            chunk = work_ids[i:i + batch_size]
            placeholders = ",".join(["%s"] * len(chunk))
            try:
                cur = self._mysql._execute(
                    f"SELECT openalex_id, title, publication_year, cited_by_count, doi FROM works WHERE openalex_id IN ({placeholders})",
                    tuple(chunk),
                )
                rows = cur.fetchall() if cur else []
                if rows:
                    with self._driver.session(database=self._config.database) as session:
                        result = session.run(CYPHER_MERGE_NODE["Paper"], nodes=rows)
                        summary = result.consume()
                        stats.nodes_created += summary.counters.nodes_created
            except Exception:
                stats.errors += 1
        return stats

    def _sync_citations_by_works(self, work_ids: List[str], batch_size: int) -> SyncStats:
        if not self._ensure_driver():
            return SyncStats(errors=1)
        stats = SyncStats()
        for i in range(0, len(work_ids), batch_size):
            chunk = work_ids[i:i + batch_size]
            placeholders = ",".join(["%s"] * len(chunk))
            try:
                cur = self._mysql._execute(
                    f"SELECT citing_work_openalex_id AS citing_work_id, cited_work_openalex_id AS cited_work_id FROM citations WHERE citing_work_openalex_id IN ({placeholders})",
                    tuple(chunk),
                )
                rows = cur.fetchall() if cur else []
                if rows:
                    with self._driver.session(database=self._config.database) as session:
                        result = session.run(CYPHER_MERGE_REL["CITES"], rels=rows)
                        summary = result.consume()
                        stats.relationships_created += summary.counters.relationships_created
            except Exception:
                stats.errors += 1
        return stats
