from __future__ import annotations

import difflib
import html
import ipaddress
from dataclasses import dataclass
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse

from app.change_analyzer import ChangeAnalysis
from app.document_finder import DocumentSection
from app.git_tools import GitDiff
from app.llm_client import DocumentationProposal
from app.patch_writer import FilePatch, PatchError, PreparedPatch, StalePatchError, apply_approved_patch


ReviewStatus = Literal["awaiting_review", "approved", "rejected", "no_update", "human_investigation"]


@dataclass
class ReviewDraft:
    source_diff: GitDiff
    analysis: ChangeAnalysis
    sections: list[DocumentSection]
    proposal: DocumentationProposal | None
    prepared_patch: PreparedPatch | None
    target_repo: Path
    status: ReviewStatus
    note: str = ""
    model_name: str = "not used"
    prompt_version: str = "v1"


class ReviewSession:
    def __init__(self, draft: ReviewDraft) -> None:
        self.draft = draft

    def edit(self, path: str, content: str) -> None:
        self._require_pending()
        if not content.strip() or self.draft.prepared_patch is None:
            raise ValueError("A non-empty patch preview is required for editing")

        updated_files: list[FilePatch] = []
        found = False
        for file_patch in self.draft.prepared_patch.files:
            if file_patch.path != path:
                updated_files.append(file_patch)
                continue
            found = True
            updated_files.append(
                FilePatch(
                    path=file_patch.path,
                    original_sha256=file_patch.original_sha256,
                    before=file_patch.before,
                    after=content,
                    unified_diff="".join(
                        difflib.unified_diff(
                            file_patch.before.splitlines(keepends=True),
                            content.splitlines(keepends=True),
                            fromfile=path,
                            tofile=path,
                        )
                    ),
                )
            )
        if not found:
            raise ValueError(f"Document is not part of this review: {path}")
        self.draft.prepared_patch = PreparedPatch(files=tuple(updated_files))
        self.draft.note = "Reviewed content saved to the draft."

    def approve(self) -> None:
        self._require_pending()
        if self.draft.proposal is None or self.draft.proposal.decision != "update_required":
            raise ValueError("Only an update proposal can be approved")
        if self.draft.prepared_patch is None:
            raise ValueError("There is no prepared documentation patch to approve")
        apply_approved_patch(self.draft.target_repo, self.draft.prepared_patch, approved=True)
        self.draft.status = "approved"
        self.draft.note = "Approved changes were applied to the configured documentation working tree."

    def reject(self) -> None:
        self._require_pending()
        self.draft.status = "rejected"
        self.draft.note = "Draft rejected; documentation files were not changed."

    def _require_pending(self) -> None:
        if self.draft.status != "awaiting_review":
            raise ValueError(f"Review is not awaiting a decision: {self.draft.status}")


