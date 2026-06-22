from research_agent.runtime.runner import _neo4j_safe_records


def test_neo4j_safe_records_stringifies_nested_maps():
    rows = _neo4j_safe_records([
        {
            "id": "W1",
            "type": "paper",
            "title": "A paper",
            "year": 2024,
            "nested": {"bad": "map"},
            "tags": ["a", "b", {"ignored": True}],
        }
    ], required_keys=("id", "type"))

    assert rows[0]["id"] == "W1"
    assert rows[0]["type"] == "paper"
    assert rows[0]["props"]["title"] == "A paper"
    assert rows[0]["props"]["year"] == 2024
    assert "nested" not in rows[0]["props"]
    assert "properties_json" in rows[0]
    assert isinstance(rows[0]["properties_json"], str)
    assert '"nested"' in rows[0]["properties_json"]

    # Verify the top-level output is Neo4j-safe: every top-level value must be
    # a primitive (str/int/float/bool), a list of primitives, or a dict whose
    # values are all primitives (for SET n += node.props usage).
    neo4j_primitive = (str, int, float, bool, type(None))
    for key, value in rows[0].items():
        if key == "props":
            # props is consumed via SET n += node.props — its values must be primitives
            assert isinstance(value, dict), f"props must be a dict, got {type(value)}"
            for pk, pv in value.items():
                assert isinstance(pv, neo4j_primitive) or (
                    isinstance(pv, list) and all(isinstance(i, neo4j_primitive) for i in pv)
                ), f"props.{pk} = {pv!r} is not Neo4j-safe"
        elif key == "properties_json":
            assert isinstance(value, str), f"properties_json must be str, got {type(value)}"
        else:
            assert isinstance(value, neo4j_primitive), (
                f"top-level key {key!r} = {value!r} must be a primitive for Neo4j safety"
            )
