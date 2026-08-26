"""Controlled vocabularies.

These mirror the CHECK constraints in 001_initial_schema.sql exactly. They
are `str` enums so they serialise as their value and compare equal to plain
strings, which keeps request payloads readable and avoids a translation
layer between the API and the database.

If you add a member here, add it to the CHECK constraint too — the database
is the authority, and `tests/test_enums_match_schema.py` fails when they
drift apart.
"""

from __future__ import annotations

from enum import StrEnum


class TicketStatus(StrEnum):
    NEW = "NEW"
    TRIAGE = "TRIAGE"
    INVESTIGATING = "INVESTIGATING"
    TESTING = "TESTING"
    IDENTIFIED = "IDENTIFIED"
    MITIGATED = "MITIGATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class Dimension(StrEnum):
    """The four KT specification dimensions."""

    WHAT = "WHAT"
    WHERE = "WHERE"
    WHEN = "WHEN"
    EXTENT = "EXTENT"


class Side(StrEnum):
    IS = "IS"
    IS_NOT = "IS_NOT"


class ChangeType(StrEnum):
    DEPLOYMENT = "deployment"
    CONFIGURATION = "configuration"
    NETWORK = "network"
    CREDENTIAL = "credential"
    CERTIFICATE = "certificate"
    OS_PATCH = "os_patch"
    SOFTWARE_UPGRADE = "software_upgrade"
    HARDWARE = "hardware"
    DATABASE = "database"
    POLICY = "policy"
    SECURITY = "security"
    DEPENDENCY = "dependency"
    TRAFFIC = "traffic"
    USER_BEHAVIOR = "user_behavior"
    UNKNOWN = "unknown"


class HypothesisStatus(StrEnum):
    PROPOSED = "PROPOSED"
    TESTING = "TESTING"
    SUPPORTED = "SUPPORTED"
    REJECTED = "REJECTED"
    CONFIRMED = "CONFIRMED"


class Direction(StrEnum):
    """Which way a piece of evidence cuts.

    The reason ticket_evidence exists as its own table: an assistant that
    cannot see that an observation argues AGAINST a hypothesis will keep
    recommending it.
    """

    FOR = "FOR"
    AGAINST = "AGAINST"
    NEUTRAL = "NEUTRAL"


class EvidenceType(StrEnum):
    LOG = "log"
    METRIC = "metric"
    TRACE = "trace"
    SCREENSHOT = "screenshot"
    CONFIGURATION = "configuration"
    COMMAND_OUTPUT = "command_output"
    TEST_RESULT = "test_result"
    USER_OBSERVATION = "user_observation"
    MONITORING_ALERT = "monitoring_alert"
    DOCUMENT = "document"
    OTHER = "other"


class TestResult(StrEnum):
    NOT_RUN = "NOT_RUN"
    INCONCLUSIVE = "INCONCLUSIVE"
    SUPPORTS = "SUPPORTS"
    REJECTS = "REJECTS"
    CONFIRMS = "CONFIRMS"


class ActionType(StrEnum):
    STABILIZATION = "STABILIZATION"
    WORKAROUND = "WORKAROUND"
    DIAGNOSTIC = "DIAGNOSTIC"
    CORRECTIVE = "CORRECTIVE"
    PREVENTIVE = "PREVENTIVE"
    ROLLBACK = "ROLLBACK"


class ActionStatus(StrEnum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class RootCauseConfidence(StrEnum):
    """The ladder a cause has to climb.

    Only CONFIRMED earns the top retrieval boost. Typing a sentence into a
    form is SUSPECTED, no matter how sure the person typing it feels.
    """

    SUSPECTED = "SUSPECTED"
    PROBABLE = "PROBABLE"
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    CONFIRMED = "CONFIRMED"


class RootCauseStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    SUSPECTED = "SUSPECTED"
    PROBABLE = "PROBABLE"
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    CONFIRMED = "CONFIRMED"


class ChunkType(StrEnum):
    PROBLEM = "PROBLEM"
    SYMPTOM = "SYMPTOM"
    CONTEXT = "CONTEXT"
    KT_SPECIFICATION = "KT_SPECIFICATION"
    DISTINCTIONS = "DISTINCTIONS"
    CHANGES = "CHANGES"
    HYPOTHESIS = "HYPOTHESIS"
    REJECTED_HYPOTHESIS = "REJECTED_HYPOTHESIS"
    EVIDENCE = "EVIDENCE"
    DIAGNOSTIC_TEST = "DIAGNOSTIC_TEST"
    ROOT_CAUSE = "ROOT_CAUSE"
    WORKAROUND = "WORKAROUND"
    RESOLUTION = "RESOLUTION"
    PREVENTION = "PREVENTION"
    TIMELINE = "TIMELINE"
    FULL_CASE_SUMMARY = "FULL_CASE_SUMMARY"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
