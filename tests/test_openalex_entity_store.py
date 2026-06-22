import re
from types import SimpleNamespace

from research_agent.data.cleaners import BatchCleaner
from research_agent.persistence.mysql_inserter import MySQLInserter
from research_agent.persistence.mysql_repository import _DDL_STATEMENTS


def sample_work():
    return {
        "id": "https://openalex.org/W1",
        "title": "Sample Work",
        "abstract": "A sample abstract",
        "publication_year": 2024,
        "cited_by_count": 7,
        "doi": "https://doi.org/10.1/sample",
        "type": "article",
        "open_access": {"oa_url": "https://example.org/paper.pdf"},
        "authorships": [
            {
                "author_position": "first",
                "is_corresponding": True,
                "raw_affiliation_strings": ["Lab A, University One"],
                "author": {
                    "id": "https://openalex.org/A1",
                    "display_name": "Author One",
                    "orcid": "https://orcid.org/0000-0001-0002-0003",
                },
                "institutions": [
                    {
                        "id": "https://openalex.org/I1",
                        "display_name": "University One",
                        "type": "education",
                        "country_code": "US",
                        "ror": "https://ror.org/abc",
                    },
                    {
                        "id": "https://openalex.org/I2",
                        "display_name": "Institute Two",
                        "type": "facility",
                        "country_code": "GB",
                    },
                ],
            },
            {
                "author_position": "last",
                "author": {"id": "https://openalex.org/A2", "display_name": "Author Two"},
                "institutions": [
                    {"id": "https://openalex.org/I1", "display_name": "University One", "country_code": "US"}
                ],
            },
        ],
        "topics": [{"id": "https://openalex.org/C1", "display_name": "Machine learning", "score": 0.9}],
        "primary_location": {
            "source": {
                "id": "https://openalex.org/V1",
                "display_name": "Journal One",
                "issn_l": "1234-5678",
                "issn": ["1234-5678"],
                "publisher": "Publisher",
                "is_oa": True,
            }
        },
        "referenced_works": ["https://openalex.org/W2"],
    }


def test_batch_cleaner_splits_authorship_into_binary_relations():
    batch = BatchCleaner().process_batch([sample_work()])

    assert len(batch.papers) == 1
    assert len(batch.work_authors) == 2
    assert {(x.author_id, x.institution_id) for x in batch.author_institutions} == {
        ("A1", "I1"), ("A1", "I2"), ("A2", "I1")
    }
    assert {(x.work_id, x.institution_id) for x in batch.work_institutions} == {
        ("W1", "I1"), ("W1", "I2")
    }
    assert len(batch.work_author_affiliations) >= 3
    assert "US" in batch.countries
    assert "article" in batch.work_types


