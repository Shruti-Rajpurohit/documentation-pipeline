from dataclasses import dataclass
from typing import Literal

from app.git_tools import GitDiff


ChangeDecision = Literal["update_required", "no_update", "human_investigation"]

_DOCUMENTATION_EXTENSIONS = {".md", ".mdx", ".rst", ".adoc", ".asciidoc"}
_SOURCE_EXTENSIONS = {
	".c",
	".cc",
	".cpp",
	".cs",
	".go",
	".h",
	".hpp",
	".java",
	".js",
	".jsx",
	".php",
	".py",
	".rb",
	".rs",
	".scala",
	".sh",
	".sql",
	".swift",
	".ts",
	".tsx",
}
_CONFIG_EXTENSIONS = {".cfg", ".conf", ".ini", ".json", ".properties", ".toml", ".xml", ".yaml", ".yml"}
_TEXT_EXTENSIONS = _DOCUMENTATION_EXTENSIONS | _SOURCE_EXTENSIONS | _CONFIG_EXTENSIONS | {
	".css",
	".csv",
	".htm",
	".html",
	".text",
	".txt",
	".tsv",
}
_TEST_DIRECTORY_NAMES = {"test", "tests", "spec", "specs"}


@dataclass(frozen=True)
class ChangeAnalysis:
	decision: ChangeDecision
	changed_files: tuple[str, ...]
	evidence_refs: tuple[str, ...]
	rationale: str
	base_commit: str
	head_commit: str


def analyze_change(git_diff: GitDiff) -> ChangeAnalysis:
	changed_files = git_diff.changed_files
	evidence_refs = tuple(f"{git_diff.head_commit}:{path}" for path in changed_files)

	if not changed_files:
		decision: ChangeDecision = "no_update"
		rationale = "The selected revisions contain no changed files."
	else:
		categories = {_classify_path(path) for path in changed_files}
		if categories == {"documentation"}:
			decision = "no_update"
			rationale = "Only documentation files changed; no product-source documentation review is needed."
		elif categories & {"source", "configuration"}:
			decision = "update_required"
			rationale = "Product source, API schema, or configuration changed and should be checked for documentation impact."
		elif categories & {"unknown", "tests"}:
			decision = "human_investigation"
			rationale = "The change includes unfamiliar or test-only files, so its documentation impact is unclear."
		else:
			decision = "update_required"
			rationale = "Product source, API schema, or configuration changed and should be checked for documentation impact."

	return ChangeAnalysis(
		decision=decision,
		changed_files=changed_files,
		evidence_refs=evidence_refs,
		rationale=rationale,
		base_commit=git_diff.base_commit,
		head_commit=git_diff.head_commit,
	)


def _classify_path(path: str) -> str:
	normalized = path.replace("\\", "/").lower()
	path_parts = normalized.split("/")
	name = path_parts[-1]
	suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""

	if suffix in _DOCUMENTATION_EXTENSIONS:
		return "documentation"
	if suffix in _TEXT_EXTENSIONS and any(part in {"docs", "documentation"} for part in path_parts[:-1]):
		return "documentation"
	if any(part in _TEST_DIRECTORY_NAMES for part in path_parts[:-1]) or name.startswith("test_") or name.endswith("_test.py"):
		return "tests"
	if suffix in _SOURCE_EXTENSIONS:
		return "source"
	if suffix in _CONFIG_EXTENSIONS or "openapi" in name or "swagger" in name or "schema" in name:
		return "configuration"
	if name in {"dockerfile", "makefile"}:
		return "configuration"
	return "unknown"
