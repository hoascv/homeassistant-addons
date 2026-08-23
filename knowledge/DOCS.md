# Knowledge

A topic a day. You subscribe to topics; each day this add-on serves the next
subtopic from that topic's syllabus with a briefing to read, a quiz that grades
itself, short-answer questions you grade against a model answer, one practical
task to do away from the screen, and flashcards that come back on a spaced
repetition schedule.

**This add-on never goes online.** It has no API key, no LLM provider, and makes
no outbound request except to Home Assistant's own Supervisor. Material gets in
through an exchange you carry out yourself: the add-on writes a prompt, you run
it against whatever assistant you have wherever you have a connection, and you
paste the reply back. One paste carries a whole syllabus plus a fortnight of
material.

---

## How it works

```
  ┌────────────┐   1. copy prompt    ┌─────────────────┐
  │  Knowledge │ ──────────────────► │  any assistant  │
  │  (offline) │                     │  (your phone,   │
  │            │ ◄────────────────── │   work laptop…) │
  └────────────┘   2. paste reply    └─────────────────┘
        │
        │ 3. a subtopic a day, forever after — no connection needed
        ▼
   briefing · quiz · short answers · practical task · flashcards
```

Three prompts exist, and the add-on picks the right one for you:

| When | Prompt | Asks for |
|---|---|---|
| A topic is new | **new** | The whole syllabus (default 24 subtopics) plus material for the first 14 |
| Some subtopics have no material | **more** | Material for exactly those, with the existing syllabus as context so the depth matches |
| Every subtopic has been covered | **extend** | A harder set of subtopics that goes beyond the syllabus, with material |

You never have to know which — the **Get a prompt** button works it out.

## Getting started

A fresh install already has **Apache Spark** and **Apache Airflow** subscribed —
the two things this repository's own pipeline runs on — so there is a button to
press rather than an empty screen. Change them with `starter_topics`, or remove
them and subscribe to your own; they are only ever added once, so deleting one
is permanent.

1. Install and start the add-on, then open it from the sidebar.
2. **+ Subscribe** — name the topic. Optionally say what you want out of it
   ("enough to run a small cluster in production without fear"); that line goes
   into the prompt and visibly changes the syllabus you get back.
3. The prompt appears immediately. **Copy prompt** (or **Download .txt** if you
   are moving it to another machine).
4. Paste it into any assistant, anywhere. Wait for the reply.
5. Copy the reply — all of it, fences and chatter included — and paste it into
   **Load a pack**. Or save it to a file and upload that.
6. Today's subtopic is ready before you close the sheet.

From then on, a new subtopic each day until the material runs out. The add-on
tells you when it is getting low, and the sidebar sensor does too.

## The daily lesson

- **Briefing** — a few hundred words teaching the subtopic. Collapsed once the
  lesson is done, so returning to an old day is not a wall of text.
- **Quiz** — multiple choice, graded instantly, with the explanation revealed
  after you answer. The correct answer is not in the page until you have
  answered: it is withheld from the payload, not merely hidden, because anything
  sent to the browser is readable from the developer console.
- **In your own words** — write an answer, then reveal the model answer and
  grade yourself *Got it / Partly / Missed it*. The model answer is fetched at
  the moment you ask for it, for the same reason. Your own text is kept, so you
  can see later what you actually thought.
- **Do this** — one applied task, 15-30 minutes, away from the screen.
- **Review** — flashcards from subtopics you have already studied.

A lesson marks itself done when every question has an answer; there is also a
button, for a day you want to close out without answering everything.

### Several topics at once

`lessons_per_day` is 1 by default, and subscribed topics take turns — least
recently served first. Subscribe to four topics and you get one subtopic a day
rotating between them, not four. Raise `lessons_per_day` if you want more, and
it will prefer to spread them across different topics before taking two from
one.

Pause a topic to take it out of the rotation without losing its progress.

## Flashcards and the review schedule

Cards use SM-2, the algorithm Anki's scheduler descends from: each card carries
an ease factor and an interval, a good review multiplies the interval by the
ease, and a failed review sends the card back to the start. The four buttons map
onto it directly.

- **Again** — the card is due again *today*, and its ease drops.
- **Hard** — advances, but the step is shortened.
- **Good** — 1 day, then 6, then × the ease.
- **Easy** — as Good, and the ease rises.

Intervals cap at a year. A card only enters the queue when its subtopic is
actually served, so the first review is never a wall of unrecognisable trivia
from material you have not read yet.

`cards_per_day` caps how many you are shown at once — the rest simply wait.

## If a pack comes back malformed

It will, sometimes. The importer is deliberately forgiving:

