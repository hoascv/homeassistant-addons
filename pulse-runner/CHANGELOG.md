# Changelog

## 0.1.0

- **First playable build.** An auto-scrolling Cube run: tap to jump, spikes and
  blocks to dodge, instant restart on death. One hardcoded course to prove out
  the physics and rendering before levels are data-driven.
- **Levels are now real data, not code.** A level is a JSON object list —
  blocks, spikes — stored in sqlite. A level-list screen replaces the
  hardcoded course; pick one, play it. The editor to build them lands next.
