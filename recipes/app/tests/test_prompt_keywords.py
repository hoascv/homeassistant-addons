"""Steering a pack towards ingredients you have.

The prompt had a `theme` parameter no page ever set, and it rendered as
"Theme: chicken." — which is a hint rather than an instruction, and a model
given a hint returns a batch that mostly ignores it.

What replaces it says two things, because they are different instructions and a
model given only the first puts every ingredient into every dish: each recipe
leans on at least one, and the batch between them covers the lot. That is what
"give me chicken and broccoli recipes" means to a person.
"""
import pytest

import prompts


# --- reading what was typed ---------------------------------------------------


def test_nothing_typed_is_no_keywords():
    assert prompts.parse_keywords("") == []
    assert prompts.parse_keywords(None) == []
    assert prompts.parse_keywords("   ,  , ") == []


def test_commas_separate_and_spaces_do_not():
    """"minced beef" and "sweet potato" are single ingredients. Splitting on
    spaces would ask for four things, two of which are not ingredients."""
    assert prompts.parse_keywords("minced beef, sweet potato") == [
        "minced beef", "sweet potato"]


def test_padding_and_inner_runs_are_tidied():
    assert prompts.parse_keywords("  chicken  breast , broccoli  ") == [
        "chicken breast", "broccoli"]


def test_newlines_count_as_separators():
    """Pasted from a note or a shopping list."""
    assert prompts.parse_keywords("chicken\nbroccoli\nrice") == [
        "chicken", "broccoli", "rice"]


def test_repeats_are_dropped_keeping_the_first_spelling():
    """Somebody typing "Chicken, chicken" means one thing, not two — and the
    spelling they typed first is the one they meant to see."""
    assert prompts.parse_keywords("Chicken, chicken, CHICKEN") == ["Chicken"]


def test_too_many_keywords_are_capped():
    """Past a dozen the request stops being "build around these" and becomes a
    list the model quietly picks from, which reads as it ignoring the ask."""
    many = ", ".join(f"thing {n}" for n in range(30))
    assert len(prompts.parse_keywords(many)) == prompts.MAX_KEYWORDS


def test_an_essay_pasted_into_the_box_is_truncated_not_passed_on():
    [word] = prompts.parse_keywords("x" * 500)
    assert len(word) == prompts.MAX_KEYWORD_LENGTH


# --- what the prompt then says ------------------------------------------------


def test_without_keywords_the_prompt_is_unchanged():
    text = prompts.new_recipes_prompt("Family", count=5)
    assert "Build them around" not in text


def test_one_keyword_asks_for_it_as_the_main_ingredient():
    text = prompts.new_recipes_prompt("Family", keywords=["skyr"])
    assert "Build them around **skyr**" in text
    assert "not a garnish" in text


def test_several_keywords_ask_for_coverage_across_the_batch():
    """Each recipe leans on one; the batch covers all. Without the second
    sentence a model puts chicken, broccoli and rice in every single dish."""
    text = prompts.new_recipes_prompt("Family", keywords=["chicken", "broccoli"])
    assert "**chicken, broccoli**" in text
    assert "at least one of them" in text
    assert "cover all of them" in text


def test_the_keywords_come_before_the_supermarket_line():
    """It is the strongest constraint in the prompt and the one most worth
    reading first."""
    text = prompts.new_recipes_prompt("Family", keywords=["chicken"])
    assert text.index("Build them around") < text.index("Netto")


def test_keywords_survive_alongside_the_avoid_list():
    """Both narrow the request and neither should crowd the other out."""
    text = prompts.new_recipes_prompt("Family", keywords=["chicken"],
                                      avoid=["Chicken curry"])
    assert "Build them around **chicken**" in text
    assert "I already have these" in text
    assert "Chicken curry" in text


def test_snacks_take_keywords_too():
    """A household with a tub of skyr wants snack ideas for it as much as
    dinner ideas."""
    text = prompts.snacks_prompt("Bulk", keywords=["skyr", "havregryn"])
    assert "**skyr, havregryn**" in text
    assert "healthy snacks" in text


def test_the_json_shape_and_rules_are_still_there():
    """The keywords steer what is asked for, never how it comes back — the
    importer depends on the shape."""
    text = prompts.new_recipes_prompt("Family", keywords=["chicken"])
    assert "shop_name" in text and "```json" in text
    assert "Reply with **JSON only**" in text


# --- through the endpoint -----------------------------------------------------


def test_the_endpoint_takes_keywords(client):
    body = client.get("/api/prompt?category=Family&keywords=chicken,%20broccoli").get_json()
    assert body["keywords"] == ["chicken", "broccoli"]
    assert "Build them around these: **chicken, broccoli**" in body["prompt"]


def test_the_endpoint_echoes_what_it_understood(client):
    """So the page can show it. A forgotten comma turns two ingredients into
    one, and seeing "minced beef chicken" as a single chip is how that gets
    caught before the prompt is taken anywhere."""
    body = client.get("/api/prompt?category=Family&keywords=minced%20beef%20chicken").get_json()
    assert body["keywords"] == ["minced beef chicken"]


def test_no_keywords_parameter_still_works(client):
    body = client.get("/api/prompt?category=Family").get_json()
    assert body["keywords"] == []
    assert "Build them around" not in body["prompt"]


def test_snacks_through_the_endpoint(client):
    body = client.get("/api/prompt?kind=snacks&category=Bulk&keywords=skyr").get_json()
    assert "Build them around **skyr**" in body["prompt"]


# --- the field ----------------------------------------------------------------


def _static(name):
    import os
    sub = "templates" if name.endswith(".html") else "static"
    with open(os.path.join(os.path.dirname(__file__), "..", sub, name),
              encoding="utf-8") as handle:
        return handle.read()


def test_the_field_is_wired():
    html, js = _static("index.html"), _static("app.js")
    assert 'id="prompt-keywords"' in html
    assert 'keywords: el("prompt-keywords").value' in js


def test_typing_does_not_fire_a_request_per_keystroke():
    """Each one rebuilds a long prompt, and a burst of them arrives out of
    order — the last response to land wins, which need not be the last typed."""
    js = _static("app.js")
    assert "clearTimeout(keywordTimer)" in js
    assert 'el("prompt-keywords").addEventListener("input", refreshPromptSoon)' in js


def test_the_chips_show_what_the_server_parsed_not_what_was_typed():
    js = _static("app.js")
    fn = js[js.index("async function refreshPrompt("):js.index("let keywordTimer")]
    assert "body.keywords" in fn
    assert "chips.hidden = words.length === 0;" in fn
