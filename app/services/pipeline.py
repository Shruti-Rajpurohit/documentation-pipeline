from __future__ import annotations
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Protocol
from app.change_analyzer import analyze_change
from app.document_finder import DocumentSearchLimitError, DocumentSection, find_candidate_sections
from app.git_tools import collect_git_diff
from app.llm_client import DocumentationProposal, build_documentation_prompt, parse_proposal, TextGenerator
from app.models.pipeline import PipelineDraft, PipelineOutcome
from app.patch_writer import PreparedPatch, prepare_patch

PROMPT_VERSION = "v3"
PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "documentation_update.txt"

def analyze_impact_report(source_diff: str, document_context: list[DocumentSection], model: TextGenerator) -> str:
    doc_index = json.dumps([{"path": s.path, "heading": s.heading} for s in document_context], indent=2)
    prompt = f"You are a technical analyst. Analyze this git diff and the provided doc index. Identify which sections need updating or if new sections are needed. Return a JSON object with 'decision' (update_required/no_update), 'create_new_section' (bool), and 'technical_summary'.\n\nDIFF:\n{source_diff}\n\nDOC INDEX:\n{doc_index}"
    return model.generate(prompt)

def generate_doc_proposal(impact_report: str, sections: list[DocumentSection], source_diff: str, model: TextGenerator, template: str) -> DocumentationProposal:
    # We integrate the impact report directly into the source context for the writer
    prompt = template.replace("{{source_context}}", f"IMPACT ANALYSIS:\n{impact_report}\n\nRAW DIFF:\n{source_diff}")
    doc_ctx = json.dumps([{"path": s.path, "heading": s.heading, "content": s.content} for s in sections], indent=2)
    prompt = prompt.replace("{{document_context}}", doc_ctx)
    
    response = model.generate(prompt)
    return parse_proposal(response, {}, set()) # Permissive parsing

def audit_proposal(proposal: DocumentationProposal, source_diff: str, model: TextGenerator) -> bool:
    prompt = f"Review this doc proposal against the source diff. Does it invent facts not present in the code? Return 'VALID' or 'INVALID'.\n\nPROPOSAL: {proposal}\n\nDIFF: {source_diff}"
    response = model.generate(prompt).lower()
    return "valid" in response

async def analyze_repository_change(source_repo: str|Path, docs_repo: str|Path, base_revision: str, head_revision: str, model_client: TextGenerator, *, prompt_template: str|None = None, model_name: str = "model") -> PipelineDraft:
    source_diff = collect_git_diff(source_repo, base_revision, head_revision)
    analysis = analyze_change(source_diff)
    
    if analysis.decision != "update_required":
        return PipelineDraft(source_diff=source_diff, analysis=analysis, sections=(), proposal=None, prepared_patch=None, raw_response=None, outcome=analysis.decision, note=analysis.rationale)

    try:
        sections = find_candidate_sections(docs_repo, source_diff.changed_files, source_diff.patch)
        if not sections:
            return PipelineDraft(source_diff=source_diff, analysis=analysis, sections=(), proposal=None, prepared_patch=None, raw_response=None, outcome="human_investigation", note="No matching sections found")
        
        # STAGE 1: Analyst
        impact = await asyncio.to_thread(analyze_impact_report, source_diff.patch, sections, model_client)
        
        # STAGE 2: Writer
        template = prompt_template or PROMPT_PATH.read_text(encoding="utf-8")
        proposal = await asyncio.to_thread(generate_doc_proposal, impact, sections, source_diff.patch, model_client, template)
        
        # STAGE 3: Auditor
        is_valid = await asyncio.to_thread(audit_proposal, proposal, source_diff.patch, model_client)
        
        if not is_valid:
            return PipelineDraft(source_diff=source_diff, analysis=analysis, sections=tuple(sections), proposal=None, prepared_patch=None, raw_response=None, outcome="human_investigation", note="Auditor detected hallucinations")
            
        prepared_patch = prepare_patch(docs_repo, proposal.changes)
        return PipelineDraft(source_diff=source_diff, analysis=analysis, sections=tuple(sections), proposal=proposal, prepared_patch=prepared_patch, raw_response="Success", outcome="awaiting_review", note="")
    except Exception as e:
        return PipelineDraft(source_diff=source_diff, analysis=analysis, sections=(), proposal=None, prepared_patch=None, raw_response=None, outcome="human_investigation", note=str(e))