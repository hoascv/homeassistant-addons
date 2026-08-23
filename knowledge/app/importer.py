"""Parsing the reply an LLM gave back for a generated study prompt.

This is the add-on's only ingestion path, so it is the one place that has to
be genuinely forgiving. The text arriving here was produced by whatever
assistant the user had to hand, pasted through whatever clipboard, and no
amount of "reply with JSON only" in the prompt makes that reliably true: the
answer comes wrapped in ``` fences, prefixed with "Here's your pack!",
followed by an offer to expand on anything, with the schema's field names
half-remembered.

So: extract the JSON out of the surrounding prose, accept the obvious
synonyms for each field, coerce the answer key however it was expressed, and
report everything that had to be dropped rather than failing the whole
import over one malformed question. A pack that lands 9 of 10 questions and
says so is worth much more than a rejection with a parse error, because the
user's only remedy is to go and ask an LLM again.
"""
import json
import re

PACK_VERSION = 1

# Every field below is looked up through these: first alias present wins.
_ALIASES = {
    "syllabus": ("syllabus", "subtopics", "topics", "outline", "curriculum"),
    "material": ("material", "lessons", "days", "content", "subtopic_material"),
    "title": ("title", "subtopic", "name", "heading"),
    "summary": ("summary", "description", "covers", "blurb"),
    "briefing": ("briefing", "notes", "explanation", "content", "body", "text", "study_notes"),
    "practical_task": ("practical_task", "task", "exercise", "practical", "activity"),
    "quiz": ("quiz", "questions", "mcq", "multiple_choice", "quiz_questions"),
    "short_answer": ("short_answer", "short_answers", "open_questions", "short", "written"),
    "flashcards": ("flashcards", "cards", "flash_cards", "deck"),
    "question": ("question", "prompt", "q", "text"),
    "choices": ("choices", "options", "answers", "alternatives"),
    "answer": ("answer", "correct", "correct_answer", "correct_index", "correct_option", "solution"),
    "explanation": ("explanation", "why", "rationale", "reason"),
    "model_answer": ("model_answer", "answer", "expected", "expected_answer", "solution", "ideal_answer"),
    "front": ("front", "term", "prompt", "question", "q"),
    "back": ("back", "definition", "answer", "a"),
}


class PackError(ValueError):
    """The payload could not be read as a pack at all."""


def _get(obj, field, default=None):
    if not isinstance(obj, dict):
        return default
    for alias in _ALIASES[field]:
        if alias in obj and obj[alias] not in (None, ""):
            return obj[alias]
    return default


def _text(value, limit=20000):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        # A briefing sometimes comes back as a list of paragraphs or bullets.
        value = "\n\n".join(_text(v, limit) or "" for v in value)
    elif not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value[:limit] or None


# --- Step 1: find the JSON in whatever was pasted ---


def extract_json(text):
    """The first complete JSON object in `text`, as a dict.

    Tries the cheap readings first (the whole string; a fenced block), then
    falls back to scanning for a balanced object. That last pass is what
    survives an assistant that wrote two paragraphs before and after the
    pack, which is the common case.
    """
    if not text or not text.strip():
        raise PackError("nothing pasted")
    text = text.strip()

    # The whole string first — a bare top-level array has to be recognised
    # here, because the brace scan below would find its first *element* and
    # confidently return that one object as the entire pack.
    candidates = [text]
    # ```json ... ``` or a bare ``` ... ``` fence.
    candidates.extend(m.group(1).strip() for m in re.finditer(r"```(?:json)?\s*(.+?)```", text, re.DOTALL))
    candidates.extend(_balanced_objects(text))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            # An assistant that skipped the envelope and returned the
            # syllabus array on its own; treat it as one.
            return {"syllabus": parsed}
    raise PackError(
        "no JSON object found in what was pasted — copy the assistant's whole reply, "
        "including the { ... } block"
    )


def _balanced_objects(text):
    """Every top-level {...} run in `text`, longest first.

    Brace counting has to ignore braces inside strings, or a briefing
    containing "{" ends the object early — and the resulting fragment
    parses as nothing, which reads to the user as "my paste was rejected"
    with no clue why.
    """
    found = []
    for start in (i for i, ch in enumerate(text) if ch == "{"):
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    found.append(text[start : i + 1])
                    break
        if found:
            # Only the outermost object per starting brace is interesting;
            # nested ones are reachable by parsing the parent.
            break
    return sorted(found, key=len, reverse=True)


# --- Step 2: normalise the pack into exactly what the database stores ---


def _answer_index(raw, choices):
    """The 0-based index of the correct choice, however it was expressed.

    Accepts an index, a letter ("B", "b)"), the answer's own text, or a
    1-based index — all of which turn up in practice. Returns None when it
    cannot be resolved, which drops the question rather than silently
    marking the wrong option correct: a quiz that grades you against a
    guess is worse than a quiz with one fewer question.
    """
    if raw is None or not choices:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        if 0 <= raw < len(choices):
            return raw
        # 1-based is the other plausible reading, but only when a 0-based
        # reading is impossible — otherwise "1" is ambiguous and guessing
        # would be exactly the silent miscorrection this avoids.
        if raw == len(choices):
            return raw - 1
        return None

    text = str(raw).strip()
    if not text:
        return None

    for i, choice in enumerate(choices):
        if text.lower() == str(choice).strip().lower():
            return i

    letter = re.match(r"^\(?([A-Za-z])[).:\]]?$", text)
    if letter:
        idx = ord(letter.group(1).lower()) - ord("a")
        if 0 <= idx < len(choices):
            return idx

    if text.isdigit():
        return _answer_index(int(text), choices)

    # "B) Kubernetes schedules pods" — a letter *and* the text.
    prefixed = re.match(r"^\(?([A-Za-z])[).:\]]\s+(.*)$", text)
    if prefixed:
        idx = ord(prefixed.group(1).lower()) - ord("a")
        if 0 <= idx < len(choices):
            return idx
    return None