- The JSON is found inside ``` fences, after a preamble, before a sign-off, or
  all three. Braces inside strings (a briefing about `${HOME}`, say) do not
  confuse it.
- Field names are matched through synonyms — `subtopics`/`syllabus`,
  `notes`/`briefing`, `options`/`choices`, `cards`/`flashcards`, and more.
- The answer key is accepted as an index, a letter (`B`, `b)`, `(C)`), the
  answer's own text, or `B) the answer text`.
- Anything genuinely unusable is **skipped and listed**, not fatal. A pack that
  lands 29 of 30 questions and tells you which one it dropped is worth far more
  than a rejection, because your only other option is to go and ask again.

One thing it will not do is guess. A quiz question whose correct choice cannot
be determined is dropped rather than scored against a guess — being marked wrong
for the right answer is worse than one question fewer.

**Check it** parses without saving, so you can see what a paste contains before
committing to it.

Re-pasting the same pack is safe: subtopics are matched by title, and material
that is already in place is kept rather than replaced, so answers you have
already given keep pointing at the questions you gave them for.

## Home Assistant sensors

Pushed once a minute, when running under Supervisor:

| Entity | State | Notable attributes |
|---|---|---|
| `sensor.knowledge_today` | today's subtopic title | `topic`, `subtopic_number`, `questions`, `answered`, `completed`, `also_today` |
| `sensor.knowledge_streak` | consecutive days | `lessons_completed`, `accuracy` |
| `sensor.knowledge_cards_due` | cards due now | `cards_total` |
| `sensor.knowledge_material_left` | days of material on the thinnest topic | `topic`, `running_low` |

The streak counts consecutive days with at least one completed lesson. Today
being unfinished does not break it — only a whole day passing with nothing
completed does, so it does not spend every morning telling you that you have
lost it.

## Daily reminder

Set `notify_service` to a notify service's bare name (`mobile_app_pixel`, not
`notify.mobile_app_pixel` — **Settings → List notify services** in the add-on
will show you what exists), turn on `daily_reminder_enabled`, and pick a time.
Once a day at or after that time you get today's subtopic, the number of cards
due, and a warning if a topic is running low on material.

If there is no lesson because everything has run out, the reminder says that
instead — which is the moment you actually need to hear it.

## Options

| Option | Default | What it does |
|---|---|---|
| `lessons_per_day` | 1 | Subtopics served per day across all topics |
| `syllabus_size` | 24 | How many subtopics a new topic's prompt asks for |
| `material_days` | 14 | How many subtopics' material one pack should carry |
| `quiz_questions` | 6 | Multiple-choice questions per subtopic |
| `short_answer_questions` | 3 | Written questions per subtopic |
| `flashcards_per_subtopic` | 8 | Cards per subtopic |
| `cards_per_day` | 20 | Cap on cards shown in one review |
| `low_material_threshold` | 3 | Warn at or below this many days of material |
| `default_level` | intermediate | Pre-selected level for a new topic |
| `starter_topics` | Apache Spark, Apache Airflow | Subscribed automatically on a fresh install, once |
| `notify_service` | — | Notify service for the daily reminder |
| `daily_reminder_enabled` | false | Whether to send it |
| `daily_reminder_time` | 18:00 | Earliest time to send it |
| `restrict_to_user_ids` | — | Limit to specific Home Assistant user ids |
| `api_token` | — | Bearer token for the direct port |

`starter_topics` is comma-separated, and an optional `Name: what you want out
of it` sets that topic's goal — the same free text the Subscribe form takes,
which is what most visibly steers the syllabus you get back. Set it empty to
start with nothing subscribed.

The counts go straight into the generated prompt. Asking for 20 quiz questions
per subtopic across 14 subtopics is a large reply — if an assistant starts
truncating, lower `material_days` and take two packs instead.

## Endpoints

- `/` — the ingress dashboard.
- `/api/summary` — today's lessons, topics with progress, stats, warnings.
- `/api/topics` — `GET` to list, `POST` `{name, goal, level}` to subscribe.
- `/api/topics/<id>` — `PATCH` `{active, goal, level}`, or `DELETE`.
- `/api/topics/<id>/prompt?kind=auto|new|more|extend` — the prompt text.
- `/api/import` — `POST` `{topic_id, text}`, or a multipart `file` upload.
- `/api/import/preview` — `POST` `{text}`, parses without saving.
- `/api/answers` — `POST` `{lesson_id, question_id, chosen_index}` or
  `{lesson_id, question_id, self_grade, response_text}`.
- `/api/questions/<id>/reveal` — a short-answer question's model answer.
- `/api/lessons/<id>/complete` — close out a lesson.
- `/api/cards/due`, `/api/cards/<id>/review` — `POST` `{grade}` where grade is
  `again`, `hard`, `good` or `easy`.
- `/api/history?days=N` — lessons served, with scores.
- `/api/notify-services` — notify services Home Assistant can see.
- `/api/stats`, `/api/backup` — row counts, and the whole database as JSON.

## Access

Same policy as the other add-ons here. Through Home Assistant's ingress, the
authenticated user's id arrives in a header; `restrict_to_user_ids` narrows
access to a list of them. The direct port (only published if you map one)
requires `api_token` as a bearer token — there is no unauthenticated door.

## Notes and limits

- **The material is only as good as the assistant that wrote it.** Nothing here
  fact-checks a briefing, and a confident wrong explanation will be taught to
  you as readily as a right one. For anything that matters, treat a pack as a
  study aid, not a source.
- A subtopic is served on exactly one day, ever — enforced by a unique
  constraint, not merely assumed.
- Subtopics are served in syllabus order, skipping any that have no material
  yet. If a later pack fills one of those in, it takes its rightful place in the
  sequence rather than being lost.
- Deleting a topic deletes everything under it: subtopics, questions, cards,
  review schedules and answers. There is a confirmation, and no undo.
- Dates are the container's local dates — Supervisor gives it Home Assistant's
  timezone, so "today" means the day you are having.
- The database is a single SQLite file at `/data/knowledge.db`, included in Home
  Assistant's own add-on backups. `/api/backup` returns the whole thing as JSON
  if you want it elsewhere.
