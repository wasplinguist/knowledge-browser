from pathlib import Path

import pytest

import scripts.run_eval_loop as cli
from scripts.run_eval_loop import ROOT, _outside_repo, _repository_roots


pytestmark = pytest.mark.unit


def test_generated_output_rejects_every_worktree_and_shared_git_directory(tmp_path):
    prohibited = _repository_roots()
    assert ROOT.resolve() in prohibited
    assert any(path.name == ".git" for path in prohibited)

    for root in prohibited:
        with pytest.raises(ValueError, match="outside every Git worktree"):
            _outside_repo(root / "generated-report")

    assert _outside_repo(tmp_path / "generated-report") == (
        tmp_path / "generated-report"
    ).resolve()


def test_source_state_requires_clean_files_and_changes_with_tracked_bytes(
    tmp_path, monkeypatch
):
    tracked = tmp_path / "search.py"
    tracked.write_text("before")
    monkeypatch.setattr(cli, "ROOT", tmp_path)

    def clean_git(*args):
        return "search.py\0" if args[0] == "ls-files" else ""

    monkeypatch.setattr(cli, "_git", clean_git)
    before = cli._source_state()
    tracked.write_text("after")
    assert cli._source_state() != before

    monkeypatch.setattr(cli, "_git", lambda *_args: " M search.py")
    with pytest.raises(ValueError, match="clean committed"):
        cli._source_state()
