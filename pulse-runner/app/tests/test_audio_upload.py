"""Orphaned-audio cleanup — the one background job this add-on runs.

There's no upload endpoint yet (that lands with the level editor in M3), so
this exercises the cleanup tick directly against a tmp audio dir, the same
way gym-tracker's reminder ticks are tested without the background thread
itself.
"""
import os

from app import _cleanup_orphaned_audio_tick


def test_cleanup_removes_unreferenced_files(conn, audio_dir):
    os.makedirs(audio_dir)
    open(os.path.join(audio_dir, "kept.mp3"), "w").close()
    open(os.path.join(audio_dir, "orphaned.mp3"), "w").close()
    conn.execute(
        "INSERT INTO audio_tracks (title, filename, content_type) VALUES ('Kept', 'kept.mp3', 'audio/mpeg')"
    )
    conn.commit()

    _cleanup_orphaned_audio_tick(conn)

    remaining = set(os.listdir(audio_dir))
    assert remaining == {"kept.mp3"}


def test_cleanup_is_noop_when_dir_missing(conn, audio_dir):
    assert not os.path.isdir(audio_dir)
    _cleanup_orphaned_audio_tick(conn)  # must not raise


def test_cleanup_leaves_everything_when_all_referenced(conn, audio_dir):
    os.makedirs(audio_dir)
    open(os.path.join(audio_dir, "a.mp3"), "w").close()
    open(os.path.join(audio_dir, "b.mp3"), "w").close()
    conn.execute("INSERT INTO audio_tracks (title, filename, content_type) VALUES ('A', 'a.mp3', 'audio/mpeg')")
    conn.execute("INSERT INTO audio_tracks (title, filename, content_type) VALUES ('B', 'b.mp3', 'audio/mpeg')")
    conn.commit()

    _cleanup_orphaned_audio_tick(conn)

    assert set(os.listdir(audio_dir)) == {"a.mp3", "b.mp3"}
