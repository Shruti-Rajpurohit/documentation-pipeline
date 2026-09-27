from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitCommandError(RuntimeError):
	pass


class InvalidRevisionError(ValueError):
	pass


class DiffLimitExceededError(ValueError):
	pass


@dataclass(frozen=True)
class GitDiff:
	base_commit: str
	head_commit: str
	changed_files: tuple[str, ...]
	patch: str


def collect_git_diff(
	repo_path: str | Path,
	base_revision: str,
	head_revision: str,
	*,
	max_files: int = 100,
	max_patch_bytes: int = 250_000,
) -> GitDiff:
	if max_files < 1 or max_patch_bytes < 1:
		raise ValueError("Diff limits must be positive")

	repository = Path(repo_path).resolve()
	base_commit = _resolve_commit(repository, base_revision)
	head_commit = _resolve_commit(repository, head_revision)

	changed = _run_git(
		repository,
		["diff", "--name-only", "-z", "--no-ext-diff", base_commit, head_commit, "--"],
	)
	changed_files = tuple(
		path for path in changed.stdout.decode("utf-8", errors="surrogateescape").split("\0") if path
	)
	if len(changed_files) > max_files:
		raise DiffLimitExceededError(f"Diff changes {len(changed_files)} files; limit is {max_files}")

	diff = _run_git(
		repository,
		["diff", "--no-ext-diff", "--no-color", "--unified=3", base_commit, head_commit, "--"],
	)
	if len(diff.stdout) > max_patch_bytes:
		raise DiffLimitExceededError(f"Diff exceeds the {max_patch_bytes}-byte limit")

	return GitDiff(
		base_commit=base_commit,
		head_commit=head_commit,
		changed_files=changed_files,
		patch=diff.stdout.decode("utf-8", errors="replace"),
	)


def _resolve_commit(repository: Path, revision: str) -> str:
	result = _run_git(
		repository,
		["rev-parse", "--verify", "--quiet", "--end-of-options", f"{revision}^{{commit}}"],
		check=False,
	)
	if result.returncode != 0:
		raise InvalidRevisionError(f"Not a valid commit revision: {revision}")
	return result.stdout.decode("ascii").strip()


def _run_git(
	repository: Path,
	arguments: list[str],
	*,
	check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
	try:
		result = subprocess.run(
			["git", "-C", str(repository), *arguments],
			capture_output=True,
			check=False,
		)
	except FileNotFoundError as error:
		raise GitCommandError("Git executable was not found") from error

	if check and result.returncode != 0:
		message = result.stderr.decode("utf-8", errors="replace").strip()
		raise GitCommandError(message or "Git command failed")
	return result
