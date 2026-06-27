import pytest

from scanfiler.proposals import Proposal, read_proposals, write_proposals


def _p(**over):
    base = dict(file_hash="h1", original_path="/in/SCAN0001.pdf", subdir="Receipts",
                new_filename="Receipt.pdf")
    base.update(over)
    return Proposal(**base)


def test_roundtrip(tmp_path):
    path = tmp_path / "p.jsonl"
    items = [_p(), _p(file_hash="h2", tags=["a", "b"], confidence=0.9)]
    write_proposals(path, items)
    back = list(read_proposals(path))
    assert back == items


def test_skips_blank_and_comment_lines(tmp_path):
    path = tmp_path / "p.jsonl"
    write_proposals(path, [_p()])
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n# a hand comment\n")
    assert len(list(read_proposals(path))) == 1


def test_invalid_json_raises_with_line_number(tmp_path):
    path = tmp_path / "p.jsonl"
    path.write_text('{"file_hash": "h1"\n', encoding="utf-8")  # truncated JSON
    with pytest.raises(ValueError, match=":1:"):
        list(read_proposals(path))
