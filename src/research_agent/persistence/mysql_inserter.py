"""MySQL batch inserter for normalized OpenAlex entities.

Writes entities in foreign-key dependency order:
1. countries -> work_types -> institutions -> authors -> venues -> concepts -> works
2. work_authors -> author_institutions -> work_institutions -> work_author_affiliations
3. work_concepts -> work_venues -> citations / external_work_refs

This version uses multi-row INSERT ... ON DUPLICATE KEY UPDATE where possible.
For tables with auto-increment surrogate keys, it uses:
    bulk upsert -> bulk SELECT id mapping -> fill _id_cache
instead of row-by-row LAST_INSERT_ID.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - optional dependency
    tqdm = None  # type: ignore[assignment]

from research_agent.data.cleaners import BatchCleanedResult


@dataclass
class BatchInsertStats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    details: Dict[str, int] = field(default_factory=dict)


class MySQLInserter:
    """Writes cleaned OpenAlex entities to MySQL with idempotent upserts."""

    def __init__(self, repository: Any) -> None:
        self._repo = repository
        self._id_cache: Dict[str, Dict[Any, int]] = {
            "works": {},
            "authors": {},
            "institutions": {},
            "venues": {},
            "concepts": {},
            "work_types": {},
            "work_authors": {},
        }
        self._show_inner_progress = False
        self._batch_size = 100

    def insert_batch(
        self,
        batch: BatchCleanedResult,
        corpus_id: str = "",
        run_id: str = "",
        membership_source: str = "openalex",
        show_progress: bool = True,
        batch_size: int = 100,
    ) -> BatchInsertStats:
        """Insert a cleaned batch into normalized MySQL tables.

        Progress bar semantics:
        - outer progress: table/stage-level progress
        - inner progress: row-level progress within each bulk stage

        batch_size controls the number of rows in each multi-row INSERT. Keep it
        conservative because raw_json fields can be large.
        """

        del run_id  # Reserved for future audit/membership extensions.

        stats = BatchInsertStats()
        self._show_inner_progress = bool(show_progress)
        self._batch_size = max(1, int(batch_size or 100))

        insert_steps = [
            ("countries", self._insert_countries),
            ("work_types", self._insert_work_types),
            ("institutions", self._insert_institutions),
            ("authors", self._insert_authors),
            ("venues", self._insert_venues),
            ("concepts", self._insert_concepts),
            ("works", self._insert_works),
            ("work_authors", self._insert_work_authors),
            ("author_institutions", self._insert_author_institutions),
            ("work_institutions", self._insert_work_institutions),
            ("work_author_affiliations", self._insert_work_author_affiliations),
            ("work_concepts", self._insert_work_concepts),
            ("work_venues", self._insert_work_venues),
            ("citations", self._insert_citations),
        ]

        total_steps = len(insert_steps) + (1 if corpus_id else 0)

        progress = None
        if show_progress and tqdm is not None:
            progress = tqdm(
                total=total_steps,
                desc="MySQL insert",
                unit="stage",
                dynamic_ncols=True,
                position=0,
            )

        try:
            for step_name, fn in insert_steps:
                if progress is not None:
                    progress.set_description(f"MySQL insert: {step_name}")
                    progress.set_postfix(
                        {
                            "papers": len(batch.papers),
                            "authors": len(batch.authors),
                            "institutions": len(batch.institutions),
                            "concepts": len(batch.concepts),
                            "citations": len(batch.citations),
                        }
                    )

                step_stats = fn(batch)
                stats = self._merge_stats(stats, step_stats)

                if progress is not None:
                    progress.update(1)

            if corpus_id:
                if progress is not None:
                    progress.set_description("MySQL insert: corpus_membership")
                    progress.set_postfix({"corpus": corpus_id, "papers": len(batch.papers)})

                stats.details["corpus_membership"] = self.insert_corpus_membership_batch(
                    corpus_id,
                    [paper.work_id for paper in batch.papers],
                    membership_source,
                )

                if progress is not None:
                    progress.update(1)

        finally:
            if progress is not None:
                progress.close()
            self._show_inner_progress = False

        return stats

    def insert_works_only(self, batch: BatchCleanedResult, corpus_id: str) -> int:
        stats = self.insert_batch(batch, corpus_id=corpus_id)
        return stats.details.get("works", 0)

    def insert_corpus_membership_batch(
        self,
        corpus_id: str,
        work_ids: List[str],
        source: str = "openalex",
    ) -> int:
        """Keep repository-level membership insertion for schema compatibility.

        This is still row-wise because the repository method hides the table schema.
        If needed, this can be converted after confirming the membership table DDL.
        """
        count = 0
        iterable = self._progress(work_ids, total=len(work_ids), desc="corpus_membership")
        for wid in iterable:
            try:
                self._repo.upsert_corpus_membership(corpus_id, wid, source)
                count += 1
            except Exception:
                pass
        return count

    # ------------------------------------------------------------------
    # Base/entity tables
    # ------------------------------------------------------------------

    def _insert_countries(self, batch: BatchCleanedResult) -> BatchInsertStats:
        rows = [
            (country.country_code, country.display_name, _json(country.raw))
            for country in batch.countries.values()
        ]
        stats = self._bulk_insert_values(
            table_name="countries",
            rows=rows,
            sql_head="""INSERT INTO countries (country_code, display_name, raw_json)""",
            row_placeholder="(%s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE
                display_name=VALUES(display_name), raw_json=VALUES(raw_json)""",
        )
        stats.details["countries"] = stats.inserted
        return stats

    def _insert_work_types(self, batch: BatchCleanedResult) -> BatchInsertStats:
        work_types = list(batch.work_types.values())
        rows = [(wt.type_name, wt.source, _json(wt.raw)) for wt in work_types]
        stats = self._bulk_insert_values(
            table_name="work_types",
            rows=rows,
            sql_head="""INSERT INTO work_types (type_name, source, raw_json)""",
            row_placeholder="(%s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE raw_json=VALUES(raw_json)""",
        )

        keys = [wt.type_name for wt in work_types if wt.type_name]
        self._id_cache["work_types"].update(
            self._fetch_id_map(
                table="work_types",
                pk_col="type_id",
                key_col="type_name",
                keys=keys,
            )
        )
        stats.details["work_types"] = stats.inserted
        return stats

    def _insert_institutions(self, batch: BatchCleanedResult) -> BatchInsertStats:
        institutions = list(batch.institutions.values())
        rows = [
            (inst.openalex_id, inst.ror, inst.display_name, inst.type, inst.country_code, _json(inst.raw))
            for inst in institutions
        ]
        stats = self._bulk_insert_values(
            table_name="institutions",
            rows=rows,
            sql_head="""INSERT INTO institutions
                (openalex_id, ror, display_name, type, country_code, raw_json)""",
            row_placeholder="(%s, %s, %s, %s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE
                ror=VALUES(ror), display_name=VALUES(display_name), type=VALUES(type),
                country_code=VALUES(country_code), raw_json=VALUES(raw_json)""",
        )

        keys = [inst.openalex_id for inst in institutions if inst.openalex_id]
        self._id_cache["institutions"].update(
            self._fetch_id_map(
                table="institutions",
                pk_col="institution_id",
                key_col="openalex_id",
                keys=keys,
            )
        )
        stats.details["institutions"] = stats.inserted
        return stats

    def _insert_authors(self, batch: BatchCleanedResult) -> BatchInsertStats:
        authors = list(batch.authors.values())
        rows = [
            (author.openalex_id, author.orcid, author.display_name, _json(author.raw))
            for author in authors
        ]
        stats = self._bulk_insert_values(
            table_name="authors",
            rows=rows,
            sql_head="""INSERT INTO authors (openalex_id, orcid, display_name, raw_json)""",
            row_placeholder="(%s, %s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE
                orcid=VALUES(orcid), display_name=VALUES(display_name), raw_json=VALUES(raw_json)""",
        )

        keys = [author.openalex_id for author in authors if author.openalex_id]
        self._id_cache["authors"].update(
            self._fetch_id_map(
                table="authors",
                pk_col="author_id",
                key_col="openalex_id",
                keys=keys,
            )
        )
        stats.details["authors"] = stats.inserted
        return stats

    def _insert_venues(self, batch: BatchCleanedResult) -> BatchInsertStats:
        venues = list(batch.venues.values())
        rows = [
            (
                venue.openalex_id,
                venue.issn_l,
                venue.issn_l or (venue.issn[0] if venue.issn else None),
                _json(venue.issn),
                venue.display_name,
                venue.publisher,
                venue.is_open_access,
                _json(venue.raw),
            )
            for venue in venues
        ]
        stats = self._bulk_insert_values(
            table_name="venues",
            rows=rows,
            sql_head="""INSERT INTO venues
                (openalex_id, issn_l, issn, issn_json, display_name, publisher, is_open_access, raw_json)""",
            row_placeholder="(%s, %s, %s, %s, %s, %s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE
                issn_l=VALUES(issn_l), issn=VALUES(issn), issn_json=VALUES(issn_json),
                display_name=VALUES(display_name), publisher=VALUES(publisher),
                is_open_access=VALUES(is_open_access), raw_json=VALUES(raw_json)""",
        )

        keys = [venue.openalex_id for venue in venues if venue.openalex_id]
        self._id_cache["venues"].update(
            self._fetch_id_map(
                table="venues",
                pk_col="venue_id",
                key_col="openalex_id",
                keys=keys,
            )
        )
        stats.details["venues"] = stats.inserted
        return stats

    def _insert_concepts(self, batch: BatchCleanedResult) -> BatchInsertStats:
        concepts = list(batch.concepts.values())
        rows = [
            (concept.openalex_id, concept.display_name, concept.level, _json(concept.raw))
            for concept in concepts
        ]
        stats = self._bulk_insert_values(
            table_name="concepts",
            rows=rows,
            sql_head="""INSERT INTO concepts (openalex_id, display_name, level, raw_json)""",
            row_placeholder="(%s, %s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE
                display_name=VALUES(display_name), level=VALUES(level), raw_json=VALUES(raw_json)""",
        )

        keys = [concept.openalex_id for concept in concepts if concept.openalex_id]
        self._id_cache["concepts"].update(
            self._fetch_id_map(
                table="concepts",
                pk_col="concept_id",
                key_col="openalex_id",
                keys=keys,
            )
        )
        stats.details["concepts"] = stats.inserted
        return stats

    def _insert_works(self, batch: BatchCleanedResult) -> BatchInsertStats:
        papers = list(batch.papers)
        rows = []
        for paper in papers:
            type_name = paper.raw.get("type") or paper.raw.get("type_crossref")
            type_id = self._id_cache["work_types"].get(type_name)
            venue_id = self._primary_venue_id(paper.raw)
            rows.append(
                (
                    paper.work_id,
                    paper.doi,
                    paper.title,
                    paper.abstract,
                    paper.publication_year,
                    paper.cited_by_count,
                    type_id,
                    venue_id,
                    paper.open_access_pdf_url,
                    paper.source,
                    _json(paper.raw),
                )
            )

        stats = self._bulk_insert_values(
            table_name="works",
            rows=rows,
            sql_head="""INSERT INTO works
                (openalex_id, doi, title, abstract, publication_year, cited_by_count,
                 type_id, primary_venue_id, open_access_pdf_url, source, raw_json)""",
            row_placeholder="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE
                doi=VALUES(doi), title=VALUES(title), abstract=VALUES(abstract),
                publication_year=VALUES(publication_year), cited_by_count=VALUES(cited_by_count),
                type_id=VALUES(type_id), primary_venue_id=VALUES(primary_venue_id),
                open_access_pdf_url=VALUES(open_access_pdf_url), source=VALUES(source), raw_json=VALUES(raw_json)""",
        )

        keys = [paper.work_id for paper in papers if paper.work_id]
        self._id_cache["works"].update(
            self._fetch_id_map(
                table="works",
                pk_col="work_id",
                key_col="openalex_id",
                keys=keys,
            )
        )
        stats.details["works"] = stats.inserted
        return stats

    # ------------------------------------------------------------------
    # Link/relationship tables
    # ------------------------------------------------------------------

    def _insert_work_authors(self, batch: BatchCleanedResult) -> BatchInsertStats:
        stats = BatchInsertStats()
        rows = []
        work_ids_for_lookup = set()

        for link in batch.work_authors:
            work_pk = self._id_cache["works"].get(link.work_id)
            author_pk = self._id_cache["authors"].get(link.author_id)
            if not work_pk or not author_pk:
                stats.skipped += 1
                continue
            rows.append(
                (
                    work_pk,
                    author_pk,
                    link.work_id,
                    link.author_id,
                    link.author_order,
                    link.is_corresponding,
                    _json(link.raw_author_position),
                )
            )
            work_ids_for_lookup.add(link.work_id)

        insert_stats = self._bulk_insert_values(
            table_name="work_authors",
            rows=rows,
            sql_head="""INSERT INTO work_authors
                (work_id_fk, author_id_fk, work_openalex_id, author_openalex_id,
                 author_order, is_corresponding, raw_author_position_json)""",
            row_placeholder="(%s, %s, %s, %s, %s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE
                is_corresponding=VALUES(is_corresponding),
                raw_author_position_json=VALUES(raw_author_position_json)""",
        )
        stats = self._merge_stats(stats, insert_stats)

        self._refresh_work_author_cache(list(work_ids_for_lookup))
        stats.details["work_authors"] = stats.inserted
        return stats

    def _insert_author_institutions(self, batch: BatchCleanedResult) -> BatchInsertStats:
        stats = BatchInsertStats()
        rows = []
        for link in batch.author_institutions:
            author_pk = self._id_cache["authors"].get(link.author_id)
            inst_pk = self._id_cache["institutions"].get(link.institution_id)
            if not author_pk or not inst_pk:
                stats.skipped += 1
                continue
            rows.append((author_pk, inst_pk, link.author_id, link.institution_id, link.relationship_source))

        insert_stats = self._bulk_insert_values(
            table_name="author_institutions",
            rows=rows,
            sql_head="""INSERT INTO author_institutions
                (author_id_fk, institution_id_fk, author_openalex_id,
                 institution_openalex_id, relationship_source)""",
            row_placeholder="(%s, %s, %s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE
                last_seen_at=NOW(3), relationship_source=VALUES(relationship_source)""",
        )
        stats = self._merge_stats(stats, insert_stats)
        stats.details["author_institutions"] = stats.inserted
        return stats

    def _insert_work_institutions(self, batch: BatchCleanedResult) -> BatchInsertStats:
        stats = BatchInsertStats()
        rows = []
        for link in batch.work_institutions:
            work_pk = self._id_cache["works"].get(link.work_id)
            inst_pk = self._id_cache["institutions"].get(link.institution_id)
            if not work_pk or not inst_pk:
                stats.skipped += 1
                continue
            rows.append((work_pk, inst_pk, link.work_id, link.institution_id, link.source))

        insert_stats = self._bulk_insert_values(
            table_name="work_institutions",
            rows=rows,
            sql_head="""INSERT INTO work_institutions
                (work_id_fk, institution_id_fk, work_openalex_id,
                 institution_openalex_id, source)""",
            row_placeholder="(%s, %s, %s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE source=VALUES(source)""",
        )
        stats = self._merge_stats(stats, insert_stats)
        stats.details["work_institutions"] = stats.inserted
        return stats

    def _insert_work_author_affiliations(self, batch: BatchCleanedResult) -> BatchInsertStats:
        stats = BatchInsertStats()
        rows = []
        for link in batch.work_author_affiliations:
            work_author_pk = self._id_cache["work_authors"].get((link.work_id, link.author_id, link.author_order))
            if not work_author_pk:
                work_author_pk = self._id_cache["work_authors"].get((link.work_id, link.author_id))
            inst_pk = self._id_cache["institutions"].get(link.institution_id)
            if not work_author_pk or not inst_pk:
                stats.skipped += 1
                continue
            rows.append((work_author_pk, inst_pk, link.institution_id, link.raw_affiliation_string))

        insert_stats = self._bulk_insert_values(
            table_name="work_author_affiliations",
            rows=rows,
            sql_head="""INSERT INTO work_author_affiliations
                (work_author_id, institution_id_fk, institution_openalex_id, raw_affiliation_string)""",
            row_placeholder="(%s, %s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE
                raw_affiliation_string=VALUES(raw_affiliation_string)""",
        )
        stats = self._merge_stats(stats, insert_stats)
        stats.details["work_author_affiliations"] = stats.inserted
        return stats

    def _insert_work_concepts(self, batch: BatchCleanedResult) -> BatchInsertStats:
        stats = BatchInsertStats()
        rows = []
        for link in batch.work_concepts:
            work_pk = self._id_cache["works"].get(link.work_id)
            concept_pk = self._id_cache["concepts"].get(link.concept_id)
            if not work_pk or not concept_pk:
                stats.skipped += 1
                continue
            rows.append((work_pk, concept_pk, link.work_id, link.concept_id, link.score, link.source))

        insert_stats = self._bulk_insert_values(
            table_name="work_concepts",
            rows=rows,
            sql_head="""INSERT INTO work_concepts
                (work_id_fk, concept_id_fk, work_openalex_id,
                 concept_openalex_id, score, source)""",
            row_placeholder="(%s, %s, %s, %s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE
                score=VALUES(score), source=VALUES(source)""",
        )
        stats = self._merge_stats(stats, insert_stats)
        stats.details["work_concepts"] = stats.inserted
        return stats

    def _insert_work_venues(self, batch: BatchCleanedResult) -> BatchInsertStats:
        stats = BatchInsertStats()
        rows = []
        for link in batch.work_venues:
            work_pk = self._id_cache["works"].get(link.work_id)
            venue_pk = self._id_cache["venues"].get(link.venue_id)
            if not work_pk or not venue_pk:
                stats.skipped += 1
                continue
            rows.append((work_pk, venue_pk, link.work_id, link.venue_id, link.is_primary))

        insert_stats = self._bulk_insert_values(
            table_name="work_venues",
            rows=rows,
            sql_head="""INSERT INTO work_venues
                (work_id_fk, venue_id_fk, work_openalex_id, venue_openalex_id, is_primary)""",
            row_placeholder="(%s, %s, %s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE is_primary=VALUES(is_primary)""",
        )
        stats = self._merge_stats(stats, insert_stats)
        stats.details["work_venues"] = stats.inserted
        return stats

    def _insert_citations(self, batch: BatchCleanedResult) -> BatchInsertStats:
        stats = BatchInsertStats()
        citation_rows = []
        external_rows = []
        seen_external = set()

        for link in batch.citations:
            citing_pk = self._id_cache["works"].get(link.citing_work_id)
            cited_pk = self._id_cache["works"].get(link.cited_work_id)
            citation_rows.append(
                (citing_pk, cited_pk, link.citing_work_id, link.cited_work_id, link.source)
            )
            if cited_pk is None and link.cited_work_id not in seen_external:
                external_rows.append((link.cited_work_id, link.citing_work_id, "citation"))
                seen_external.add(link.cited_work_id)

        insert_stats = self._bulk_insert_values(
            table_name="citations",
            rows=citation_rows,
            sql_head="""INSERT INTO citations
                (citing_work_id_fk, cited_work_id_fk,
                 citing_work_openalex_id, cited_work_openalex_id, source)""",
            row_placeholder="(%s, %s, %s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE
                citing_work_id_fk=VALUES(citing_work_id_fk),
                cited_work_id_fk=VALUES(cited_work_id_fk), source=VALUES(source)""",
        )
        stats = self._merge_stats(stats, insert_stats)

        # External references are auxiliary; count errors into the same stage, but
        # keep the citation detail focused on actual citation rows.
        external_stats = self._bulk_insert_values(
            table_name="external_work_refs",
            rows=external_rows,
            sql_head="""INSERT INTO external_work_refs (openalex_id, first_seen_from, source)""",
            row_placeholder="(%s, %s, %s)",
            update_clause="""ON DUPLICATE KEY UPDATE updated_at=NOW(3)""",
        )
        stats.errors += external_stats.errors
        stats.details["external_work_refs"] = external_stats.inserted
        stats.details["citations"] = insert_stats.inserted
        return stats

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _bulk_insert_values(
        self,
        *,
        table_name: str,
        rows: Sequence[Tuple[Any, ...]],
        sql_head: str,
        row_placeholder: str,
        update_clause: str,
    ) -> BatchInsertStats:
        """Execute multi-row INSERT ... ON DUPLICATE KEY UPDATE in chunks.

        If a chunk fails, fall back to row-wise execution for that chunk so that
        one bad row does not discard the whole stage and errors remain countable.
        """
        stats = BatchInsertStats()
        if not rows:
            return stats

        chunks = list(_chunked(list(rows), self._batch_size))
        pbar = None
        if self._show_inner_progress and tqdm is not None:
            pbar = tqdm(
                total=len(rows),
                desc=f"  {table_name}",
                unit="row",
                leave=False,
                dynamic_ncols=True,
                position=1,
                mininterval=0.5,
            )

        try:
            single_sql = f"{sql_head} VALUES {row_placeholder} {update_clause}"
            for chunk in chunks:
                placeholders = ", ".join([row_placeholder] * len(chunk))
                sql = f"{sql_head} VALUES {placeholders} {update_clause}"
                params = tuple(value for row in chunk for value in row)
                try:
                    self._repo._execute(sql, params)
                    stats.inserted += len(chunk)
                except Exception:
                    # Diagnostic fallback. It is slower, but only used on failing chunks.
                    for row in chunk:
                        try:
                            self._repo._execute(single_sql, row)
                            stats.inserted += 1
                        except Exception:
                            stats.errors += 1
                finally:
                    if pbar is not None:
                        pbar.update(len(chunk))
        finally:
            if pbar is not None:
                pbar.close()

        return stats

    def _fetch_id_map(
        self,
        *,
        table: str,
        pk_col: str,
        key_col: str,
        keys: Sequence[Any],
    ) -> Dict[Any, int]:
        """Fetch {external_key: integer_pk} mapping in chunks."""
        result: Dict[Any, int] = {}
        unique_keys = [key for key in dict.fromkeys(keys) if key is not None and key != ""]
        if not unique_keys:
            return result

        iterable = self._progress(
            list(_chunked(unique_keys, max(1, self._batch_size * 5))),
            total=(len(unique_keys) + self._batch_size * 5 - 1) // (self._batch_size * 5),
            desc=f"fetch {table} ids",
            unit="chunk",
        )

        for chunk in iterable:
            placeholders = ", ".join(["%s"] * len(chunk))
            sql = f"SELECT {pk_col}, {key_col} FROM {table} WHERE {key_col} IN ({placeholders})"
            cur = self._repo._execute(sql, tuple(chunk))
            for row in _fetch_all(cur):
                pk, key = _row_get(row, 0, pk_col), _row_get(row, 1, key_col)
                if key is not None and pk is not None:
                    result[key] = int(pk)

        return result

    def _refresh_work_author_cache(self, work_openalex_ids: Sequence[str]) -> None:
        unique_work_ids = [wid for wid in dict.fromkeys(work_openalex_ids) if wid]
        if not unique_work_ids:
            return

        iterable = self._progress(
            list(_chunked(unique_work_ids, max(1, self._batch_size * 5))),
            total=(len(unique_work_ids) + self._batch_size * 5 - 1) // (self._batch_size * 5),
            desc="fetch work_author ids",
            unit="chunk",
        )

        for chunk in iterable:
            placeholders = ", ".join(["%s"] * len(chunk))
            sql = f"""
                SELECT work_author_id, work_openalex_id, author_openalex_id, author_order
                FROM work_authors
                WHERE work_openalex_id IN ({placeholders})
            """
            cur = self._repo._execute(sql, tuple(chunk))
            for row in _fetch_all(cur):
                work_author_id = _row_get(row, 0, "work_author_id")
                work_id = _row_get(row, 1, "work_openalex_id")
                author_id = _row_get(row, 2, "author_openalex_id")
                author_order = _row_get(row, 3, "author_order")
                if work_author_id is None or not work_id or not author_id:
                    continue
                pk = int(work_author_id)
                self._id_cache["work_authors"][(work_id, author_id)] = pk
                self._id_cache["work_authors"][(work_id, author_id, author_order)] = pk

    def _progress(self, iterable: Iterable[Any], *, total: Optional[int], desc: str, unit: str = "row") -> Any:
        if self._show_inner_progress and tqdm is not None:
            return tqdm(
                iterable,
                total=total,
                desc=f"  {desc}",
                unit=unit,
                leave=False,
                dynamic_ncols=True,
                position=1,
                mininterval=0.5,
            )
        return iterable

    def _primary_venue_id(self, raw_work: Dict[str, Any]) -> Optional[int]:
        source = (raw_work.get("primary_location") or {}).get("source") or {}
        venue_openalex_id = _openalex_id(source.get("id"))
        if not venue_openalex_id:
            return None
        return self._id_cache["venues"].get(venue_openalex_id)

    def _upsert_returning_id(self, sql: str, params: Tuple[Any, ...]) -> int:
        """Compatibility helper retained for external callers/tests."""
        cur = self._repo._execute(sql, params)
        lastrowid = getattr(cur, "lastrowid", None)
        if lastrowid is None:
            return 0
        return int(lastrowid)

    @staticmethod
    def _merge_stats(acc: BatchInsertStats, cur: BatchInsertStats) -> BatchInsertStats:
        acc.inserted += cur.inserted
        acc.updated += cur.updated
        acc.skipped += cur.skipped
        acc.errors += cur.errors
        acc.details.update(cur.details)
        return acc


def _chunked(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    size = max(1, int(size))
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _fetch_all(cur: Any) -> List[Any]:
    fetchall = getattr(cur, "fetchall", None)
    if fetchall is None:
        return []
    rows = fetchall()
    return list(rows or [])


def _row_get(row: Any, idx: int, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row[idx]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _openalex_id(value: Any) -> str:
    if not value:
        return ""
    return str(value).rstrip("/").rsplit("/", 1)[-1]

