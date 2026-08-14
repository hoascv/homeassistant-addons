# Pulse Runner

An original rhythm precision-platformer, played through Home Assistant's
sidebar (ingress). Auto-scroll forward, tap to jump, don't hit the spikes.
Everything is stored locally in the add-on's own SQLite database; nothing
leaves your Home Assistant instance.

This is not a copy of any existing commercial game — no borrowed name,
artwork, level design, or music. It's the same genre (an auto-scrolling
tap-to-jump precision platformer), built from scratch.

## Playing

Pick a level from the list and tap **Play**. The screen auto-scrolls; tap,
click, or press Space/Up to jump. Touching a spike or missing a jump ends the
run instantly — tap to restart from the beginning. There's no partial credit
for a near-miss and no pause-and-rewind: precision is the point.

Levels are currently seeded when the add-on first starts. A level editor to
build your own is planned for a future update — see `CHANGELOG.md`.

## Configuration

- **restrict_to_user_ids**: comma-separated Home Assistant user IDs allowed
  to open the add-on. Empty (default) means any user who can see the sidebar
  entry may use it.
- **audio_cleanup_interval_hours**: how often a background sweep removes
  uploaded audio files that no longer belong to any level, in hours, 1–168
  (default `6`). Only matters once the level editor's audio upload ships.

## Credits

Levels that ship with the add-on play silent until royalty-free (CC0)
background tracks are picked and added here with attribution. No audio
resembling any commercial game's soundtrack is used or planned.
