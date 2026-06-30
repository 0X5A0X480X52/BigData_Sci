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



def test_openalex_source_uses_select_cache_and_conservative_per_page():
    import shutil
    import uuid

    from research_agent.core.config import OpenAlexConfig
    from research_agent.data.openalex_source import OpenAlexSource

    class FakeWorks:
        def __init__(self, calls):
            self.calls = calls
            self.params = {}

        def select(self, fields):
            self.calls.append(("select", fields))
            return self

        def search(self, query):
            self.calls.append(("search", query))
            return self

        def filter(self, **kwargs):
            self.calls.append(("filter", kwargs))
            return self

        def paginate(self, per_page=None, n_max=None):
            self.calls.append(("paginate", per_page, n_max))
            return [[{"id": "https://openalex.org/W1", "title": "A"}]]

        def __getitem__(self, key):
            self.calls.append(("getitem", key))
            return {"id": key, "title": "A"}

    class FakePyAlex:
        def __init__(self):
            self.calls = []

        def Works(self):
            return FakeWorks(self.calls)

    cache_dir = f"outputs/test_artifacts/openalex_source_cache_{uuid.uuid4().hex}"
    shutil.rmtree(cache_dir, ignore_errors=True)
    fake = FakePyAlex()
    source = OpenAlexSource(OpenAlexConfig(cache_dir=cache_dir, rate_limit_per_second=0))
    source._pyalex = fake

    rows = list(source.search_works("bert", max_results=1, select_fields="id,title"))
    citing = list(source.get_citing_works("W1", max_results=1, select_fields="id"))
    work = source.get_work("W1", select_fields="id,title")

    assert rows[0]["id"] == "https://openalex.org/W1"
    assert citing[0]["id"] == "https://openalex.org/W1"
    assert work["id"] == "https://openalex.org/W1"
    assert ("select", "id,title") in fake.calls
    assert ("select", "id") in fake.calls
    assert all(call[1] == 100 for call in fake.calls if call[0] == "paginate")
    assert source.stats["requests"] >= 3
