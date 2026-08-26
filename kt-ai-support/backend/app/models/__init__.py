"""ORM models.

The vocabularies live here as plain string constants rather than Python
enums, because they must match the CHECK constraints in the migration
exactly and a mismatch should fail loudly at insert time, in one place,
not silently diverge across two type systems.
"""

from app.models.enums import (
    ActionStatus,
    ActionType,
    ChangeType,
    ChunkType,
    Dimension,
    Direction,
    EvidenceType,
    HypothesisStatus,
    RootCauseConfidence,
    Side,
    TestResult,
    TicketStatus,
)
from app.models.knowledge import RagChunk, RagQuery
from app.models.kt import KTChange, KTDistinction, KTSpecification
from app.models.diagnosis import (
    DiagnosticTest,
    KTHypothesis,
    RootCause,
    TicketAction,
    TicketEvidence,
)
from app.models.ticket import SupportTicket, TicketTimeline

__all__ = [
    "SupportTicket", "TicketTimeline",
    "KTSpecification", "KTDistinction", "KTChange",
    "KTHypothesis", "TicketEvidence", "DiagnosticTest", "TicketAction", "RootCause",
    "RagChunk", "RagQuery",
    "Dimension", "Side", "ChangeType", "HypothesisStatus", "Direction",
    "EvidenceType", "TestResult", "ActionType", "ActionStatus",
    "RootCauseConfidence", "ChunkType", "TicketStatus",
]
