from research_agent.data.openalex_source import _iter_page_results


class FakeOpenAlexResponseList:
    def __iter__(self):
        yield {"id": "https://openalex.org/W1", "title": "A"}
        yield {"id": "https://openalex.org/W2", "title": "B"}


def test_iter_page_results_supports_response_list_shape():
    rows = list(_iter_page_results(FakeOpenAlexResponseList()))
    assert [row["id"] for row in rows] == ["https://openalex.org/W1", "https://openalex.org/W2"]


def test_iter_page_results_supports_dict_page_shape():
    rows = list(_iter_page_results({"results": [{"id": "W1"}, {"id": "W2"}]}))
    assert [row["id"] for row in rows] == ["W1", "W2"]


def test_scholarly_service_falls_back_when_openalex_source_fails():
    from research_agent.core.artifact_store import ArtifactStore
    from research_agent.core.models import RunConfig
    from research_agent.services.scholarly_data import ScholarlyDataService

    class FailingOpenAlex:
        def search_works(self, query, max_results):
            raise RuntimeError("openalex boom")

    service = ScholarlyDataService(
        ArtifactStore("outputs/test_artifacts/openalex_fallback", "run"),
        RunConfig(max_field_corpus=5),
        openalex_source=FailingOpenAlex(),
    )
    corpus = service.create_field_corpus("transformer", max_results=5)

    assert corpus.papers
    assert "fell back to fixture" in corpus.warnings[0]