def test_schema_contains_binary_relations_without_legacy_triple_table():
    ddl = "\n".join(_DDL_STATEMENTS)

    assert "work_author_institutions" not in ddl
    for table in ("work_authors", "author_institutions", "work_institutions", "work_author_affiliations"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
    assert "openalex_id" in ddl
    assert "work_openalex_id" in ddl
    assert "institution_openalex_id" in ddl


class FakeCursor:
    def __init__(self, rows=None, lastrowid=None):
        self._rows = list(rows or [])
        self.lastrowid = lastrowid

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeRepo:
    def __init__(self):
        self.sql = []
        self.next_id = 1
        self.memberships = []
        self.tables = {
            "countries": {},
            "work_types": {},
            "institutions": {},
            "authors": {},
            "venues": {},
            "concepts": {},
            "works": {},
            "work_authors": [],
        }

    def _execute(self, sql, params=()):
        normalized = re.sub(r"\s+", " ", sql.strip())
        self.sql.append((normalized, params))
        upper = normalized.upper()
        if upper.startswith("INSERT INTO"):
            return self._handle_insert(normalized, params)
        if upper.startswith("SELECT"):
            return self._handle_select(normalized, params)
        return FakeCursor(lastrowid=self.next_id)

    def _handle_insert(self, sql, params):
        table = re.search(r"INSERT INTO\s+([a-z_]+)", sql, re.I)
        table_name = table.group(1) if table else ""
        if table_name == "countries":
            self.tables["countries"][params[0]] = {"country_code": params[0], "display_name": params[1]}
        elif table_name == "work_types":
            self.tables["work_types"][params[0]] = {"type_id": self.next_id, "type_name": params[0]}
        elif table_name == "institutions":
            self.tables["institutions"][params[0]] = {"institution_id": self.next_id, "openalex_id": params[0]}
        elif table_name == "authors":
            self.tables["authors"][params[0]] = {"author_id": self.next_id, "openalex_id": params[0]}
        elif table_name == "venues":
            self.tables["venues"][params[0]] = {"venue_id": self.next_id, "openalex_id": params[0]}
        elif table_name == "concepts":
            self.tables["concepts"][params[0]] = {"concept_id": self.next_id, "openalex_id": params[0]}
        elif table_name == "works":
            self.tables["works"][params[0]] = {"work_id": self.next_id, "openalex_id": params[0]}
        elif table_name == "work_authors":
            self.tables["work_authors"].append(
                {
                    "work_author_id": self.next_id,
                    "work_openalex_id": params[2],
                    "author_openalex_id": params[3],
                    "author_order": params[4],
                }
            )
        self.next_id += 1
        return FakeCursor(lastrowid=self.next_id - 1)

    def _handle_select(self, sql, params):
        if "FROM work_types" in sql:
            keys = set(params)
            rows = [
                {"type_id": row["type_id"], "type_name": row["type_name"]}
                for key, row in self.tables["work_types"].items()
                if key in keys
            ]
            return FakeCursor(rows=rows)
        if "FROM institutions" in sql:
            keys = set(params)
            rows = [
                {"institution_id": row["institution_id"], "openalex_id": row["openalex_id"]}
                for key, row in self.tables["institutions"].items()
                if key in keys
            ]
            return FakeCursor(rows=rows)
        if "FROM authors" in sql:
            keys = set(params)
            rows = [
                {"author_id": row["author_id"], "openalex_id": row["openalex_id"]}
                for key, row in self.tables["authors"].items()
                if key in keys
            ]
            return FakeCursor(rows=rows)
        if "FROM venues" in sql:
            keys = set(params)
            rows = [
                {"venue_id": row["venue_id"], "openalex_id": row["openalex_id"]}
                for key, row in self.tables["venues"].items()
                if key in keys
            ]
            return FakeCursor(rows=rows)
        if "FROM concepts" in sql:
            keys = set(params)
            rows = [
                {"concept_id": row["concept_id"], "openalex_id": row["openalex_id"]}
                for key, row in self.tables["concepts"].items()
                if key in keys
            ]
            return FakeCursor(rows=rows)
        if "FROM works" in sql and "work_openalex_id" not in sql:
            keys = set(params)
            rows = [
                {"work_id": row["work_id"], "openalex_id": row["openalex_id"]}
                for key, row in self.tables["works"].items()
                if key in keys
            ]
            return FakeCursor(rows=rows)
        if "FROM work_authors" in sql:
            keys = set(params)
            rows = [
                {
                    "work_author_id": row["work_author_id"],
                    "work_openalex_id": row["work_openalex_id"],
                    "author_openalex_id": row["author_openalex_id"],
                    "author_order": row["author_order"],
                }
                for row in self.tables["work_authors"]
                if row["work_openalex_id"] in keys
            ]
            return FakeCursor(rows=rows)
        return FakeCursor(rows=[])

    def upsert_corpus_membership(self, corpus_id, work_id, source):
        self.memberships.append((corpus_id, work_id, source))


def test_mysql_inserter_writes_split_relation_tables():
    batch = BatchCleaner().process_batch([sample_work()])
    repo = FakeRepo()
    stats = MySQLInserter(repo).insert_batch(batch, corpus_id="C1", membership_source="field_query")
    sql_text = "\n".join(sql for sql, _ in repo.sql)

    assert stats.errors == 0
    assert "INSERT INTO work_authors" in sql_text
    assert "INSERT INTO author_institutions" in sql_text
    assert "INSERT INTO work_institutions" in sql_text
    assert "INSERT INTO work_author_affiliations" in sql_text
    assert "INSERT INTO citations" in sql_text
    assert "INSERT INTO external_work_refs" in sql_text
    assert repo.memberships == [("C1", "W1", "field_query")]
