"""Which SHAs the GitHub mirror holds.

A wrong "no" here is invisible: the link routes back to gitlab.kitware.com,
which still serves it, so the page looks right while the archive sends the
reader to the host it exists to take load off. 82,152 SHAs at once.
"""

from __future__ import annotations

import subprocess

import pytest

from crawler import commits


@pytest.fixture
def clone(tmp_path, monkeypatch):
    """A real one-commit repository, so the answer is checkable."""
    repo = tmp_path / "repo.git"
    subprocess.run(["git", "init", "-q", "--bare", str(repo)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    (work / "f.txt").write_text("x")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(["git", "-C", str(work), "add", "f.txt"], check=True, env=env)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "c"], check=True, env=env)
    sha = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(work), "push", "-q", str(repo), "HEAD:refs/heads/main"],
        check=True,
        env=env,
    )
    monkeypatch.setattr(commits, "CLONE", repo)
    return sha


def test_a_commit_the_clone_holds_reads_true(clone):
    assert commits.resolve({clone}) == {clone: True}


def test_a_commit_the_clone_lacks_reads_false(clone):
    absent = "b" * 40
    assert commits.resolve({clone, absent}) == {clone: True, absent: False}


def test_a_broken_clone_raises_instead_of_answering_false(tmp_path, monkeypatch):
    """`check=False` with an unread return code mapped all 82,152 SHAs to
    False and routed every commit link back to GitLab, silently."""
    monkeypatch.setattr(commits, "CLONE", tmp_path / "not-a-repo")
    with pytest.raises(RuntimeError, match="cat-file"):
        commits.resolve({"a" * 40})


def test_nothing_to_ask_is_not_a_failure(clone):
    assert commits.resolve(set()) == {}
