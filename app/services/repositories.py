from __future__ import annotations

from pathlib import Path


class RepositoryConfigurationError(RuntimeError):
	pass


class RepositoryAccessError(PermissionError):
	pass


def validate_repository_paths(
	source_repo: str,
	docs_repo: str,
	allowed_roots: str | None,
) -> tuple[str, str]:
	if not allowed_roots or not allowed_roots.strip():
		raise RepositoryConfigurationError("REPOSITORY_ROOTS is not configured")

	roots: list[Path] = []
	for configured_root in allowed_roots.split(","):
		if not configured_root.strip():
			continue
		root = Path(configured_root.strip()).expanduser().resolve(strict=True)
		if not root.is_dir():
			raise RepositoryConfigurationError("A configured repository root is not a directory")
		roots.append(root)
	if not roots:
		raise RepositoryConfigurationError("REPOSITORY_ROOTS does not contain a valid root")

	resolved_repositories: list[str] = []
	for repository in (source_repo, docs_repo):
		path = Path(repository).expanduser().resolve(strict=True)
		if not path.is_dir():
			raise ValueError("Repository paths must be directories")
		if not any(path.is_relative_to(root) for root in roots):
			raise RepositoryAccessError("Repository path is outside configured repository roots")
		resolved_repositories.append(str(path))
	return resolved_repositories[0], resolved_repositories[1]