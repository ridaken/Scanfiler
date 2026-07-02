from scanfiler.ai.schema import Decision, build_response_format


def test_enum_constrained_when_new_disallowed():
    rf = build_response_format(["A", "B"], allow_new=False)
    subdir = rf["json_schema"]["schema"]["properties"]["subdir"]
    assert subdir == {"type": "string", "enum": ["A", "B"]}


def test_open_string_when_new_allowed():
    rf = build_response_format(["A", "B"], allow_new=True)
    subdir = rf["json_schema"]["schema"]["properties"]["subdir"]
    assert subdir == {"type": "string"}
    assert rf["json_schema"]["strict"] is True


def test_open_string_when_no_existing_subdirs():
    rf = build_response_format([], allow_new=False)
    assert rf["json_schema"]["schema"]["properties"]["subdir"] == {"type": "string"}


def test_decision_defaults():
    d = Decision(filename="X", subdir="Y", confidence=0.5)
    assert d.is_new_subdir is False
    assert d.tags == []
    assert d.date is None


def test_all_fields_required():
    schema = build_response_format(["A"], allow_new=True)["json_schema"]["schema"]
    # Every property is required (forces doc_type/summary; OpenAI strict-compliant).
    assert set(schema["required"]) == set(schema["properties"])
    assert "doc_type" in schema["required"]
    assert "summary" in schema["required"]


def test_doctype_and_summary_are_non_empty():
    props = build_response_format([], allow_new=True)["json_schema"]["schema"]["properties"]
    assert props["doc_type"]["minLength"] == 1
    assert props["summary"]["minLength"] == 1
    # date stays nullable, tags may be empty
    assert props["date"] == {"type": ["string", "null"]}
    assert "minItems" not in props["tags"]