def render_dashboard(session: ReviewSession, message: str = "") -> str:
    draft = session.draft
    status = html.escape(draft.status.replace("_", " ").title())
    source_diff = html.escape(draft.source_diff.patch)
    sections_html = "".join(
        "<article class=\"section\">"
        f"<h3>{html.escape(section.path)} · {html.escape(section.heading)}</h3>"
        f"<p class=\"meta\">Lines {section.start_line}-{section.end_line}; "
        f"matched: {html.escape(', '.join(section.matched_terms))}</p>"
        f"<pre>{html.escape(section.content)}</pre></article>"
        for section in draft.sections
    ) or "<p>No documentation sections were selected.</p>"

    patch_html = ""
    if draft.prepared_patch is not None:
        patch_html = "".join(
            "<article class=\"patch\">"
            f"<h3>{html.escape(file_patch.path)}</h3>"
            f"<pre>{html.escape(file_patch.unified_diff)}</pre>"
            "<form method=\"post\" action=\"/edit\">"
            f"<input type=\"hidden\" name=\"path\" value=\"{html.escape(file_patch.path, quote=True)}\">"
            f"<label>Edit proposed file content<textarea name=\"content\" required>{html.escape(file_patch.after)}</textarea></label>"
            "<button type=\"submit\">Save edit</button></form></article>"
            for file_patch in draft.prepared_patch.files
        )

    proposal_html = "<p>No model proposal was generated.</p>"
    if draft.proposal is not None:
        proposal_html = (
            f"<p><strong>Decision:</strong> {html.escape(draft.proposal.decision.replace('_', ' '))}</p>"
            f"<p>{html.escape(draft.proposal.rationale)}</p>"
            f"<p><strong>Uncertainties:</strong> {html.escape('; '.join(draft.proposal.uncertainties) or 'None')}</p>"
            f"<p><strong>Suggested checks:</strong> {html.escape('; '.join(draft.proposal.tests_to_run) or 'None')}</p>"
        )

    actions_html = ""
    if draft.status == "awaiting_review":
        actions_html = (
            "<form class=\"actions\" method=\"post\" action=\"/approve\">"
            "<button class=\"approve\" type=\"submit\">Approve and apply</button></form>"
            "<form class=\"actions\" method=\"post\" action=\"/reject\">"
            "<button class=\"reject\" type=\"submit\">Reject</button></form>"
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Documentation review</title>
<style>
:root {{ color-scheme: light; --ink: #172b2a; --muted: #536765; --line: #cad7d3; --paper: #f4f7f3; --white: #fff; --green: #176b52; --red: #a83232; }}
* {{ box-sizing: border-box; }} body {{ margin: 0; background: var(--paper); color: var(--ink); font: 16px/1.5 Georgia, serif; }}
header {{ padding: 28px max(22px, calc((100vw - 1100px) / 2)); color: white; background: #183d39; }}
h1 {{ margin: 0; font-size: 28px; }} header p {{ margin: 5px 0 0; color: #c6ded4; }} main {{ max-width: 1100px; margin: 24px auto; padding: 0 22px 50px; }}
section {{ margin: 20px 0; padding: 18px 0; border-top: 1px solid var(--line); }} h2 {{ margin: 0 0 12px; font-size: 21px; }} h3 {{ overflow-wrap: anywhere; font-size: 16px; }}
.summary {{ display: flex; flex-wrap: wrap; gap: 8px 24px; }} .meta {{ color: var(--muted); font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }}
pre {{ overflow: auto; padding: 14px; border: 1px solid var(--line); background: var(--white); color: #213a35; font: 13px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }}
.section, .patch {{ margin: 14px 0; }} textarea {{ display: block; width: 100%; min-height: 180px; margin: 7px 0 10px; padding: 12px; border: 1px solid #94aaa2; background: white; color: var(--ink); font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }}
button {{ min-height: 42px; padding: 8px 14px; border: 0; background: #e1eae5; color: var(--ink); font: bold 15px Georgia, serif; cursor: pointer; }} button:hover {{ filter: brightness(.95); }} .approve {{ background: var(--green); color: white; }} .reject {{ color: var(--red); }}
.actions {{ display: inline-block; margin: 8px 8px 0 0; }} .notice {{ padding: 10px 12px; border-left: 4px solid var(--green); background: white; }}
@media (max-width: 600px) {{ header {{ padding: 20px 18px; }} main {{ margin-top: 10px; padding: 0 16px 36px; }} }}
</style></head><body><header><h1>Documentation review</h1><p>Local change-impact draft</p></header><main>
<p class="notice"><strong>Status:</strong> {status}{f" · {html.escape(message or draft.note)}" if message or draft.note else ""}</p>
<p class="meta">Model: {html.escape(draft.model_name)} · Prompt: {html.escape(draft.prompt_version)}</p>
<section><h2>Change analysis</h2><div class="summary"><span><strong>Decision:</strong> {html.escape(draft.analysis.decision.replace('_', ' '))}</span>
<span><strong>Commits:</strong> {html.escape(draft.analysis.base_commit[:12])} → {html.escape(draft.analysis.head_commit[:12])}</span></div>
<p>{html.escape(draft.analysis.rationale)}</p><p class="meta">Evidence: {html.escape(', '.join(draft.analysis.evidence_refs))}</p></section>
<section><h2>Source diff</h2><p class="meta">Changed files: {html.escape(', '.join(draft.analysis.changed_files))}</p><pre>{source_diff}</pre></section>
<section><h2>Documentation context</h2>{sections_html}</section>
<section><h2>Model proposal</h2>{proposal_html}</section>
<section><h2>Proposed document patch</h2>{patch_html or '<p>No patch is available.</p>'}</section>
<section>{actions_html}</section></main></body></html>"""


class ReviewRequestHandler(BaseHTTPRequestHandler):
    def __init__(self, *args: object, session: ReviewSession, **kwargs: object) -> None:
        self.session = session
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        if self.path != "/":
            self.send_error(404)
            return
        self._send_html(render_dashboard(self.session, self.session.draft.note))

    def do_POST(self) -> None:
        if not _is_loopback(self.client_address[0]) or not self._valid_origin():
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 1_000_000:
                self.send_error(413)
                return
            form = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            if self.path == "/approve":
                self.session.approve()
            elif self.path == "/reject":
                self.session.reject()
            elif self.path == "/edit":
                self.session.edit(_form_value(form, "path"), _form_value(form, "content"))
            else:
                self.send_error(404)
                return
        except (UnicodeDecodeError, ValueError, PatchError, StalePatchError) as error:
            self._send_html(render_dashboard(self.session, str(error)), status=400)
            return
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format_string: str, *args: object) -> None:
        return

    def _valid_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        hostname = urlparse(origin).hostname
        if hostname is None:
            return False
        return hostname == "localhost" or _is_loopback(hostname)

    def _send_html(self, content: str, status: int = 200) -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)


def serve_review(session: ReviewSession, host: str = "127.0.0.1", port: int = 8765) -> None:
    if not _is_loopback(host):
        raise ValueError("The review dashboard must bind to a loopback address")
    server = HTTPServer((host, port), partial(ReviewRequestHandler, session=session))
    print(f"Review dashboard: http://{host}:{server.server_port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _form_value(form: dict[str, list[str]], key: str) -> str:
    values = form.get(key)
    if not values:
        raise ValueError(f"Missing form field: {key}")
    return values[0]


def _is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return address == "localhost"