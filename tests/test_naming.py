from scanfiler.naming import (
    normalize_extension,
    resolve_collision,
    sanitize_component,
    sanitize_subdir,
    with_date_prefix,
)


def test_sanitize_strips_invalid_chars():
    assert sanitize_component('in/valid:name?*"') == "invalidname"


def test_sanitize_reserved_name_guarded():
    assert sanitize_component("CON").startswith("_")
    assert sanitize_component("nul.pdf").startswith("_")


def test_sanitize_trailing_dots_and_spaces():
    assert sanitize_component("  report.  ") == "report"


def test_sanitize_length_cap():
    out = sanitize_component("a" * 500, max_len=10)
    assert len(out) <= 10


def test_sanitize_empty_falls_back():
    assert sanitize_component("///") == "document"


def test_subdir_blocks_traversal():
    assert sanitize_subdir("../../etc") == "etc"
    assert sanitize_subdir("/abs/Receipts") == "abs/Receipts"
    assert sanitize_subdir("..") == "_Unsorted"


def test_extension_normalized_and_preserved():
    assert normalize_extension(".PDF") == ".pdf"
    assert normalize_extension("JPG") == ".jpg"
    assert normalize_extension("") == ""


def test_date_prefix():
    assert with_date_prefix("Invoice", "2025-06-15", True) == "2025-06-Invoice"
    assert with_date_prefix("Invoice", "2025", True) == "2025-Invoice"
    assert with_date_prefix("Invoice", None, True) == "Invoice"
    assert with_date_prefix("Invoice", "2025-06", False) == "Invoice"


def test_collision_suffix():
    taken = {"invoice.pdf"}
    assert resolve_collision("Invoice", ".pdf", taken, "suffix") == "Invoice-2.pdf"
    taken.add("invoice-2.pdf")
    assert resolve_collision("Invoice", ".pdf", taken, "suffix") == "Invoice-3.pdf"


def test_collision_skip_returns_none():
    assert resolve_collision("Invoice", ".pdf", {"invoice.pdf"}, "skip") is None
