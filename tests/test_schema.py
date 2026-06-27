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
