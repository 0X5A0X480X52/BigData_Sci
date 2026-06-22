"""Entity cleaners — convert OpenAlex raw JSON into structured dataclass instances.

Pattern: BaseCleaner[T] -> entity-specific extractors -> BatchCleaner for
coordinated batch processing with global deduplication.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Generic, List, Optional, TypeVar

from research_agent.core.models import Paper
from research_agent.core.utils import abstract_from_inverted_index, normalize_openalex_id


T = TypeVar("T")


@dataclass
class CountryEntity:
    country_code: str
    display_name: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkTypeEntity:
    type_name: str
    source: str = "openalex"
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthorEntity:
    openalex_id: str
    display_name: str
    orcid: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InstitutionEntity:
    openalex_id: str
    display_name: str
    type: Optional[str] = None
    country_code: Optional[str] = None
    ror: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConceptEntity:
    openalex_id: str
    display_name: str
    level: int = 0
    score: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VenueEntity:
    openalex_id: str
    display_name: str
    issn_l: Optional[str] = None
    issn: List[str] = field(default_factory=list)
    publisher: Optional[str] = None
    is_open_access: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CitationLink:
    citing_work_id: str
    cited_work_id: str
    source: str = "openalex_referenced_works"


@dataclass
class WorkAuthorLink:
    work_id: str
    author_id: str
    author_order: int = 0
    is_corresponding: bool = False
    raw_author_position: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthorInstitutionLink:
    author_id: str
    institution_id: str
    relationship_source: str = "openalex_authorship"


@dataclass
class WorkInstitutionLink:
    work_id: str
    institution_id: str
    source: str = "openalex_authorship"


@dataclass
class WorkAuthorAffiliationLink:
    work_id: str
    author_id: str
    institution_id: str
    author_order: int = 0
    raw_affiliation_string: Optional[str] = None


@dataclass
class WorkConceptLink:
    work_id: str
    concept_id: str
    score: float = 0.0
    source: str = "openalex"


@dataclass
class WorkVenueLink:
    work_id: str
    venue_id: str
    is_primary: bool = True


class BaseCleaner(Generic[T], ABC):
    """Abstract base for all entity cleaners."""

    @staticmethod
    def safe_get(obj: Dict[str, Any], *keys: str, default: Any = None) -> Any:
        for key in keys:
            if not isinstance(obj, dict):
                return default
            obj = obj.get(key, {})
        return obj if obj != {} else default

    @staticmethod
    def clean_string(value: Any, max_length: int = 2000) -> Optional[str]:
        if not isinstance(value, str) or not value.strip():
            return None
        return value.strip()[:max_length]

    @staticmethod
    def extract_openalex_id(url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        return url.rstrip("/").rsplit("/", 1)[-1].strip()

    @staticmethod
    def clean_orcid(raw: Optional[str]) -> Optional[str]:
        if not raw:
            return None
        raw = raw.strip()
        m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", raw)
        return f"https://orcid.org/{m.group(1)}" if m else None

    @abstractmethod
    def extract(self, raw: Dict[str, Any]) -> Optional[T]:
        ...


class WorkCleaner(BaseCleaner[Paper]):
    """Extract a Paper from OpenAlex Work JSON."""

    def extract(self, raw: Dict[str, Any]) -> Optional[Paper]:
        work_id = normalize_openalex_id(str(raw.get("id", "")))
        if not work_id:
            return None

        abstract = raw.get("abstract") or abstract_from_inverted_index(
            raw.get("abstract_inverted_index")
        )
        oa = raw.get("open_access") or {}
        return Paper(
            work_id=work_id,
            title=self.clean_string(raw.get("title"), max_length=1000) or "(untitled)",
            abstract=abstract or "",
            publication_year=raw.get("publication_year"),
            cited_by_count=int(raw.get("cited_by_count") or 0),
            authors=[
                self.clean_string(self.safe_get(item, "author", "display_name"), max_length=256) or ""
                for item in raw.get("authorships", [])
                if self.safe_get(item, "author", "display_name")
            ],
            topics=[
                self.clean_string(t.get("display_name"), max_length=256) or ""
                for t in (raw.get("topics") or raw.get("concepts") or [])
                if t.get("display_name")
            ],
            referenced_works=[normalize_openalex_id(ref) for ref in raw.get("referenced_works", [])],
            doi=self.clean_string(raw.get("doi")),
            open_access_pdf_url=oa.get("oa_url") or raw.get("pdf_url"),
            source="openalex",
            raw=raw,
        )


class AuthorCleaner(BaseCleaner[AuthorEntity]):
    def extract(self, raw: Dict[str, Any]) -> Optional[AuthorEntity]:
        author = self.safe_get(raw, "author")
        if not author or not isinstance(author, dict):
            return None
        author_id = self.extract_openalex_id(author.get("id"))
        name = self.clean_string(author.get("display_name"), max_length=256)
        if not author_id or not name:
            return None
        return AuthorEntity(
            openalex_id=author_id,
            display_name=name,
            orcid=self.clean_orcid(author.get("orcid")),
            raw=author,
        )


class InstitutionCleaner(BaseCleaner[InstitutionEntity]):
    def extract(self, raw: Dict[str, Any]) -> Optional[InstitutionEntity]:
        inst_id = self.extract_openalex_id(raw.get("id"))
        name = self.clean_string(raw.get("display_name"), max_length=512)
        if not inst_id or not name:
            return None
        return InstitutionEntity(
            openalex_id=inst_id,
            display_name=name,
            type=self.clean_string(raw.get("type"), max_length=64),
            country_code=self.clean_string(raw.get("country_code"), max_length=8),
            ror=self.clean_string(raw.get("ror"), max_length=128),
            raw=raw,
        )


class ConceptCleaner(BaseCleaner[ConceptEntity]):
    def extract(self, raw: Dict[str, Any]) -> Optional[ConceptEntity]:
        concept_id = self.extract_openalex_id(raw.get("id"))
        name = self.clean_string(raw.get("display_name"), max_length=256)
        if not concept_id or not name:
            return None
        return ConceptEntity(
            openalex_id=concept_id,
            display_name=name,
            level=int(raw.get("level") or 0),
            score=float(raw.get("score") or 0.0),
            raw=raw,
        )


class VenueCleaner(BaseCleaner[VenueEntity]):
    def extract(self, raw: Dict[str, Any]) -> Optional[VenueEntity]:
        venue_id = self.extract_openalex_id(raw.get("id"))
        name = self.clean_string(raw.get("display_name"), max_length=512)
        if not venue_id or not name:
            return None
        issn_value = raw.get("issn") or []
        if isinstance(issn_value, str):
            issn_list = [issn_value]
        elif isinstance(issn_value, list):
            issn_list = [str(item) for item in issn_value if item]
        else:
            issn_list = []
        return VenueEntity(
            openalex_id=venue_id,
            display_name=name,
            issn_l=self.clean_string(raw.get("issn_l"), max_length=32),
            issn=issn_list,
            publisher=self.clean_string(raw.get("publisher"), max_length=256),
            is_open_access=bool(raw.get("is_oa", False)),
            raw=raw,
        )


@dataclass
class BatchCleanedResult:
    papers: List[Paper] = field(default_factory=list)
    authors: Dict[str, AuthorEntity] = field(default_factory=dict)
    institutions: Dict[str, InstitutionEntity] = field(default_factory=dict)
    concepts: Dict[str, ConceptEntity] = field(default_factory=dict)
    venues: Dict[str, VenueEntity] = field(default_factory=dict)
    countries: Dict[str, CountryEntity] = field(default_factory=dict)
    work_types: Dict[str, WorkTypeEntity] = field(default_factory=dict)
    work_authors: List[WorkAuthorLink] = field(default_factory=list)
    author_institutions: List[AuthorInstitutionLink] = field(default_factory=list)
    work_institutions: List[WorkInstitutionLink] = field(default_factory=list)
    work_author_affiliations: List[WorkAuthorAffiliationLink] = field(default_factory=list)
    work_concepts: List[WorkConceptLink] = field(default_factory=list)
    work_venues: List[WorkVenueLink] = field(default_factory=list)
    citations: List[CitationLink] = field(default_factory=list)

    @property
    def total_entities(self) -> int:
        return (
            len(self.papers)
            + len(self.authors)
            + len(self.institutions)
            + len(self.concepts)
            + len(self.venues)
            + len(self.countries)
            + len(self.work_types)
        )


class BatchCleaner:
    """Coordinates cleaning of a batch of OpenAlex Works with global dedup."""

    def __init__(self) -> None:
        self.work_cleaner = WorkCleaner()
        self.author_cleaner = AuthorCleaner()
        self.institution_cleaner = InstitutionCleaner()
        self.concept_cleaner = ConceptCleaner()
        self.venue_cleaner = VenueCleaner()

    def process_batch(self, raw_works: List[Dict[str, Any]]) -> BatchCleanedResult:
        result = BatchCleanedResult()

        for raw_work in raw_works:
            paper = self.work_cleaner.extract(raw_work)
            if paper is None:
                continue
            result.papers.append(paper)
            work_id_norm = normalize_openalex_id(paper.work_id)

            work_type = raw_work.get("type") or raw_work.get("type_crossref")
            if isinstance(work_type, str) and work_type.strip():
                result.work_types.setdefault(work_type, WorkTypeEntity(type_name=work_type, raw={"type": work_type}))

            for idx, authorship in enumerate(raw_work.get("authorships", [])):
                author = self.author_cleaner.extract(authorship)
                if not author:
                    continue
                result.authors.setdefault(author.openalex_id, author)
                result.work_authors.append(
                    WorkAuthorLink(
                        work_id=work_id_norm,
                        author_id=author.openalex_id,
                        author_order=idx,
                        is_corresponding=bool(authorship.get("is_corresponding", False)),
                        raw_author_position={
                            "author_position": authorship.get("author_position"),
                            "is_corresponding": authorship.get("is_corresponding", False),
                        },
                    )
                )

                affiliation_strings = _affiliation_strings(authorship)
                for inst_raw in authorship.get("institutions", []):
                    inst = self.institution_cleaner.extract(inst_raw)
                    if not inst:
                        continue
                    result.institutions.setdefault(inst.openalex_id, inst)
                    if inst.country_code:
                        result.countries.setdefault(
                            inst.country_code,
                            CountryEntity(country_code=inst.country_code, raw={"country_code": inst.country_code}),
                        )
                    result.author_institutions.append(
                        AuthorInstitutionLink(author_id=author.openalex_id, institution_id=inst.openalex_id)
                    )
                    result.work_institutions.append(
                        WorkInstitutionLink(work_id=work_id_norm, institution_id=inst.openalex_id)
                    )
                    for aff in affiliation_strings or [None]:
                        result.work_author_affiliations.append(
                            WorkAuthorAffiliationLink(
                                work_id=work_id_norm,
                                author_id=author.openalex_id,
                                institution_id=inst.openalex_id,
                                author_order=idx,
                                raw_affiliation_string=aff,
                            )
                        )

            for con_raw in raw_work.get("topics", raw_work.get("concepts", [])):
                concept = self.concept_cleaner.extract(con_raw)
                if concept:
                    result.concepts.setdefault(concept.openalex_id, concept)
                    result.work_concepts.append(
                        WorkConceptLink(
                            work_id=work_id_norm,
                            concept_id=concept.openalex_id,
                            score=concept.score,
                            source="topics" if raw_work.get("topics") else "concepts",
                        )
                    )

            source = (raw_work.get("primary_location") or {}).get("source") or {}
            if source:
                venue = self.venue_cleaner.extract(source)
                if venue:
                    result.venues.setdefault(venue.openalex_id, venue)
                    result.work_venues.append(
                        WorkVenueLink(work_id=work_id_norm, venue_id=venue.openalex_id, is_primary=True)
                    )

            for ref_id in paper.referenced_works:
                result.citations.append(
                    CitationLink(citing_work_id=work_id_norm, cited_work_id=ref_id)
                )

        _dedupe_links(result)
        return result

    def clean_single(self, raw_work: Dict[str, Any]) -> Optional[Paper]:
        return self.work_cleaner.extract(raw_work)


def _affiliation_strings(authorship: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    raw = authorship.get("raw_affiliation_strings") or []
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list):
        values.extend(str(item).strip() for item in raw if str(item).strip())
    for aff in authorship.get("affiliations", []) or []:
        raw_aff = aff.get("raw_affiliation_string") if isinstance(aff, dict) else None
        if raw_aff:
            values.append(str(raw_aff).strip())
    return list(dict.fromkeys(values))


def _dedupe_links(result: BatchCleanedResult) -> None:
    result.work_authors = _unique(result.work_authors, lambda x: (x.work_id, x.author_id, x.author_order))
    result.author_institutions = _unique(result.author_institutions, lambda x: (x.author_id, x.institution_id))
    result.work_institutions = _unique(result.work_institutions, lambda x: (x.work_id, x.institution_id))
    result.work_author_affiliations = _unique(
        result.work_author_affiliations,
        lambda x: (x.work_id, x.author_id, x.institution_id, x.raw_affiliation_string or ""),
    )
    result.work_concepts = _unique(result.work_concepts, lambda x: (x.work_id, x.concept_id))
    result.work_venues = _unique(result.work_venues, lambda x: (x.work_id, x.venue_id))
    result.citations = _unique(result.citations, lambda x: (x.citing_work_id, x.cited_work_id))


def _unique(items: List[Any], key_fn: Any) -> List[Any]:
    seen = set()
    output = []
    for item in items:
        key = key_fn(item)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output

