# Journal

A daily journal that lives on your own Home Assistant, behind a master
password. Write the day into a handful of sections you choose, rate it, tag it,
note what moved on the goals you are chasing — and open any past date from your
phone, through Home Assistant's sidebar.

Everything you write is encrypted at rest with a key derived from a password
this add-on never stores. Locked, it cannot read a word of your journal. Nor
can anyone holding the database file, the Home Assistant backup it sits in, or
the disk it came off.

---

## Read this first

**There is no password recovery. None.**

Not a reset, not a hint, not a recovery code, not a support address. The
password is not stored anywhere in any form — the add-on checks it by trying
it, which is why a wrong one simply fails and a forgotten one is final. If you
forget it, every entry is permanently unreadable. That is not a limitation
around the edges of the design; it *is* the design.

Put the password in your password manager before you type it into the first-run
screen.

If you would rather have a journal you can always get back into than one nobody
else can get into, this add-on is the wrong choice, and that is a reasonable
thing to want.

---

## Getting started

1. Install and start the add-on, then open **Journal** in the sidebar.
2. Choose a master password (at least 8 characters — a passphrase of a few
   words beats a short scramble).
3. That is it. You land on today with four sections ready to fill in.

## The day

The entry screen is one day, always. It opens on today.

- **Sections** — four to begin with: *What I did*, *What I was thinking*,
  *Grateful for*, *Tomorrow*. Fill in as many as you feel like; the empty ones
  are not stored and cost nothing. Rename or replace them in settings.
- **Mood** — five faces, from rough to great. Tap the chosen one again to clear
  it. It colours the day's square in the calendar strip.
- **Tags** — comma separated, `#` optional, lower-cased and de-duplicated for
  you. Searchable.
- **Goal check-ins** — one line per active goal: what moved today, and a
  **moved** tick for the days you actually pushed it forward.

It saves as you write — a second and a half after you stop typing, whenever you
leave a field, and whenever you leave the page or switch days. The pill in the
card header says where you stand: *Saved 21:04*, *Unsaved…*, or *Not written
yet*. There is a **Save** button too, for when you want to see it happen.

A day emptied of everything is deleted rather than stored blank, so opening an
old day to read it cannot quietly add to your streak.

## Going back

- **The date bar** — arrows for a day either way, a date picker for anywhere,
  **Today** to come home. When the neighbouring days are empty, the nearest day
  you actually wrote appears as a shortcut, so you can skip an empty fortnight
  in one tap instead of thirteen.
- **Recent weeks** — twelve weeks as one square a day: colour is the mood, an
  outline means written without a mood, empty means nothing written. Tap any
  square to open that day.
- **On this day** — the same date in earlier years, appearing under the entry
  once you have a year of history. The reason to keep a journal at all.
- **Search** (🔍) — plain text across every entry: prose, tags and goal notes,
  newest first, with the surrounding words for context.

Search decrypts each entry in turn, in the add-on, because there is no way to
index ciphertext without the index becoming a summary of the journal lying in
the clear. A decade of daily entries is a few thousand small rows, so this
stays quick; it is simply linear rather than clever.

## Goals

**Manage** on the Goals card opens the list. A goal has a title, an optional
*why*, and an optional target date.

- Check in on any day from the entry screen. Check-ins are stored inside the
  day you wrote them, so a goal's history is genuinely a history of what you
  wrote at the time.
- **History** on a goal shows every check-in, newest first, each one a link
  back to the day it came from.
- A goal you have not touched for a while gets an amber flag with the number of
  quiet days — `goal_nudge_days` in the add-on's configuration, 7 by default,
  0 to switch the flagging off.
- **Done**, **Drop**, and **Reopen** move a goal without deleting it. Closed
  goals disappear from the daily check-in list but stay in the manage sheet
  with their history.
- **Delete** removes the goal itself. What you wrote about it on each day stays
  in those days — dropping a goal is not a reason to rewrite your diary.

Note this is a different thing from the **Goal Tracker** add-on in this
repository, which tracks body weight and body fat against a target. This one is
about goals you write about.

## Changing the shape of a day

Settings (⚙️) → **Sections**. Rename, reorder, add or remove. A heading you
remove stops appearing on new days.

Two things are true of every entry you have already written:

- The heading is stored with the words, so renaming *Grateful for* to *Wins*
  next March leaves February's entry saying *Grateful for*. Your past is not
  retitled under you.
- Text written under a section you later removed is still shown when you open
  that day, marked **retired**. Nothing is hidden because the template moved
  on.

## Locking

- The **padlock** in the header locks immediately — everywhere, not just in the
  tab you pressed it in.
