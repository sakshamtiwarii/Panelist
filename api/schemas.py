"""Request bodies for the API. Response shapes stay plain dicts."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from api.deps import DEFAULT_DATASET

# company_id -> level. A Literal rather than a bare str, so an unknown level
# is a 422 naming the valid ones instead of an override the solver ignores.
PriorityOverrides = Dict[str, Literal["protect", "normal", "deprioritise"]]


class LoginRequest(BaseModel):
    username: str
    password: str


class GenerateRequest(BaseModel):
    seed: int = 42
    companies: int = 35
    students: int = 800
    rooms: int = 20
    days: int = 4
    load_factor: Optional[float] = None
    name: str = "primary"


class ScheduleRequest(BaseModel):
    dataset: str = DEFAULT_DATASET
    time_limit_seconds: float = 30.0
    priority_overrides: PriorityOverrides = Field(default_factory=dict)


class Disruption(BaseModel):
    type: str = Field(..., description="company_late | panel_drop | "
                                       "student_withdraw | room_unavailable | "
                                       "company_add | company_remove | "
                                       "shortlist_add | shortlist_remove")
    company_id: Optional[str] = None
    student_id: Optional[str] = None
    room_id: Optional[str] = None
    day: Optional[int] = None
    hours: Optional[float] = None
    count: Optional[int] = None
    scope: Optional[str] = None
    from_slot: Optional[int] = None
    to_slot: Optional[int] = None
    reason: Optional[str] = None
    # roster amendment fields
    name: Optional[str] = None
    tier: Optional[int] = None
    cgpa_cutoff: Optional[float] = None
    panel_count: Optional[int] = None
    interview_minutes: Optional[int] = None
    shortlist: Optional[List[str]] = None
    shortlist_size: Optional[int] = None


class NewCompany(BaseModel):
    name: str
    cgpa_cutoff: float = 7.0
    panel_count: int = 2
    interview_minutes: int = 30
    tier: int = 3
    company_id: Optional[str] = None
    shortlist: Optional[List[str]] = None
    shortlist_size: int = 20
    churn_cap_pct: float = 10.0
    time_limit_seconds: float = 60.0


class ShortlistEntry(BaseModel):
    company_id: str
    student_id: str
    churn_cap_pct: float = 10.0
    time_limit_seconds: float = 45.0


class ReplanRequest(BaseModel):
    disruptions: List[Disruption]
    churn_cap_pct: float = 10.0
    time_limit_seconds: float = 60.0
    now_slot: Optional[int] = None
    priority_overrides: PriorityOverrides = Field(default_factory=dict)


class ApplyRequest(BaseModel):
    proposal_id: str
    # Commit the lower-churn alternative the replanner solved for when the
    # first fix blew the churn cap.
    use_alternative: bool = False
