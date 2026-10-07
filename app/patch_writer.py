from __future__ import annotations

import difflib
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from app.llm_client import DocumentEdit


class PatchError(ValueError):
	pass


class StalePatchError(RuntimeError):
	pass


_DOCUMENTATION_EXTENSIONS = {".md", ".mdx", ".rst", ".adoc", ".asciidoc"}
_HEADING_PATTERN = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True)
class FilePatch:
	path: str
	original_sha256: str
	before: str
	after: str
	unified_diff: str


@dataclass(frozen=True)
class PreparedPatch:
	files: tuple[FilePatch, ...]


def prepare_patch(repo_path: str | Path, edits: Iterable[DocumentEdit]) -> PreparedPatch:
	root = Path(repo_path).resolve()
	grouped_edits: dict[str, list[DocumentEdit]] = {}
	for edit in edits:
		_validate_relative_path(edit.path)
		grouped_edits.setdefault(edit.path, []).append(edit)

	if not grouped_edits:
		raise PatchError("Cannot prepare a patch with no document edits")

	file_patches: list[FilePatch] = []
	for relative_path, file_edits in sorted(grouped_edits.items()):
		path = root.joinpath(*PurePosixPath(relative_path).parts)
		if path.is_symlink() or not path.is_file() or path.suffix.lower() not in _DOCUMENTATION_EXTENSIONS:
			raise PatchError(f"Target is not an existing documentation file: {relative_path}")
		try:
			path.resolve(strict=True).relative_to(root)
			before = path.read_text(encoding="utf-8")
		except (OSError, UnicodeError, ValueError) as error:
			raise PatchError(f"Unable to read documentation file: {relative_path}") from error

		after = before
		seen_sections: set[str] = set()
		for edit in file_edits:
			if edit.section in seen_sections:
				raise PatchError(f"Multiple edits target the same section: {relative_path} / {edit.section}")
			seen_sections.add(edit.section)
			after = _apply_edit(after, relative_path, edit)

		diff = "".join(
			difflib.unified_diff(
				before.splitlines(keepends=True),
				after.splitlines(keepends=True),
				fromfile=relative_path,
				tofile=relative_path,
			)
		)
		file_patches.append(
			FilePatch(
				path=relative_path,
				original_sha256=hashlib.sha256(before.encode("utf-8")).hexdigest(),
				before=before,
				after=after,
				unified_diff=diff,
			)
		)

	return PreparedPatch(files=tuple(file_patches))


def apply_approved_patch(
	repo_path: str | Path,
	prepared_patch: PreparedPatch,
	*,
	approved: bool = False,
) -> None:
	if not approved:
		raise PatchError("Human approval is required before applying documentation changes")

	root = Path(repo_path).resolve()
	targets: list[tuple[Path, FilePatch]] = []
	for file_patch in prepared_patch.files:
		_validate_relative_path(file_patch.path)
		path = root.joinpath(*PurePosixPath(file_patch.path).parts)
		if path.is_symlink() or not path.is_file():
			raise StalePatchError(f"Patch target is no longer a regular file: {file_patch.path}")
		try:
			path.resolve(strict=True).relative_to(root)
			current = path.read_bytes()
		except (OSError, ValueError) as error:
			raise StalePatchError(f"Patch target is no longer available: {file_patch.path}") from error
		if hashlib.sha256(current).hexdigest() != file_patch.original_sha256:
			raise StalePatchError(f"Patch target changed after review: {file_patch.path}")
		targets.append((path, file_patch))

	temporary_paths: list[Path] = []
	try:
		for path, file_patch in targets:
			with tempfile.NamedTemporaryFile(
				mode="w",
				encoding="utf-8",
				dir=path.parent,
				prefix=f".{path.name}.",
				suffix=".tmp",
				delete=False,
			) as temporary_file:
				temporary_file.write(file_patch.after)
				temporary_file.flush()
				os.fsync(temporary_file.fileno())
				temporary_paths.append(Path(temporary_file.name))
		for temporary_path, (path, _) in zip(temporary_paths, targets):
			os.replace(temporary_path, path)
	finally:
		for temporary_path in temporary_paths:
			temporary_path.unlink(missing_ok=True)


def _validate_relative_path(relative_path: str) -> None:
	parsed = PurePosixPath(relative_path)
	if not relative_path or parsed.is_absolute() or ".." in parsed.parts or "\\" in relative_path:
		raise PatchError(f"Unsafe documentation path: {relative_path}")


def _apply_edit(text: str, path: str, edit: DocumentEdit) -> str:
	lines = text.splitlines(keepends=True)
	headings: list[tuple[int, int, str]] = []
	for index, line in enumerate(lines):
		match = _HEADING_PATTERN.match(line.rstrip("\r\n"))
		if match:
			headings.append((index, len(match.group(1)), match.group(2).strip()))

	if not headings:
		if edit.section != Path(path).name:
			raise PatchError(f"Section not found in {path}: {edit.section}")
		start, end = 0, len(lines)
		heading_line = ""
	else:
		matches = [heading for heading in headings if heading[2] == edit.section]
		if edit.start_line is not None:
			matches = [heading for heading in matches if heading[0] + 1 == edit.start_line]
		if len(matches) != 1:
			raise PatchError(f"Section is missing or ambiguous in {path}: {edit.section}")
		start, _, _ = matches[0]
		end = len(lines)
		for next_index, _, _ in headings:
			if next_index > start:
				end = next_index
				break
		heading_line = lines[start].rstrip("\r\n")

	replacement_content = edit.content.strip()
	if not replacement_content:
		raise PatchError(f"Section content cannot be empty: {path} / {edit.section}")
	if edit.action == "append":
		existing = "".join(lines[start + 1 : end]) if heading_line else "".join(lines[start:end])
		replacement_content = f"{existing.rstrip()}\n\n{replacement_content}" if existing.strip() else replacement_content

	replacement = []
	if heading_line:
		replacement.extend([heading_line, "\n", "\n"])
	replacement.extend([replacement_content, "\n"])
	lines[start:end] = replacement
	return "".join(lines)