- Idle for `auto_lock_minutes` (60 by default) and it locks itself. Set it to
  `0` to switch the idle timeout off; the padlock and restarts still lock.
- Restarting the add-on, rebooting the host, or updating always locks it. The
  key is only ever in memory.
- Closing the browser tab ends that session too — the token lives in the tab,
  not in a cookie.

## Changing the master password

Settings (⚙️) → **Master password**. It decrypts every entry, goal and setting
with the old key and re-encrypts with the new one, in a single transaction: if
anything fails part-way, the whole change rolls back and your old password
still works. You stay unlocked afterwards.

## Backups and getting your journal out

- **The database** (`/data/journal.db`) is in every Home Assistant backup
  already, and it is encrypted there. Restoring a backup restores the journal;
  the same master password opens it.
- **The plain-text export** (Settings → *Download plain-text export*) is a JSON
  file of everything — entries, goals, sections — **with no encryption on it at
  all**. It exists so your journal is not trapped in this add-on. Treat the
  file as you would the journal itself.

There is no import. The database is the migration path: restore the backup, or
copy `journal.db` into the new install's `/data`.

## Home Assistant

**A sensor**, `sensor.journal_streak`, updated every minute:

| | |
|---|---|
| state | current streak, in days |
| `entries` | how many days have been written |
| `longest_streak` | the best run so far |
| `last_entry_on` | the date of the most recent entry |
| `written_today` | whether today has anything in it |
| `goals_active` / `goals_done` | goal counts |
| `unlocked_sessions` | how many sessions currently hold the key |

Counts and dates only, and it cannot be otherwise: the loop that publishes it
has no key and never gets one. Nothing you write can reach Home Assistant's
state machine, where it would be recorded, graphed and backed up in the clear.

**A reminder**, optionally: set `notify_service` to the bare service name
(`mobile_app_pixel`, not `notify.mobile_app_pixel`), turn on
`daily_reminder_enabled`, and pick a time. If the day is still unwritten by
then, you get one nudge — *"Nothing written today yet. You are on a 6-day
streak."* It cannot quote an entry at you, for the same reason the sensor
cannot.

## Options

| Option | Default | What it does |
|---|---|---|
| `auto_lock_minutes` | `60` | Idle minutes before the key is dropped and the journal locks. `0` never auto-locks. |
| `goal_nudge_days` | `7` | Flag an active goal with no check-in for this many days. `0` turns it off. |
| `notify_service` | – | Notify service for the reminder, bare name. |
| `daily_reminder_enabled` | `false` | Whether to send it. |
| `daily_reminder_time` | `21:00` | When, in Home Assistant's timezone. |
| `restrict_to_user_ids` | – | Comma-separated Home Assistant user IDs allowed in at all. Empty means every admin. |

## Security

**What is encrypted.** Entry text, section headings as written, mood, tags,
goal titles, goal notes, goal check-ins, your section template. AES-256-GCM,
one blob per row, a fresh nonce every write.

**What is not.** Which dates have an entry, how many goals exist and whether
they are active, and when rows were written. This skeleton is deliberate: it is
what lets a locked add-on still know it is on a fourteen-day streak and send
you a nudge without being able to read anything. Someone with the database file
can tell that you wrote on 3 March. They cannot tell what.

**The key.** `scrypt` with a random 16-byte salt at n=2¹⁵ (32 MiB per attempt),
derived on unlock and held in memory only. There is no password hash stored to
be stolen; correctness is decided by whether the key authenticates a verifier
blob. Guesses against the running add-on are throttled with a cooldown that
doubles after five consecutive failures.

**Binding.** Each ciphertext is tied to the row it belongs to (`entry:
2026-08-29`, `goal:<id>`) as authenticated data, so a blob moved between rows
by hand fails to authenticate rather than quietly reading back under the wrong
date.

**The doors.** Ingress only — Home Assistant's own login first, then the master
password. Unlike the other add-ons here, this one publishes no direct port and
has no API token, because a token that returns decrypted entries would be a
second key to the same lock. `restrict_to_user_ids` narrows who may reach it at
all.

**What this does not protect against.** While you have it unlocked, the add-on
holds the key and serves your entries over your local network to your browser —
if your Home Assistant is reachable over plain HTTP and someone is listening on
that network, they can read what you are reading. Use HTTPS to Home Assistant.
Equally, someone who can run code on your Home Assistant host while the journal
is unlocked can read the key out of memory. The encryption is about the file at
rest, in a backup, and on a disk that leaves the house: that is a real threat
for a journal, and it is the one this defends against.
