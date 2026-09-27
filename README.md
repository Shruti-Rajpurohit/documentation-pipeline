# Automated Documentation Pipeline

A local-first prototype that compares product repository revisions, finds related documentation, asks a local model for an evidence-linked patch, and presents the draft for human review. Nothing is written to the docs working tree until someone approves the draft in the local dashboard.

## Requirements

- Python 3.10 or newer
- Git
- Ollama with a pinned model available locally
- A source repository with at least two commits
- A documentation working tree containing Markdown, MDX, reStructuredText, or AsciiDoc files

The application uses only Python's standard library; no package installation is needed.

## Run

Start Ollama and pull the model you want to evaluate:

```sh
ollama pull "MODEL:VERSION"
```

Replace `MODEL:VERSION` with the exact model tag you evaluated and intend to use. Run a review against a source commit range:

```sh
python -m app.main \
  --source-repo /path/to/product-repo \
  --docs-repo /path/to/docs-working-tree \
  --base <base-commit> \
  --head <head-commit> \
  --model <pinned-ollama-model>
```

The source and docs paths may refer to the same repository. The dashboard binds to `127.0.0.1:8765` by default; open the printed local URL to inspect the diff, evidence, generated patch, and suggested checks. The model and prompt versions are shown with the draft.

Use `python -m app.main --help` for all options. The Ollama URL, dashboard port, and loopback bind address can be configured. The dashboard rejects non-loopback bind addresses.

## Review Behavior

- Source diffs are bounded to 100 files and 250 KB.
- Documentation search is exact-term based and bounded to 1,000 files and 1 MB per file. Exceeding the scan limit stops for human investigation.
- Model proposals must be valid JSON, cite supplied source evidence, and target a retrieved document section. The patch writer independently restricts targets to existing documentation files under the configured docs root.
- Previewing a patch does not modify files. Editing updates only the draft. Rejecting closes the review without writing. Approval checks that reviewed files have not changed since the preview and then applies the patch.
- The HTTP review server is intended for one local user and one draft at a time. It is not an authenticated, multi-user service.

## Current Limitations

- Publishing currently applies approved changes to the docs working tree only. It does not create a commit, branch, pull request, or push to GitHub. The docs destination and publication policy still need to be selected.
- There is no GitHub Actions/webhook trigger; runs are manual.
- Retrieval uses exact identifiers and changed file names, not embeddings or a vector database. Missing or ambiguous matches stop for human investigation.
- The analyzer uses conservative file-type heuristics. It does not perform semantic OpenAPI compatibility analysis.
- Suggested checks are displayed but are not executed automatically.
- No real Ollama quality evaluation has been run yet; choose and benchmark a pinned model for the target repository before relying on its drafts.

## Tests

Run the standard-library test suite:

```sh
python -m unittest discover -s tests -v
```