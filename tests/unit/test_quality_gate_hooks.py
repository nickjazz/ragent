"""Subprocess black-box tests for .claude/hooks/* quality-gate scripts.

Per docs/00_rule.md §Shell Hook Testing: hooks are load-bearing quality
gates and every behaviour path needs a subprocess test against a temporary
git repo fixture, not a unit test against extracted bash functions.
"""

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STAMP_SCRIPT = REPO_ROOT / ".claude/hooks/stamp_pre_commit_approved.sh"
GATE_SCRIPT = REPO_ROOT / ".claude/hooks/pre_commit_gate.sh"
PUSH_GATE_SCRIPT = REPO_ROOT / ".claude/hooks/pre_push_gate.sh"

FRESHNESS = 3600  # must match PUSH_FRESHNESS / FULL_FRESHNESS in pre_push_gate.sh
SIMPLIFY_FULL = "simplify:full"
REVIEW_FULL = "review:full"


@pytest.fixture
def gate_repo(tmp_path):
    """A throwaway git repo with the real hook scripts and a stub Makefile.

    The hooks resolve ROOT via `git rev-parse --show-toplevel` and operate
    relative to it, so running them with cwd inside this repo fully
    isolates them from the real project's .claude/ state.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "Makefile").write_text("format:\n\t@true\n\nlint:\n\t@true\n")
    subprocess.run(["git", "add", "Makefile"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    hooks_dir = repo / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "stamp_pre_commit_approved.sh").write_text(STAMP_SCRIPT.read_text())
    (hooks_dir / "pre_commit_gate.sh").write_text(GATE_SCRIPT.read_text())
    return repo


def run_hook(repo, script, command):
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run(
        ["bash", str(repo / ".claude/hooks" / script)],
        cwd=repo,
        input=payload,
        capture_output=True,
        text=True,
    )


def stage_file(repo, rel_path, content):
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    subprocess.run(["git", "add", rel_path], cwd=repo, check=True)


def stage_default_change(repo):
    """Stage a trivial src/ change — triggers CODE_GATE without adding risk."""
    stage_file(repo, "src/ragent/foo.py", "def foo():\n    return 1\n")


def staged_diff_sha(repo):
    diff = subprocess.run(
        ["git", "diff", "--cached"], cwd=repo, check=True, capture_output=True
    ).stdout
    return hashlib.sha256(diff).hexdigest()


def write_marker(repo, sha, ts, by):
    (repo / ".claude/.pre_commit_approved").write_text(
        json.dumps({"diff_sha": sha, "ts": ts, "by": by})
    )


def append_audit(repo, sha, ts, by):
    with open(repo / ".claude/.stamp_audit.log", "a") as f:
        f.write(json.dumps({"ts": ts, "by": by, "diff_sha": sha}) + "\n")


def approve(repo, *, sha=None, now=None, simplify_ts=None, review_ts=None):
    """Write a marker + audit log that satisfies the gate for `sha`."""
    sha = sha or staged_diff_sha(repo)
    now = now if now is not None else int(time.time())
    simplify_ts = now if simplify_ts is None else simplify_ts
    review_ts = now if review_ts is None else review_ts
    write_marker(repo, sha, max(simplify_ts, review_ts), REVIEW_FULL)
    append_audit(repo, sha, simplify_ts, SIMPLIFY_FULL)
    append_audit(repo, sha, review_ts, REVIEW_FULL)
    return sha, now


# --- stamp_pre_commit_approved.sh -------------------------------------------


def run_stamp(repo, skill_arg, token_set=True):
    env = {**os.environ}
    if token_set:
        env["RAGENT_SKILL_INVOCATION_TOKEN"] = "1"
    else:
        env.pop("RAGENT_SKILL_INVOCATION_TOKEN", None)
    return subprocess.run(
        ["bash", str(repo / ".claude/hooks/stamp_pre_commit_approved.sh"), skill_arg],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def test_stamp_rejects_without_token(gate_repo):
    stage_file(gate_repo, "x.py", "x = 1\n")
    proc = run_stamp(gate_repo, "review", token_set=False)
    assert proc.returncode == 2
    assert "RAGENT_SKILL_INVOCATION_TOKEN" in proc.stderr
    assert not (gate_repo / ".claude/.pre_commit_approved").exists()


def test_stamp_rejects_invalid_skill_name(gate_repo):
    proc = run_stamp(gate_repo, "bogus")
    assert proc.returncode == 2
    assert "invalid skill name" in proc.stderr


def test_stamp_appends_valid_json_audit_entry(gate_repo):
    stage_file(gate_repo, "x.py", "x = 1\n")
    sha = staged_diff_sha(gate_repo)
    proc = run_stamp(gate_repo, "review")
    assert proc.returncode == 0
    audit_lines = (gate_repo / ".claude/.stamp_audit.log").read_text().strip().splitlines()
    last = json.loads(audit_lines[-1])
    assert last["by"] == REVIEW_FULL
    assert last["diff_sha"] == sha
    marker = json.loads((gate_repo / ".claude/.pre_commit_approved").read_text())
    assert marker["diff_sha"] == sha
    assert marker["by"] == REVIEW_FULL


# --- pre_commit_gate.sh: marker / audit-log gate ----------------------------


def test_gate_accepts_with_both_fresh_entries(gate_repo):
    stage_default_change(gate_repo)
    approve(gate_repo)
    result = run_hook(gate_repo, "pre_commit_gate.sh", 'git commit -m "[STRUCTURAL] x"')
    assert result.returncode == 0, result.stderr


def test_gate_accepts_when_audit_log_missing(gate_repo):
    # Commit gate no longer checks the audit log — /simplify+/review are
    # verified once per push (push gate), not once per commit. A commit that
    # carries only the marker file (no audit log) must still be accepted.
    stage_default_change(gate_repo)
    sha = staged_diff_sha(gate_repo)
    write_marker(gate_repo, sha, ts=int(time.time()), by=REVIEW_FULL)
    result = run_hook(gate_repo, "pre_commit_gate.sh", 'git commit -m "[STRUCTURAL] x"')
    assert result.returncode == 0, result.stderr


def test_gate_accepts_when_only_one_skill_in_audit_log(gate_repo):
    # Commit gate no longer cross-checks the audit log for both skills;
    # that check was moved to pre_push_gate.sh (per-push review gate).
    stage_default_change(gate_repo)
    sha = staged_diff_sha(gate_repo)
    now = int(time.time())
    write_marker(gate_repo, sha, now, REVIEW_FULL)
    append_audit(gate_repo, sha, now, REVIEW_FULL)
    result = run_hook(gate_repo, "pre_commit_gate.sh", 'git commit -m "[STRUCTURAL] x"')
    assert result.returncode == 0, result.stderr


def test_gate_accepts_when_audit_entries_are_stale(gate_repo):
    # Commit gate no longer enforces audit-log freshness — stale entries from
    # a previous /simplify+/review run do not block the commit; the push gate
    # enforces freshness with a 60-minute window at push time.
    stage_default_change(gate_repo)
    sha = staged_diff_sha(gate_repo)
    now = int(time.time())
    stale = now - FRESHNESS - 60
    write_marker(gate_repo, sha, now, REVIEW_FULL)
    append_audit(gate_repo, sha, stale, SIMPLIFY_FULL)
    append_audit(gate_repo, sha, stale, REVIEW_FULL)
    result = run_hook(gate_repo, "pre_commit_gate.sh", 'git commit -m "[STRUCTURAL] x"')
    assert result.returncode == 0, result.stderr


# --- pre_commit_gate.sh: _classify_risk auth/security boundary (PR #205 review) --


def test_classify_risk_low_for_authors_md_false_positive(gate_repo):
    stage_file(gate_repo, "AUTHORS.md", "Jane Doe\n")
    stage_default_change(gate_repo)
    approve(gate_repo)
    result = run_hook(gate_repo, "pre_commit_gate.sh", 'git commit -m "[STRUCTURAL] x"')
    assert result.returncode == 0, result.stderr
    assert not (gate_repo / ".claude/.pending_full_review").exists()


@pytest.mark.parametrize(
    "rel_path",
    [
        "src/ragent/auth_mode.py",
        "src/ragent/oauth_client.py",
        "src/ragent/authentication/handler.py",
        "src/ragent/authorization.py",
    ],
)
def test_classify_risk_high_for_auth_path_segment(gate_repo, rel_path):
    # Each alternative is staged and asserted in isolation (not consolidated
    # into one combined-paths test) because the gate's reason string comes
    # from a single grep over the whole staged set — a combined test would
    # pass even if only one alternative still matched, masking a regression
    # in the other three. Per-alternative isolation is the point of pinning.
    stage_file(gate_repo, rel_path, "MODE = 'oidc'\n")
    approve(gate_repo)
    result = run_hook(gate_repo, "pre_commit_gate.sh", 'git commit -m "[STRUCTURAL] x"')
    assert result.returncode == 0, result.stderr
    pending = json.loads((gate_repo / ".claude/.pending_full_review").read_text())
    assert "auth/security" in pending["reason"]


# --- pre_commit_gate.sh: _write_pending_if_changed skip-on-same-sha ----------


def test_write_pending_skips_update_when_sha_unchanged(gate_repo):
    # auth.py triggers the auth/security high-risk path → _write_pending_if_changed fires.
    # When .pending_full_review already carries the same diff_sha as the current
    # staged diff, the helper must skip the write to preserve existing timestamps
    # (author-only rebase scenario where diff content is unchanged).
    stage_file(gate_repo, "src/ragent/auth.py", "# stub\n")
    sha = staged_diff_sha(gate_repo)
    original_content = json.dumps(
        {"diff_sha": sha, "ts": int(time.time()) - 100, "reason": "auth/security"}
    )
    (gate_repo / ".claude" / ".pending_full_review").write_text(original_content)
    result = run_hook(gate_repo, "pre_commit_gate.sh", 'git commit -m "[STRUCTURAL] x"')
    assert result.returncode == 0, result.stderr
    assert (gate_repo / ".claude" / ".pending_full_review").read_text() == original_content


# --- pre_push_gate.sh: per-push review gate ----------------------------------


@pytest.fixture
def push_gate_repo(tmp_path):
    """Git repo with a local bare 'origin' remote for testing pre_push_gate.sh.

    The fixture pushes one base commit to origin (forming the upstream side),
    then adds one new commit locally so there is a non-empty push range
    (git diff origin/main...HEAD).
    """
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=repo, check=True)

    # Base commit — represents what's already in origin.
    (repo / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True)
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=repo, check=True)

    # New commit that forms the push range (not yet in origin).
    (repo / "change.txt").write_text("change\n")
    subprocess.run(["git", "add", "change.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=repo, check=True)

    hooks_dir = repo / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "pre_push_gate.sh").write_text(PUSH_GATE_SCRIPT.read_text())
    return repo


def run_push_hook(repo, command="git push"):
    payload = json.dumps({"tool_input": {"command": command}})
    return subprocess.run(
        ["bash", str(repo / ".claude/hooks/pre_push_gate.sh")],
        cwd=repo,
        input=payload,
        capture_output=True,
        text=True,
    )


def push_range_sha(repo, base="origin/main"):
    diff = subprocess.run(
        ["git", "diff", f"{base}...HEAD"], cwd=repo, check=True, capture_output=True
    ).stdout
    return hashlib.sha256(diff).hexdigest()


def test_push_gate_rejects_when_push_diff_not_reviewed(push_gate_repo):
    result = run_push_hook(push_gate_repo)
    assert result.returncode == 2
    assert "per-push review gate" in result.stderr


def test_push_gate_rejects_when_only_one_skill_stamped(push_gate_repo):
    sha = push_range_sha(push_gate_repo)
    append_audit(push_gate_repo, sha, int(time.time()), SIMPLIFY_FULL)
    result = run_push_hook(push_gate_repo)
    assert result.returncode == 2
    assert "per-push review gate" in result.stderr
    assert "review=no" in result.stderr


def test_push_gate_rejects_when_stamps_are_stale(push_gate_repo):
    sha = push_range_sha(push_gate_repo)
    stale = int(time.time()) - FRESHNESS - 60
    append_audit(push_gate_repo, sha, stale, SIMPLIFY_FULL)
    append_audit(push_gate_repo, sha, stale, REVIEW_FULL)
    result = run_push_hook(push_gate_repo)
    assert result.returncode == 2
    assert "per-push review gate" in result.stderr


def test_push_gate_passes_review_check_when_both_skills_stamped(push_gate_repo):
    # Gate must not block at the review step when stamps are present.
    # Failure at a later step (tests/lint) is irrelevant to this assertion.
    sha = push_range_sha(push_gate_repo)
    now = int(time.time())
    append_audit(push_gate_repo, sha, now, SIMPLIFY_FULL)
    append_audit(push_gate_repo, sha, now, REVIEW_FULL)
    result = run_push_hook(push_gate_repo)
    assert "per-push review gate" not in result.stderr


# --- pre_push_gate.sh: markdown bypass excludes contract docs ------------------


def test_push_gate_bypasses_non_contract_markdown(push_gate_repo):
    # A push whose entire diff is a non-contract .md file must skip all gates.
    # Sync origin to current HEAD first so the push range is markdown-only.
    subprocess.run(
        ["git", "push", "-q", "origin", "HEAD:main"],
        cwd=push_gate_repo,
        check=True,
    )
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=push_gate_repo, check=True)
    (push_gate_repo / "NOTES.md").write_text("notes\n")
    subprocess.run(["git", "add", "NOTES.md"], cwd=push_gate_repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add notes"],
        cwd=push_gate_repo,
        check=True,
    )
    result = run_push_hook(push_gate_repo)
    assert "markdown-only diff" in result.stderr
    assert result.returncode == 0


@pytest.mark.parametrize("contract_doc", ["docs/00_spec.md", "docs/00_plan.md"])
def test_push_gate_does_not_bypass_contract_markdown(push_gate_repo, contract_doc):
    # Pushes that include a contract doc must not bypass the review gate even if
    # the rest of the diff is pure markdown.
    # Sync origin so the push range is markdown-only (no change.txt noise).
    subprocess.run(
        ["git", "push", "-q", "origin", "HEAD:main"],
        cwd=push_gate_repo,
        check=True,
    )
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=push_gate_repo, check=True)
    path = push_gate_repo / contract_doc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# spec\n")
    subprocess.run(["git", "add", contract_doc], cwd=push_gate_repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "update spec"],
        cwd=push_gate_repo,
        check=True,
    )
    result = run_push_hook(push_gate_repo)
    assert "markdown-only diff" not in result.stderr
    assert result.returncode == 2
    assert "per-push review gate" in result.stderr


# --- pre_push_gate.sh: deleted Python files do not fail ruff ------------------


def test_push_gate_does_not_run_ruff_on_deleted_py_file(push_gate_repo):
    # When the push range deletes a .py file, the gate must not pass that path
    # to ruff (ruff exits 2 for missing files, which would block a valid push).
    # Seed a .py file in the base commit already pushed to origin.
    py_file = push_gate_repo / "todelete.py"
    py_file.write_text("x = 1\n")
    subprocess.run(["git", "add", "todelete.py"], cwd=push_gate_repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add py"],
        cwd=push_gate_repo,
        check=True,
    )
    subprocess.run(
        ["git", "push", "-q", "origin", "HEAD:main"],
        cwd=push_gate_repo,
        check=True,
    )
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=push_gate_repo, check=True)
    # Now delete it in a new commit (the push range).
    py_file.unlink()
    subprocess.run(["git", "rm", "-q", "todelete.py"], cwd=push_gate_repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "delete py"],
        cwd=push_gate_repo,
        check=True,
    )
    sha = push_range_sha(push_gate_repo)
    now = int(time.time())
    append_audit(push_gate_repo, sha, now, SIMPLIFY_FULL)
    append_audit(push_gate_repo, sha, now, REVIEW_FULL)
    result = run_push_hook(push_gate_repo)
    assert "format check failed" not in result.stderr
    assert "per-push review gate" not in result.stderr


# --- pre_push_gate.sh: pyproject.toml busts unit test cache -------------------


def test_push_gate_cache_busted_when_pyproject_changes(push_gate_repo):
    # A push that only changes pyproject.toml must not hit the unit test cache
    # even when src/ and tests/unit/ Python content is unchanged.
    (push_gate_repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    subprocess.run(["git", "add", "pyproject.toml"], cwd=push_gate_repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add pyproject"],
        cwd=push_gate_repo,
        check=True,
    )
    # Write a cache file that would match src/ragent + tests/unit hash (empty here)
    # but NOT account for pyproject.toml — so the gate must recompute and miss.
    stale_hash = "deadbeef"
    (push_gate_repo / ".claude" / ".unit_test_cache").write_text(stale_hash)
    sha = push_range_sha(push_gate_repo)
    now = int(time.time())
    append_audit(push_gate_repo, sha, now, SIMPLIFY_FULL)
    append_audit(push_gate_repo, sha, now, REVIEW_FULL)
    result = run_push_hook(push_gate_repo)
    assert "cache hit" not in result.stderr
