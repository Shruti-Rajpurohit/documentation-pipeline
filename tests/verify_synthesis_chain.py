import asyncio

from app.document_finder import DocumentSection
from app.services.pipeline import generate_doc_proposal


class MockModel:
    def __init__(self) -> None:
        self.prompt = ""

    def generate(self, prompt: str) -> str:
        self.prompt = prompt
        return (
            '{"decision":"update_required","rationale":"The guide needs a user-facing update.",'
            '"changes":[{"path":"docs/guide.md","section":"Feature","action":"replace",'
            '"content":"Select Save to keep your changes.",'
            '"evidence":["head:src/api.py"]}],"uncertainties":[],"tests_to_run":[]}'
        )


async def test_firewall() -> None:
    model = MockModel()
    raw_diff = "diff --git a/api.py b/api.py\n+ def new_endpoint(): pass"
    section = DocumentSection(
        path="docs/guide.md",
        heading="Feature",
        start_line=1,
        end_line=3,
        content="# Feature\n\nSave your changes.",
        matched_terms=("feature",),
    )

    proposal, _ = generate_doc_proposal(
        "Users can save changes from this feature.",
        [section],
        raw_diff,
        model,
        "{{source_context}}\n{{document_context}}",
        "Use concise, user-facing language.",
        evidence_refs={"head:src/api.py"},
    )

    assert raw_diff not in model.prompt
    assert proposal.changes[0].content == "Select Save to keep your changes."
    print("Synthesis firewall passed: writer received impact context and returned a documentation edit.")


if __name__ == "__main__":
    asyncio.run(test_firewall())
