"""The importer is the only way material gets in, and what it is fed was
written by an assistant and pasted by a human. These tests are mostly about
the ways that goes wrong."""
import json

import pytest

import importer


# --- Finding the JSON ---


def test_extract_plain_json():
    assert importer.extract_json('{"topic": "X"}') == {"topic": "X"}


def test_extract_from_fenced_block():
    text = 'Sure! Here is your pack:\n\n```json\n{"topic": "X"}\n```\n\nLet me know if you want more.'
    assert importer.extract_json(text) == {"topic": "X"}


def test_extract_from_unlabelled_fence():
    assert importer.extract_json('```\n{"topic": "X"}\n```') == {"topic": "X"}


def test_extract_from_surrounding_prose():
    text = 'Absolutely — here you go.\n{"topic": "X", "syllabus": []}\nHope that helps!'
    assert importer.extract_json(text) == {"topic": "X", "syllabus": []}


def test_extract_survives_braces_inside_strings():
    # A briefing that talks about JSON, or a shell snippet with ${VAR}, must
    # not end the object early — naive brace counting gets this wrong.
    payload = {"topic": "Bash", "syllabus": [{"title": "Expansion ${HOME} and }", "summary": "x"}]}
    text = f"Here:\n{json.dumps(payload)}\nDone."
    assert importer.extract_json(text) == payload


def test_extract_bare_array_is_treated_as_a_syllabus():
    parsed = importer.extract_json('[{"title": "One"}, {"title": "Two"}]')
    assert [e["title"] for e in parsed["syllabus"]] == ["One", "Two"]


def test_extract_raises_on_nothing_usable():
    with pytest.raises(importer.PackError):
        importer.extract_json("I'm sorry, I can't help with that.")


def test_extract_raises_on_empty():
    with pytest.raises(importer.PackError):
        importer.extract_json("   ")


# --- Answer keys, expressed every way they turn up ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1, 1),
        ("1", 1),
        ("B", 1),
        ("b)", 1),
        ("(C)", 2),
        ("Right", 1),
        ("right", 1),
        ("B) Right", 1),
        (3, 2),  # 1-based, only because 3 is out of range as a 0-based index
    ],
)
def test_answer_index_accepts_what_assistants_actually_write(raw, expected):
    assert importer._answer_index(raw, ["Wrong", "Right", "Other"]) == expected


def test_answer_index_gives_up_rather_than_guessing():
    assert importer._answer_index("no idea", ["a", "b"]) is None
    assert importer._answer_index(None, ["a", "b"]) is None
    assert importer._answer_index(True, ["a", "b"]) is None


# --- Normalising ---


def test_normalise_accepts_field_synonyms():
    raw = {
        "subject": "Chess",
        "subtopics": [{"name": "Forks", "description": "Two at once"}],
        "lessons": [
            {
                "subtopic": "Forks",
                "notes": "A fork attacks two pieces.",
                "questions": [{"prompt": "What is a fork?", "options": ["A", "B"], "correct": "A"}],
                "cards": [{"term": "Fork", "definition": "Two targets, one piece"}],
            }
        ],
    }
    pack = importer.normalise(raw)
    assert pack["syllabus"] == [{"title": "Forks", "summary": "Two at once"}]
    assert pack["material"][0]["briefing"] == "A fork attacks two pieces."
    assert pack["material"][0]["questions"][0]["answer_index"] == 0
    assert pack["material"][0]["cards"] == [{"front": "Fork", "back": "Two targets, one piece"}]


def test_normalise_drops_one_bad_question_and_keeps_the_rest():
    raw = {
        "topic": "X",
        "material": [
            {
                "title": "One",
                "briefing": "b",
                "quiz": [
                    {"question": "Good", "choices": ["a", "b"], "answer": 0},
                    {"question": "No answer key", "choices": ["a", "b"], "answer": "???"},
                    {"question": "Only one choice", "choices": ["a"], "answer": 0},
                ],
            }
        ],
    }
    pack = importer.normalise(raw)
    questions = pack["material"][0]["questions"]
    assert [q["question"] for q in questions] == ["Good"]
    assert len(pack["warnings"]) == 2


def test_normalise_flags_a_topic_mismatch_but_still_imports():
    pack = importer.normalise({"topic": "Chess", "syllabus": [{"title": "A"}]}, expected_topic="Go")
    assert pack["syllabus"]
    assert any("Chess" in w and "Go" in w for w in pack["warnings"])


def test_normalise_ignores_case_when_matching_the_topic():
    pack = importer.normalise({"topic": "kubernetes", "syllabus": [{"title": "A"}]}, expected_topic="Kubernetes")
    assert pack["warnings"] == []


def test_normalise_skips_duplicate_syllabus_titles():
    pack = importer.normalise({"syllabus": [{"title": "A"}, {"title": "a"}]})
    assert len(pack["syllabus"]) == 1
    assert any("duplicate" in w for w in pack["warnings"])


def test_normalise_accepts_a_string_only_syllabus():
    pack = importer.normalise({"syllabus": ["First", "Second"]})
    assert [e["title"] for e in pack["syllabus"]] == ["First", "Second"]


def test_normalise_joins_a_briefing_returned_as_paragraphs():
    pack = importer.normalise({"material": [{"title": "A", "briefing": ["One.", "Two."]}]})
    assert pack["material"][0]["briefing"] == "One.\n\nTwo."


def test_normalise_skips_material_with_nothing_in_it():
    pack = importer.normalise({"syllabus": [{"title": "A"}], "material": [{"title": "A"}]})
    assert pack["material"] == []
    assert any("no briefing" in w for w in pack["warnings"])


def test_normalise_rejects_a_pack_with_neither_syllabus_nor_material():
    with pytest.raises(importer.PackError):
        importer.normalise({"topic": "X"})


def test_normalise_accepts_choices_as_a_lettered_object():
    pack = importer.normalise(
        {
            "material": [
                {
                    "title": "A",
                    "briefing": "b",
                    "quiz": [{"question": "q", "choices": {"A": "first", "B": "second"}, "answer": "B"}],
                }
            ]
        }
    )
    question = pack["material"][0]["questions"][0]
    assert question["choices"] == ["first", "second"]
    assert question["answer_index"] == 1


def test_parse_end_to_end_from_a_realistic_reply():
    reply = (
        "Great topic! Here's a starter pack.\n\n```json\n"
        + json.dumps(
            {
                "topic": "Rust",
                "syllabus": [{"title": "Ownership", "summary": "Who owns what"}],
                "material": [
                    {
                        "title": "Ownership",
                        "briefing": "Every value has one owner.",
                        "practical_task": "Write a function that moves a String.",
                        "quiz": [{"question": "How many owners?", "choices": ["One", "Many"], "answer": 0}],
                        "short_answer": [{"question": "Why?", "model_answer": "Memory safety without a GC."}],
                        "flashcards": [{"front": "Move", "back": "Ownership transfer"}],
                    }
                ],
            }
        )
        + "\n```\n\nWant me to go deeper on any of these?"
    )
    pack = importer.parse(reply, expected_topic="Rust")
    assert pack["warnings"] == []
    assert len(pack["material"][0]["questions"]) == 2  # one mcq + one short
    assert len(pack["material"][0]["cards"]) == 1
