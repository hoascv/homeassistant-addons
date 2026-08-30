"""The briefing renderer, and the one thing it must never do.

Briefings come from whatever assistant the user pasted from, and the rendered
output is inserted into the page with innerHTML. That makes this the only place
in this add-on where a mistake means script execution in somebody's Home
Assistant session, so the injection tests below matter more than the formatting
ones and are deliberately first.

The design being tested is the ordering: everything is escaped before any rule
runs, so no tag in the source can survive to become a tag in the output. These
tests are what stops a future "just allow a bit of HTML" change slipping past.
"""
import pytest

import markdown


# --- the part that must not break ---------------------------------------------


@pytest.mark.parametrize("payload", [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "<a href='javascript:alert(1)'>click</a>",
    "<iframe src='//evil'></iframe>",
    "<style>body{display:none}</style>",
    "<svg/onload=alert(1)>",
    "<body onload=alert(1)>",
    "<!--<script>alert(1)</script>-->",
])
def test_no_html_in_a_briefing_ever_becomes_a_tag(payload):
    """The whole security model in one assertion: escape first, add markup after.
    Nothing the user pasted can reach the DOM as an element."""
    out = markdown.render(payload)
    assert "<script" not in out.lower()
    assert "<img" not in out.lower()
    assert "<iframe" not in out.lower()
    assert "onerror" not in out.lower() or "&lt;" in out
    assert "&lt;" in out, "the payload should be visible as escaped text"


def test_html_inside_a_code_fence_is_also_escaped():
    """A fence preserves content verbatim, which must not mean 'unescaped'."""
    out = markdown.render("```\n<script>alert(1)</script>\n```")
    assert "<script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out


def test_html_inside_inline_code_is_also_escaped():
    out = markdown.render("Use `<div>` for that")
    assert "<div>" not in out
    assert "&lt;div&gt;" in out


def test_a_quote_character_cannot_break_out_of_an_attribute():
    """The only attributes emitted are class names built from a restricted
    pattern, but the escaping is what actually guarantees it."""
    out = markdown.render('```js"onload="alert(1)\ncode\n```')
    assert 'onload="alert' not in out
    assert "&quot;" in out or 'class="lang-' not in out


def test_a_language_label_that_is_not_a_plain_word_produces_no_class():
    assert 'class="lang-' not in markdown.render("```not a language\nx\n```")
    assert 'class="lang-python"' in markdown.render("```python\nx\n```")


# --- code fences, where the diagrams live -------------------------------------


def test_a_fence_preserves_spacing_exactly():
    """Box-drawing only lines up if nothing touches the whitespace — this is the
    reason the feature exists."""
    diagram = "┌────────┐\n│ Driver │\n└────────┘"
    out = markdown.render(f"```\n{diagram}\n```")
    assert diagram in out
    assert "<pre" in out and "<code" in out


def test_a_fence_is_not_treated_as_prose():
    out = markdown.render("```\n# not a heading\n- not a list\n```")
    assert "<h4" not in out
    assert "<ul" not in out
    assert "# not a heading" in out


def test_an_unterminated_fence_runs_to_the_end_rather_than_eating_everything():
    out = markdown.render("intro\n\n```\nstill code")
    assert "<p" in out
    assert "still code" in out


# --- headings -----------------------------------------------------------------


def test_headings_start_below_the_pages_own_levels():
    """A briefing dropping an h2 into a card would outrank the card's title."""
    out = markdown.render("# One\n\n## Two\n\n### Three")
    assert "<h1" not in out and "<h2" not in out and "<h3" not in out
    assert "<h4" in out and "<h5" in out


def test_a_hash_without_a_space_is_not_a_heading():
    assert "<h4" not in markdown.render("#hashtag")


# --- lists --------------------------------------------------------------------


def test_a_bulleted_list_becomes_a_ul():
    out = markdown.render("- first\n- second\n- third")
    assert out.count("<li>") == 3
    assert "<ul" in out


@pytest.mark.parametrize("source", ["1. one\n2. two", "1) one\n2) two"])
def test_a_numbered_list_becomes_an_ol(source):
    out = markdown.render(source)
    assert "<ol" in out
    assert out.count("<li>") == 2


def test_a_wrapped_list_item_stays_one_item():
    """Assistants wrap long bullets; two <li> for one bullet reads as a bug."""
    out = markdown.render("- a bullet that carries on\n  onto the next line\n- second")
    assert out.count("<li>") == 2
    assert "carries on onto the next line" in out


# --- tables -------------------------------------------------------------------


def test_a_pipe_table_becomes_a_table():
    out = markdown.render(
        "| Symptom | Cause |\n|---|---|\n| Slow stage | Skew |\n| OOM | Wide rows |"
    )
    assert "<table" in out
    assert out.count("<th>") == 2
    assert out.count("<tr>") == 3  # header plus two body rows
    assert "Skew" in out


def test_pipes_without_a_divider_are_not_a_table():
    out = markdown.render("| this | is | prose |")
    assert "<table" not in out


# --- inline -------------------------------------------------------------------


def test_bold_and_italic():
    out = markdown.render("**bold** and *italic*")
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out


def test_inline_code_is_not_re_scanned_for_emphasis():
    """`**kwargs` is a Python idiom, not an attempt at bold, and the subject
    here is frequently code."""
    out = markdown.render("pass `**kwargs` through")
    assert "<strong>" not in out
    assert "**kwargs" in out


def test_an_asterisk_inside_a_word_is_not_italic():
    out = markdown.render("the file*name pattern")
    assert "<em>" not in out


def test_underscores_are_left_alone():
    """snake_case identifiers are everywhere in this material; treating _ as
    emphasis would mangle half the code references."""
    out = markdown.render("call data_interval_start now")
    assert "<em>" not in out
    assert "data_interval_start" in out


# --- paragraphs and edges -----------------------------------------------------


def test_blank_lines_separate_paragraphs():
    out = markdown.render("first para\n\nsecond para")
    assert out.count("<p") == 2


def test_a_wrapped_paragraph_is_joined():
    out = markdown.render("a sentence that was\nwrapped by the assistant")
    assert out.count("<p") == 1
    assert "was wrapped" in out


@pytest.mark.parametrize("value", ["", None])
def test_empty_input_is_empty_output(value):
    assert markdown.render(value) == ""


def test_plain_prose_survives_unchanged():
    out = markdown.render("Just an ordinary sentence.")
    assert "Just an ordinary sentence." in out


def test_a_realistic_briefing_renders_every_construct():
    out = markdown.render(
        "Intro line.\n\n"
        "## A heading\n\n"
        "Some **bold** and `code`.\n\n"
        "- one\n- two\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "```python\nprint('hi')\n```\n"
    )
    for fragment in ("<h4", "<strong>", "<code", "<ul", "<table", "<pre"):
        assert fragment in out, fragment


# --- block quotes -------------------------------------------------------------


def test_a_quote_becomes_a_blockquote():
    out = markdown.render("> a caution worth setting apart")
    assert "<blockquote" in out
    assert "a caution worth setting apart" in out


def test_consecutive_quote_lines_join_into_one():
    out = markdown.render("> first line\n> second line")
    assert out.count("<blockquote") == 1
    assert "first line second line" in out


def test_a_quote_does_not_swallow_the_prose_after_it():
    out = markdown.render("> quoted\n\nordinary paragraph")
    assert "<blockquote" in out and "<p" in out
    assert "ordinary paragraph" in out