def _normalise_quiz(raw_list, warnings, where):
    out = []
    for i, item in enumerate(raw_list or []):
        question = _text(_get(item, "question"), 2000)
        raw_choices = _get(item, "choices") or []
        if isinstance(raw_choices, dict):
            # {"A": "...", "B": "..."} — keep the key order the answer refers to.
            raw_choices = [raw_choices[k] for k in sorted(raw_choices)]
        choices = [c for c in (_text(c, 500) for c in raw_choices) if c]
        if not question or len(choices) < 2:
            warnings.append(f"{where}: skipped quiz question {i + 1} (no question text or fewer than two choices)")
            continue
        answer = _answer_index(_get(item, "answer"), choices)
        if answer is None:
            warnings.append(f"{where}: skipped quiz question {i + 1} (could not tell which choice is correct)")
            continue
        out.append(
            {
                "kind": "mcq",
                "question": question,
                "choices": choices,
                "answer_index": answer,
                "explanation": _text(_get(item, "explanation"), 2000),
                "model_answer": None,
            }
        )
    return out


def _normalise_short(raw_list, warnings, where):
    out = []
    for i, item in enumerate(raw_list or []):
        if isinstance(item, str):
            # A bare string is a question with no model answer — usable, but
            # there is nothing to grade against, so say so.
            question, model = _text(item, 2000), None
        else:
            question = _text(_get(item, "question"), 2000)
            model = _text(_get(item, "model_answer"), 4000)
        if not question:
            warnings.append(f"{where}: skipped short-answer question {i + 1} (no question text)")
            continue
        out.append(
            {
                "kind": "short",
                "question": question,
                "choices": None,
                "answer_index": None,
                "explanation": None,
                "model_answer": model,
            }
        )
    return out


def _normalise_cards(raw_list, warnings, where):
    out = []
    for i, item in enumerate(raw_list or []):
        if isinstance(item, (list, tuple)) and len(item) == 2:
            front, back = _text(item[0], 1000), _text(item[1], 2000)
        else:
            front, back = _text(_get(item, "front"), 1000), _text(_get(item, "back"), 2000)
        if not front or not back:
            warnings.append(f"{where}: skipped flashcard {i + 1} (needs both a front and a back)")
            continue
        out.append({"front": front, "back": back})
    return out


def normalise(raw, expected_topic=None):
    """Turn a parsed pack into the canonical shape app.py stores.

    Never raises for content problems — anything unusable is dropped and
    described in `warnings`. It raises only when there is no usable pack at
    all, because that is the one case where the user must go back to the
    assistant rather than accept a partial result.
    """
    if not isinstance(raw, dict):
        raise PackError("the pasted JSON is not an object")

    warnings = []
    topic = _text(raw.get("topic") or raw.get("subject"), 200)
    if expected_topic and topic and topic.strip().lower() != expected_topic.strip().lower():
        warnings.append(
            f"the pack says its topic is {topic!r} but it was imported into {expected_topic!r} — "
            "importing anyway, since only you can tell whether that is the same thing"
        )

    syllabus = []
    seen_titles = set()
    for i, item in enumerate(_get(raw, "syllabus") or []):
        if isinstance(item, str):
            title, summary = _text(item, 300), None
        else:
            title, summary = _text(_get(item, "title"), 300), _text(_get(item, "summary"), 1000)
        if not title:
            warnings.append(f"skipped syllabus entry {i + 1} (no title)")
            continue
        key = title.lower()
        if key in seen_titles:
            warnings.append(f"skipped duplicate syllabus entry {title!r}")
            continue
        seen_titles.add(key)
        syllabus.append({"title": title, "summary": summary})

    material = []
    for i, item in enumerate(_get(raw, "material") or []):
        title = _text(_get(item, "title"), 300)
        if not title:
            warnings.append(f"skipped material block {i + 1} (no title to attach it to)")
            continue
        where = title
        quiz = _normalise_quiz(_get(item, "quiz"), warnings, where)
        short = _normalise_short(_get(item, "short_answer"), warnings, where)
        cards = _normalise_cards(_get(item, "flashcards"), warnings, where)
        briefing = _text(_get(item, "briefing"))
        entry = {
            "title": title,
            "summary": _text(_get(item, "summary"), 1000),
            "briefing": briefing,
            "practical_task": _text(_get(item, "practical_task"), 4000),
            "questions": quiz + short,
            "cards": cards,
        }
        if not briefing and not entry["questions"] and not entry["cards"]:
            warnings.append(f"skipped material for {title!r} (no briefing, questions or flashcards in it)")
            continue
        material.append(entry)

    if not syllabus and not material:
        raise PackError(
            "the JSON parsed, but it has neither a syllabus nor any material — "
            "it does not look like a pack for this add-on"
        )

    return {"topic": topic, "syllabus": syllabus, "material": material, "warnings": warnings}


def parse(text, expected_topic=None):
    """`extract_json` then `normalise` — the whole path from paste to storable."""
    return normalise(extract_json(text), expected_topic=expected_topic)
