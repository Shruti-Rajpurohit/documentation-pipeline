from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


_DOCUMENTATION_EXTENSIONS = {".md", ".mdx", ".rst", ".adoc", ".asciidoc"}
_EXCLUDED_DIRECTORIES = {".git", ".venv", "venv", "node_modules", "vendor", "build", "dist", "__pycache__"}
_HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
_LOW_SIGNAL_TERMS = {
	"and", "are", "class", "def", "else", "false", "from", "function", "import", "none",
	"null", "return", "self", "true", "with",
}


class DocumentSearchLimitError(RuntimeError):
	pass


@dataclass(frozen=True)
class DocumentSection:
	path: str
	heading: str
	start_line: int
	end_line: int
	content: str
	matched_terms: tuple[str, ...]


def find_candidate_sections(
	repo_path: str | Path,
	changed_files: tuple[str, ...],
	patch: str,
	*,
	limit: int = 10,
	max_files: int = 1_000,
	max_file_bytes: int = 1_000_000,
) -> list[DocumentSection]:
	if limit < 1 or max_files < 1 or max_file_bytes < 1:
		raise ValueError("Search limits must be positive")

	root = Path(repo_path).resolve()
	terms = _extract_terms(changed_files, patch)
	if not terms:
		return []

	candidates: list[tuple[int, DocumentSection]] = []
	scanned_files = 0
	for current_root, directories, filenames in os.walk(root, followlinks=False):
		directories[:] = [
			name
			for name in directories
			if name not in _EXCLUDED_DIRECTORIES and not (Path(current_root) / name).is_symlink()
		]
		for filename in filenames:
			path = Path(current_root) / filename
			if path.suffix.lower() not in _DOCUMENTATION_EXTENSIONS or path.is_symlink():
				continue
			scanned_files += 1
			if scanned_files > max_files:
				raise DocumentSearchLimitError(f"Documentation scan exceeds the {max_files}-file limit")
			try:
				if path.stat().st_size > max_file_bytes:
					continue
				text = path.read_text(encoding="utf-8")
			except (OSError, UnicodeError):
				continue

			relative_path = path.relative_to(root).as_posix()
			for section in _split_sections(relative_path, text):
				matched_terms = tuple(
					term for term in terms if _contains_term(section.content, term)
				)
				if matched_terms:
					candidates.append(
						(
							len(matched_terms),
							DocumentSection(
								path=section.path,
								heading=section.heading,
								start_line=section.start_line,
								end_line=section.end_line,
								content=section.content,
								matched_terms=matched_terms,
							),
						)
					)

	return _rank_candidates(candidates, limit)


def _extract_terms(changed_files: tuple[str, ...], patch: str) -> tuple[str, ...]:
	terms: list[str] = []
	seen: set[str] = set()

	def add_term(term: str) -> None:
		normalized = term.lower()
		if len(term) < 3 or normalized in _LOW_SIGNAL_TERMS or normalized in seen:
			return
		seen.add(normalized)
		terms.append(term)

	for path in changed_files:
		add_term(Path(path).stem)
	for line in patch.splitlines():
		if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
			for term in _IDENTIFIER_PATTERN.findall(line[1:]):
				add_term(term)
				if len(terms) >= 50:
					return tuple(terms)
	return tuple(terms)


def _contains_term(content: str, term: str) -> bool:
	return re.search(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])", content, re.IGNORECASE) is not None


def _split_sections(path: str, text: str) -> list[DocumentSection]:
	lines = text.splitlines()
	sections: list[DocumentSection] = []
	heading = Path(path).name
	start_line = 1
	content_lines: list[str] = []

	def append_section(end_line: int) -> None:
		content = "\n".join(content_lines).strip()
		if content:
			sections.append(
				DocumentSection(
					path=path,
					heading=heading,
					start_line=start_line,
					end_line=end_line,
					content=content,
					matched_terms=(),
				)
			)

	for line_number, line in enumerate(lines, start=1):
		match = _HEADING_PATTERN.match(line)
		if match:
			append_section(line_number - 1)
			heading = match.group(1).strip()
			start_line = line_number
			content_lines = [line]
		else:
			content_lines.append(line)
	append_section(len(lines))
	return sections


def _rank_candidates(
	candidates: list[tuple[int, DocumentSection]],
	limit: int,
) -> list[DocumentSection]:
	candidates.sort(key=lambda candidate: (-candidate[0], candidate[1].path, candidate[1].start_line))
	return [section for _, section in candidates[:limit]]
