# Changelog

## 1.1.1

- **The Copy prompt button did not copy.** `navigator.clipboard` requires a
  secure context, and Home Assistant ingress is served over plain http — so on
  most installs the modern API is not merely refused, it is absent, and the
  button has never worked. It fell to "Select the text below and copy", which
  is a lot of text to select.
- It now falls back to `document.execCommand("copy")`, deprecated and the only
  thing that works outside a secure context. The modern API is still preferred
  where it is available.
- The fallback needs a genuinely selectable element, so the scratch textarea is
  positioned off-screen rather than hidden — `display: none` cannot be selected
  and would copy an empty string while looking correct. iOS additionally
  ignores `.select()` on a readonly field, so an explicit range is set.
- If both refuse, the prompt is now **selected for you**, leaving one keypress
  rather than a drag through several screens. The Download button was, and
  remains, the other way out.

## 1.1.0

- **Briefings are rendered as Markdown.** Headings, bold, lists, tables, inline
  code and fenced code blocks. Until now a briefing was inserted as escaped
  plain text, so the `##` and the triple backticks that assistants reliably
  produce were displayed literally — every pack imported so far has them.
- **Fenced blocks are monospace, with the spacing untouched.** That is what
  makes a diagram drawn with `─` and `│` line up; in the page's proportional
  font it never could. Wide blocks scroll inside their own box rather than
  pushing the page sideways on a phone.
- Rendered **server-side, in `markdown.py`, with tests**. A briefing is whatever
  an assistant produced and the result goes into the DOM, so this is the one
  security-sensitive path in the add-on. Doing it in Python rather than in
  `app.js` is what makes it unit-testable — there is no JavaScript test runner
  in this repository, and an untested HTML sanitiser is not worth having.
- The escaping happens **before** any rule runs, so no tag in a briefing can
  survive to become a tag in the output. There is no allow-list to keep in sync
  and no sanitiser to outwit. Eight injection payloads are asserted against.
- The raw text is still sent alongside the HTML, and the page falls back to it,
  so a briefing is never lost to a renderer bug.
- Adds `test_static_wiring.py`, which the other add-ons here have had for a
  while and Knowledge did not. It caught an unrelated pre-existing lookup on
  first run.

## 1.0.0

First release.

- **A topic a day, with no internet.** Subscribe to topics; each day serves the
  next subtopic from that topic's syllabus with a briefing, a self-grading quiz,
  short-answer questions with model answers, one practical task, and flashcards
  on an SM-2 spaced-repetition schedule.
- **The add-on never calls a language model.** No API key, no provider, no
  outbound request except to Home Assistant's own Supervisor. Material arrives
  through an exchange you carry out: the add-on writes a prompt, you run it
  against any assistant wherever you have a connection, and paste the reply
  back. One paste carries a whole syllabus plus a fortnight of material.
- Three prompts, chosen automatically from the state of the topic: the first
  pack for a new topic, a refill naming only the subtopics still missing (with
  the existing syllabus as context so the depth matches), and an extension once
  a syllabus is finished.
- The importer is deliberately forgiving, because what it is fed was written by
  an assistant and pasted by a human: it finds the JSON inside ``` fences or
  surrounding prose, survives braces inside strings, accepts field-name
  synonyms, and reads an answer key expressed as an index, a letter, or the
  answer's own text. Anything unusable is skipped and listed rather than failing
  the whole pack — but a question whose correct choice cannot be determined is
  dropped rather than guessed at, since being marked wrong for the right answer
  is worse than one question fewer.
- Quiz answers and short-answer model answers are withheld from the payload
  until you have answered, rather than hidden in the page — anything sent to the
  browser is readable from the developer console.
- Several topics take turns, least recently served first, so subscribing to four
  gives one subtopic a day rotating between them rather than four a day.
- Ships subscribed to **Apache Spark** and **Apache Airflow** — what this
  repository's own pipeline runs on — so a fresh install opens with something to
  press instead of an empty screen. Configurable through `starter_topics`, and
  applied exactly once, so deleting a starter topic is permanent rather than
  something the next restart undoes.
- Four Home Assistant sensors (today's subtopic, streak, cards due, material
  left) and an optional daily reminder through a notify service — which says so
  when a topic has run out of material, the moment you actually need to hear it.
- Verified end to end with a real browser render against a deliberately messy
  simulated assistant reply — mixed answer-key formats, a malformed question,
  prose either side of the JSON — covering the daily lesson, grading, review
  scheduling and every prompt kind, in light and dark.
