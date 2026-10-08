from enum import Enum


class UserRole(str, Enum):
	WRITER = "WRITER"
	REVIEWER = "REVIEWER"
	APPROVER = "APPROVER"


class ReviewStatus(str, Enum):
	AWAITING_REVIEW = "AWAITING_REVIEW"
	APPROVED = "APPROVED"
	REJECTED = "REJECTED"


class ProposalDecision(str, Enum):
	UPDATE_REQUIRED = "update_required"
	NO_UPDATE = "no_update"
	HUMAN_INVESTIGATION = "human_investigation"


class EditAction(str, Enum):
	REPLACE = "replace"
	APPEND = "append"