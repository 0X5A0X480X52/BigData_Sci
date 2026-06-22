# -*- coding: utf-8 -*-
"""
数据模型模块

定义与数据库表对应的实体类。
"""

from .entities import (
    WorkEntity,
    AuthorEntity,
    InstitutionEntity,
    ConceptEntity,
    VenueEntity,
    CountryEntity,
    WorkTypeEntity,
    WorkAuthorInstitution,
    WorkConcept,
    WorkVenue,
    WorkDatabase,
    CitationEntity,
)

__all__ = [
    "WorkEntity",
    "AuthorEntity",
    "InstitutionEntity",
    "ConceptEntity",
    "VenueEntity",
    "CountryEntity",
    "WorkTypeEntity",
    "WorkAuthorInstitution",
    "WorkConcept",
    "WorkVenue",
    "WorkDatabase",
    "CitationEntity",
]
