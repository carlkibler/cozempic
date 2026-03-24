"""Tests for find_current_session strict mode and sidecar session store."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from cozempic.session import find_current_session


def _write_session(proj_dir: Path, session_id: str, content: str = "") -> Path:
    proj_dir.mkdir(parents=True, exist_ok=True)
    p = proj_dir / f"{session_id}.jsonl"
    p.write_text(content or json.dumps({"message": {"role": "user", "content": "hi"}}) + "\n",
                 encoding="utf-8")
    return p


class TestStrictMode:
    def test_strict_returns_none_when_only_fallback_available(self, tmp_path):
        """With no process or CWD match, strict=True returns None instead of guessing."""
        proj = tmp_path / "projects" / "-some-other-path"
        _write_session(proj, "aaaa1111-0000-0000-0000-000000000000")

        with (
            patch("cozempic.session.get_projects_dir", return_value=tmp_path / "projects"),
            patch("cozempic.session._session_id_from_process", return_value=None),
        ):
            result = find_current_session(cwd="/unrelated/path", strict=True)

        assert result is None

    def test_non_strict_returns_fallback(self, tmp_path):
        """With no process or CWD match, strict=False still returns most recent session."""
        proj = tmp_path / "projects" / "-some-other-path"
        _write_session(proj, "aaaa1111-0000-0000-0000-000000000000")

        with (
            patch("cozempic.session.get_projects_dir", return_value=tmp_path / "projects"),
            patch("cozempic.session._session_id_from_process", return_value=None),
        ):
            result = find_current_session(cwd="/unrelated/path", strict=False)

        assert result is not None
        assert result["session_id"] == "aaaa1111-0000-0000-0000-000000000000"

    def test_strict_succeeds_when_process_detected(self, tmp_path):
        """Process-based detection (Strategy 1) satisfies strict mode."""
        session_id = "bbbb2222-0000-0000-0000-000000000000"
        proj = tmp_path / "projects" / "-some-path"
        _write_session(proj, session_id)

        with (
            patch("cozempic.session.get_projects_dir", return_value=tmp_path / "projects"),
            patch("cozempic.session._session_id_from_process", return_value=session_id),
        ):
            result = find_current_session(strict=True)

        assert result is not None
        assert result["session_id"] == session_id

    def test_strict_succeeds_on_cwd_slug_match(self, tmp_path):
        """CWD slug match (Strategy 3) satisfies strict mode."""
        cwd = "/Users/foo/myproject"
        slug = cwd.replace("/", "-")
        proj = tmp_path / "projects" / slug
        session_id = "cccc3333-0000-0000-0000-000000000000"
        _write_session(proj, session_id)

        with (
            patch("cozempic.session.get_projects_dir", return_value=tmp_path / "projects"),
            patch("cozempic.session._session_id_from_process", return_value=None),
        ):
            result = find_current_session(cwd=cwd, strict=True)

        assert result is not None
        assert result["session_id"] == session_id

    def test_no_sessions_returns_none_regardless_of_strict(self, tmp_path):
        projects = tmp_path / "projects"
        projects.mkdir()

        with (
            patch("cozempic.session.get_projects_dir", return_value=projects),
            patch("cozempic.session._session_id_from_process", return_value=None),
        ):
            assert find_current_session(strict=True) is None
            assert find_current_session(strict=False) is None


class TestSlugRoundTrip:
    def test_simple_path_round_trips(self):
        from cozempic.session import cwd_to_project_slug, project_slug_to_path
        path = "/Users/foo/myproject"
        slug = cwd_to_project_slug(path)
        assert project_slug_to_path(slug) == path

    def test_hyphenated_path_slug_is_ambiguous(self):
        """Slug reversal is known-ambiguous for hyphenated paths.

        '/Users/foo/my-project' and '/Users/foo/my/project' produce the same
        slug.  This is intentionally not fixed in project_slug_to_path() —
        callers that need an exact path must use get_session_cwd() (sidecar).
        """
        from cozempic.session import cwd_to_project_slug, project_slug_to_path
        slug_a = cwd_to_project_slug("/Users/foo/my-project")
        slug_b = cwd_to_project_slug("/Users/foo/my/project")
        # The ambiguity: both slugs are identical
        assert slug_a == slug_b
        # And reversal cannot recover the original — this is expected behaviour
        assert project_slug_to_path(slug_a) != "/Users/foo/my-project"


class TestSidecarStore:
    def test_record_and_retrieve(self, tmp_path):
        """record_session persists cwd; get_session_cwd retrieves it."""
        from cozempic.session import get_session_cwd, record_session
        sid = "aaaa1111-0000-0000-0000-000000000000"
        with patch("cozempic.session.get_sidecar_path", return_value=tmp_path / "sidecar.json"):
            record_session(sid, "/Users/foo/my-project", context_window=200_000)
            assert get_session_cwd(sid) == "/Users/foo/my-project"

    def test_hyphenated_path_survives_round_trip(self, tmp_path):
        """Sidecar stores exact cwd — hyphenated paths are not mangled."""
        from cozempic.session import get_session_cwd, record_session
        sid = "bbbb2222-0000-0000-0000-000000000000"
        path = "/Users/foo/my-hyphenated-project"
        with patch("cozempic.session.get_sidecar_path", return_value=tmp_path / "sidecar.json"):
            record_session(sid, path)
            assert get_session_cwd(sid) == path

    def test_context_window_persisted(self, tmp_path):
        from cozempic.session import get_session_context_window, record_session
        sid = "cccc3333-0000-0000-0000-000000000000"
        with patch("cozempic.session.get_sidecar_path", return_value=tmp_path / "sidecar.json"):
            record_session(sid, "/some/path", context_window=1_000_000)
            assert get_session_context_window(sid) == 1_000_000

    def test_context_window_preserved_on_refresh(self, tmp_path):
        """Refreshing last_seen_at without a context_window keeps the old value."""
        from cozempic.session import get_session_context_window, record_session
        sid = "dddd4444-0000-0000-0000-000000000000"
        with patch("cozempic.session.get_sidecar_path", return_value=tmp_path / "sidecar.json"):
            record_session(sid, "/some/path", context_window=200_000)
            record_session(sid, "/some/path")  # refresh without context_window
            assert get_session_context_window(sid) == 200_000

    def test_unknown_session_returns_none(self, tmp_path):
        from cozempic.session import get_session_cwd
        with patch("cozempic.session.get_sidecar_path", return_value=tmp_path / "sidecar.json"):
            assert get_session_cwd("no-such-session") is None

    def test_evicts_oldest_when_full(self, tmp_path):
        """Sidecar is capped at _SIDECAR_MAX_ENTRIES; oldest entry is evicted."""
        from datetime import datetime as real_datetime
        from cozempic.session import _SIDECAR_MAX_ENTRIES, get_session_cwd, record_session

        sidecar = tmp_path / "sidecar.json"
        call_count = [0]

        def mock_now():
            n = call_count[0]
            call_count[0] += 1
            return real_datetime(2026, 1, 1, n // 3600, (n % 3600) // 60, n % 60)

        with (
            patch("cozempic.session.get_sidecar_path", return_value=sidecar),
            patch("cozempic.session.datetime") as mock_dt,
        ):
            mock_dt.now.side_effect = mock_now
            # First entry gets the smallest timestamp (oldest)
            first_id = f"{'0' * 8}-0000-0000-0000-{'0' * 12}"
            record_session(first_id, "/first", context_window=200_000)
            for i in range(1, _SIDECAR_MAX_ENTRIES + 1):
                sid = f"{i:08x}-0000-0000-0000-{'0' * 12}"
                record_session(sid, f"/path/{i}")
            # Oldest entry should have been evicted
            assert get_session_cwd(first_id) is None

    def test_missing_sidecar_returns_none(self, tmp_path):
        from cozempic.session import get_session_cwd
        with patch("cozempic.session.get_sidecar_path", return_value=tmp_path / "nonexistent.json"):
            assert get_session_cwd("any-session") is None

    def test_corrupt_sidecar_returns_none(self, tmp_path):
        from cozempic.session import get_session_cwd
        sidecar = tmp_path / "sidecar.json"
        sidecar.write_text("not valid json", encoding="utf-8")
        with patch("cozempic.session.get_sidecar_path", return_value=sidecar):
            assert get_session_cwd("any-session") is None
