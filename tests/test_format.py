import pytest

from apbot.ui.format import ZERO_WIDTH_SPACE, chunk_lines, clip, escape, fit_lines, progress, safe_text, ts


def test_escape_makes_markdown_inert():
    assert escape("*a* _b_ ~c~ `d` |e|") == r"\*a\* \_b\_ \~c\~ \`d\` \|e\|"


def test_escape_breaks_mentions():
    for text in ("@everyone", "@here", "<@123456>", "<@&99>", "<#42>"):
        out = escape(text)
        assert "@everyone" not in out and "@here" not in out and "<@" not in out and "<#" not in out
        assert ZERO_WIDTH_SPACE in out


def test_escape_keeps_markup_that_only_works_at_a_line_start_from_working():
    for text in ("# heading", "> quote", "- item", "1. item"):
        assert escape(text)[0] in ("\\", ZERO_WIDTH_SPACE)


def test_escape_makes_one_line_out_of_many():
    assert escape("a\n\n  b\tc") == "a b c"


def test_clip():
    assert clip("abc", 3) == "abc"
    assert clip("abcd", 3) == "ab…"
    assert clip("abcd", 1) == "…"


def test_safe_text_cuts_before_escaping_so_no_backslash_dangles():
    out = safe_text("*" * 50, 10)
    assert out == r"\*" * 9 + "…"


def test_ts():
    assert ts(1700000000.9, "R") == "<t:1700000000:R>"
    with pytest.raises(ValueError):
        ts(1, "x")


def test_progress():
    assert progress(12, 340) == "12/340 (3%)"
    assert progress(0, 0) == "0/0"


def test_fit_lines_reports_how_many_were_left_out():
    assert fit_lines(["aaa", "bbb", "ccc"], 7) == (["aaa", "bbb"], 1)
    assert fit_lines(["aaa", "bbb", "ccc"], 100) == (["aaa", "bbb", "ccc"], 0)
    assert fit_lines(["toolong"], 3) == ([], 1)
    assert fit_lines([], 10) == ([], 0)


def test_chunk_lines_never_exceeds_the_limit_and_keeps_lines_whole():
    lines = [f"line {i:02d}" for i in range(10)]  # 7 characters each
    chunks = chunk_lines(lines, 20)
    assert all(len(c) <= 20 for c in chunks)
    assert "\n".join(chunks).split("\n") == lines
    assert chunk_lines([], 20) == []


def test_chunk_lines_cuts_a_line_longer_than_the_limit():
    assert chunk_lines(["x" * 50], 10) == ["x" * 9 + "…"]


def test_escape_leaves_version_numbers_readable():
    assert escape("0.6.8") == "0.6.8"
