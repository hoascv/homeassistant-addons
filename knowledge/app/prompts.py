"""The prompts this add-on hands the user to take to an LLM.

The whole design rests on one exchange: the add-on writes a prompt, the user
runs it wherever they have an assistant and an internet connection (a phone,
a work laptop, a browser tab), and pastes the reply back. Nothing here ever
calls a model itself — there is no API key in this add-on and no network
call outside Home Assistant's own Supervisor.

That makes the prompt the actual interface, so it is written the way an API
schema would be: exact field names, exact types, a worked example, and an
explicit instruction about what *not* to include. The importer is forgiving
about all of it (see importer.py), but the closer the reply starts out, the
less it has to drop.
"""

_SCHEMA = """{
  "topic": "<the topic name, copied exactly>",
  "syllabus": [
    {"title": "<subtopic name>", "summary": "<one sentence on what it covers>"}
  ],
  "material": [
    {
      "title": "<must match a syllabus title exactly>",
      "briefing": "<200-400 words teaching this subtopic: what it is, why it matters, the mechanism, the common misunderstanding>",
      "practical_task": "<one concrete thing to do away from the screen, doable in 15-30 minutes>",
      "quiz": [
        {
          "question": "<multiple-choice question>",
          "choices": ["<option A>", "<option B>", "<option C>", "<option D>"],
          "answer": 0,
          "explanation": "<why that choice is right and the tempting one is wrong>"
        }
      ],
      "short_answer": [
        {"question": "<question to answer in 2-4 sentences>", "model_answer": "<what a good answer contains>"}
      ],
      "flashcards": [
        {"front": "<term or question>", "back": "<definition or answer>"}
      ]
    }
  ]
}"""


def _rules(counts):
    return f"""Rules:
- "answer" is the 0-based index into that question's own "choices" array.
- Every "title" in "material" must appear verbatim in "syllabus".
- Order "syllabus" so each subtopic only depends on earlier ones.
- Per subtopic in "material": {counts['quiz']} quiz questions, \
{counts['short']} short-answer questions, {counts['cards']} flashcards.
- Make the wrong choices plausible. A quiz where the right answer is the longest \
or the only specific one teaches nothing.
- Prefer specifics over generalities: real numbers, real commands, real names.
- Reply with the JSON object and nothing else — no preamble, no commentary \
afterwards, no markdown around it."""


def new_topic_prompt(topic, goal=None, level="intermediate", syllabus_size=24, material_count=14, counts=None):
    """The first prompt for a topic: the whole syllabus, plus material for
    the first `material_count` subtopics.

    The syllabus is requested in full even though only part of it comes back
    with material, because the syllabus is what lets the add-on tell you how
    far through a topic you are and what is coming — and asking for it again
    later would get a different, incompatible outline.
    """
    counts = counts or {"quiz": 6, "short": 3, "cards": 8}
    goal_line = f"\nWhat I want out of it: {goal}\n" if goal else ""
    return f"""I am studying a topic a day, offline, with a small self-hosted app. \
Build me a study pack.

Topic: {topic}
Level: {level}{goal_line}
Produce:
1. A syllabus of {syllabus_size} subtopics that covers {topic} properly from \
first principles to working competence.
2. Full material for the first {material_count} of those subtopics.

Return exactly this JSON shape:

{_SCHEMA}

{_rules(counts)}"""


def more_material_prompt(topic, syllabus, pending_titles, level="intermediate", counts=None):
    """A refill: material for subtopics already in the syllabus.

    The full syllabus goes back into the prompt as context so the briefings
    land at the right depth — subtopic 19 of 24 should assume the first
    eighteen, and an assistant with no memory of the earlier exchange can
    only know that if it is told.
    """
    counts = counts or {"quiz": 6, "short": 3, "cards": 8}
    outline = "\n".join(f"{i + 1}. {title}" for i, title in enumerate(syllabus))
    wanted = "\n".join(f"- {title}" for title in pending_titles)
    return f"""I am studying a topic a day, offline, with a small self-hosted app. \
I already have the syllabus below and need material for some of it.

Topic: {topic}
Level: {level}

The full syllabus, already agreed — do not change it, renumber it or add to it:
{outline}

Write full material for exactly these subtopics, and no others:
{wanted}

Each briefing may assume everything earlier in the syllabus is already known.

Return exactly this JSON shape (the "syllabus" array can be omitted here):

{_SCHEMA}

{_rules(counts)}"""


def extend_syllabus_prompt(topic, syllabus, extra=12, level="intermediate", counts=None):
    """A prompt for what comes *after* a finished syllabus — the topic is
    done at the depth first asked for, and the next step is to go deeper
    rather than repeat.
    """
    counts = counts or {"quiz": 6, "short": 3, "cards": 8}
    outline = "\n".join(f"{i + 1}. {title}" for i, title in enumerate(syllabus))
    return f"""I am studying a topic a day, offline, with a small self-hosted app. \
I have finished the syllabus below and want to go further.

Topic: {topic}
Level: {level}

Already covered — do not repeat any of it:
{outline}

Produce:
1. {extra} new subtopics that go beyond the above: the harder cases, the \
operational reality, the parts that only matter once the basics are boring.
2. Full material for all {extra} of them.

Return exactly this JSON shape:

{_SCHEMA}

{_rules(counts)}"""
