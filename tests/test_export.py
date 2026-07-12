"""Tests for export.py — schema validation, formatting, and output structure."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from export import export


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_checkpoint(tmp_path, data: dict) -> Path:
    p = tmp_path / "checkpoint.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _minimal_lecture(title="Intro", week="Week 1", transcript="Hello world."):
    return {"title": title, "week": week, "url": "https://example.com", "transcript": transcript}


def _minimal_checkpoint(lectures=None):
    return {
        "course_slug": "test-course",
        "course_title": "Test Course",
        "lectures": lectures if lectures is not None else [_minimal_lecture()],
    }


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class TestSchemaValidation:
    def test_missing_lectures_key(self, tmp_path):
        cp = _write_checkpoint(tmp_path, {"course_slug": "x", "course_title": "X"})
        with pytest.raises(ValueError, match="lectures"):
            export(cp, tmp_path / "out")

    def test_empty_lectures_list(self, tmp_path):
        cp = _write_checkpoint(tmp_path, _minimal_checkpoint(lectures=[]))
        with pytest.raises(ValueError, match="lectures"):
            export(cp, tmp_path / "out")

    def test_lectures_wrong_type(self, tmp_path):
        cp = _write_checkpoint(tmp_path, _minimal_checkpoint(lectures="not-a-list"))
        with pytest.raises(ValueError, match="lectures"):
            export(cp, tmp_path / "out")

    def test_file_not_found_propagates(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            export(tmp_path / "missing.json", tmp_path / "out")

    def test_invalid_json_propagates(self, tmp_path):
        import json as _json
        bad = tmp_path / "bad.json"
        bad.write_text("{not json}", encoding="utf-8")
        with pytest.raises(_json.JSONDecodeError):
            export(bad, tmp_path / "out")


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

class TestOutputStructure:
    def test_transcript_files_created(self, tmp_path):
        cp = _write_checkpoint(tmp_path, _minimal_checkpoint())
        out = tmp_path / "out"
        export(cp, out)
        slug_dir = out / "test-course"
        assert (slug_dir / "transcript.txt").exists()
        assert (slug_dir / "transcript.md").exists()
        assert (slug_dir / "metadata.json").exists()

    def test_individual_lecture_files_created(self, tmp_path):
        lectures = [
            _minimal_lecture("Alpha Beta", "Week 1", "transcript one"),
            _minimal_lecture("Gamma Delta", "Week 1", "transcript two"),
        ]
        cp = _write_checkpoint(tmp_path, _minimal_checkpoint(lectures=lectures))
        out = tmp_path / "out"
        export(cp, out)
        txts = list((out / "test-course" / "transcripts").glob("*.txt"))
        # 2 individual lecture files
        assert len(txts) == 2

    def test_metadata_json_structure(self, tmp_path):
        lectures = [_minimal_lecture("L1"), _minimal_lecture("L2", "Week 2")]
        cp = _write_checkpoint(tmp_path, _minimal_checkpoint(lectures=lectures))
        out = tmp_path / "out"
        export(cp, out)
        meta = json.loads((out / "test-course" / "metadata.json").read_text())
        assert meta["course_slug"] == "test-course"
        assert meta["total_lectures"] == 2
        assert "exported_at" in meta


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

class TestFormatting:
    def _merged_txt(self, tmp_path, lectures) -> str:
        cp = _write_checkpoint(tmp_path, _minimal_checkpoint(lectures=lectures))
        out = tmp_path / "out"
        export(cp, out)
        return (out / "test-course" / "transcript.txt").read_text(encoding="utf-8")

    def test_module_boundary_exactly_one_blank(self, tmp_path):
        lectures = [
            _minimal_lecture("L1", "Week 1", "t1"),
            _minimal_lecture("L2", "Week 2", "t2"),
        ]
        content = self._merged_txt(tmp_path, lectures)
        # Must not have two consecutive blank lines anywhere
        assert "\n\n\n" not in content

    def test_same_module_exactly_one_blank_between(self, tmp_path):
        lectures = [
            _minimal_lecture("L1", "Week 1", "t1"),
            _minimal_lecture("L2", "Week 1", "t2"),
        ]
        content = self._merged_txt(tmp_path, lectures)
        assert "\n\n\n" not in content

    def test_module_header_present(self, tmp_path):
        lectures = [
            _minimal_lecture("L1", "Week 1"),
            _minimal_lecture("L2", "Week 2"),
        ]
        content = self._merged_txt(tmp_path, lectures)
        assert "=== Module: Week 1 ===" in content
        assert "=== Module: Week 2 ===" in content

    def test_lecture_index_in_output(self, tmp_path):
        lectures = [_minimal_lecture("L1"), _minimal_lecture("L2")]
        content = self._merged_txt(tmp_path, lectures)
        assert "[1]" in content
        assert "[2]" in content

    def test_title_newline_stripped(self, tmp_path):
        lectures = [_minimal_lecture("Title\nWith Newline", "Week 1")]
        content = self._merged_txt(tmp_path, lectures)
        # _clean_title takes first line only
        assert "Title\nWith Newline" not in content
        assert "[1] Title" in content

    def test_separator_present(self, tmp_path):
        lectures = [_minimal_lecture("L1"), _minimal_lecture("L2")]
        content = self._merged_txt(tmp_path, lectures)
        assert "---" in content

    def test_empty_transcript_no_double_blank(self, tmp_path):
        lectures = [_minimal_lecture("L1", "Week 1", ""), _minimal_lecture("L2", "Week 1", "")]
        content = self._merged_txt(tmp_path, lectures)
        assert "\n\n\n" not in content

    def test_trailing_newline_in_transcript_no_double_blank(self, tmp_path):
        lectures = [_minimal_lecture("L1", "Week 1", "some text\n")]
        content = self._merged_txt(tmp_path, lectures)
        assert "\n\n\n" not in content
