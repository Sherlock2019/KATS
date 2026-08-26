"""Request/response models.

Naming convention throughout: `XCreate` is what a client may send, `XUpdate`
is the same with every field optional, and `XRead` is what comes back. The
split matters most on hypotheses and root causes, where a client must not be
able to set fields the system is supposed to earn (`confirmed_at`, and the
ticket's denormalised `root_cause_status`).
"""

from app.schemas.diagnosis import (
    ActionCreate,
    ActionRead,
    DiagnosticTestCreate,
    DiagnosticTestRead,
    DiagnosticTestUpdate,
    EvidenceCreate,
    EvidenceRead,
    HypothesisCreate,
    HypothesisRead,
    HypothesisUpdate,
    RootCauseCreate,
    RootCauseRead,
)
from app.schemas.kt import (
    ChangeCreate,
    ChangeRead,
    DistinctionCreate,
    DistinctionRead,
    KTGrid,
    KTGridCell,
    SpecificationCreate,
    SpecificationRead,
)
from app.schemas.rag import (
    ChunkRead,
    DiagnoseRequest,
    DiagnoseResponse,
    InspectorRow,
    LikelyCause,
    NextActionResponse,
    NextQuestionResponse,
    RecommendedTest,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SimilarCase,
)
from app.schemas.ticket import (
    Completeness,
    TicketCreate,
    TicketDetail,
    TicketRead,
    TicketSummary,
    TicketUpdate,
    TimelineCreate,
    TimelineRead,
)

__all__ = [
    "TicketCreate", "TicketUpdate", "TicketRead", "TicketDetail", "TicketSummary",
    "TimelineCreate", "TimelineRead", "Completeness",
    "SpecificationCreate", "SpecificationRead", "KTGrid", "KTGridCell",
    "DistinctionCreate", "DistinctionRead", "ChangeCreate", "ChangeRead",
    "HypothesisCreate", "HypothesisUpdate", "HypothesisRead",
    "EvidenceCreate", "EvidenceRead",
    "DiagnosticTestCreate", "DiagnosticTestUpdate", "DiagnosticTestRead",
    "ActionCreate", "ActionRead", "RootCauseCreate", "RootCauseRead",
    "SearchRequest", "SearchResponse", "SearchResult", "ChunkRead",
    "DiagnoseRequest", "DiagnoseResponse", "LikelyCause", "RecommendedTest",
    "SimilarCase", "NextActionResponse", "NextQuestionResponse", "InspectorRow",
]
