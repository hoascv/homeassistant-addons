# Changelog

## 1.1.0

- **Backup and restore, without taking the encryption off.** Settings (⚙️) →
  *Download encrypted backup* writes out the whole database as it sits on disk:
  the same AES-256-GCM ciphertext, opened by the same master password and by
  nothing else. It is the copy that is safe to keep on a memory stick, which
  the plain-text export next to it never was.
- **Restore replaces this journal with an uploaded backup.** The file is
  checked for the right tables first, so another add-on's backup is refused
  rather than swapped in over a journal that cannot be got back. A rejected
  upload changes nothing and leaves nothing behind.
- The restored journal opens with **the password it had when the backup was
  taken**, which is not necessarily the one used to unlock. Every open session
  is dropped on restore for that reason — the key held in memory belongs to a
  vault that no longer exists — and the page returns to the lock screen.
- **Backup requires the journal to be unlocked; restore requires it only when
  there is already a vault.** The file is unreadable either way, but asking for
  the password to obtain it stops someone with sidebar access but no password
  carrying the ciphertext off to attack at leisure. Restore is the exception on
  purpose: a fresh install has no vault to unlock, and moving a journal to a new
  machine is exactly what this is for.

## 1.0.0

First release.

- **An encrypted daily journal.** Semi-structured entries — sections you
  choose, a mood, tags — behind a master password, with any past date a tap
  away.
- **Nothing readable on disk.** Entry text, headings, mood, tags, goal titles
  and goal notes are AES-256-GCM at rest under a key derived with scrypt from a
  password the add-on never stores. What stays in the clear is the skeleton:
  which dates have an entry, and which goals exist. That is what lets a locked
  add-on still publish a streak sensor and send a nudge that gives nothing
  away.
- **There is no password recovery**, and the first-run screen says so before it
  accepts one. The password is checked by trying it, not by comparing it with
  anything stored.
- **Goals with daily check-ins.** A line a day per goal, a timeline per goal
  built from the days themselves, and an amber flag on any goal that has gone
  quiet. Check-ins live inside the entry rather than in a table of their own:
  an index of which goal you touched on which day, sitting in the clear beside
  the encrypted words, would be the shape of your life without the content.
- **Going back.** A date bar that skips empty stretches to the nearest day you
  actually wrote, twelve weeks as a mood-coloured strip, the same date in
  earlier years, and plain-text search across everything.
- **An editable template.** Rename, reorder, add or drop sections. Entries keep
  the heading they were written under, and text under a section you later
  removed is still shown, marked retired.
- Locks on the padlock, on idle (`auto_lock_minutes`, 60 by default), and on
  every restart. Ingress only: no direct port and no API token, deliberately.
