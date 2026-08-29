"""
Panelist — roster amendments.

These return a PROPOSAL, not a committed change. Adding a company means its
interviews need slots that are already taken; removing one frees capacity the
plan cannot use. Writing either straight to the database would leave the live
schedule wrong while every metric still read zero clashes, because nothing
re-checked it. Committing goes through POST /replan/apply like any other fix.
"""

from fastapi import APIRouter, Depends

from api.auth import current_user
from api.routes.replan import propose
from api.schemas import NewCompany, ShortlistEntry

router = APIRouter(prefix="/roster", tags=["roster"])


@router.post("/companies")
def add_company(req: NewCompany, _=Depends(current_user)):
    """Propose registering a company that arrived after the dataset was built."""
    event = {"type": "company_add", **req.model_dump(
        exclude_none=True, exclude={"churn_cap_pct", "time_limit_seconds"})}
    return propose([event], req.churn_cap_pct, req.time_limit_seconds)


@router.delete("/companies/{company_id}")
def remove_company(company_id: str, churn_cap_pct: float = 10.0,
                   time_limit_seconds: float = 60.0,
                   _=Depends(current_user)):
    """Propose withdrawing a company and cancelling its interviews."""
    return propose([{"type": "company_remove", "company_id": company_id}],
                   churn_cap_pct, time_limit_seconds)


@router.post("/shortlist")
def add_shortlist_entry(req: ShortlistEntry, _=Depends(current_user)):
    """Propose adding one student to one company's shortlist."""
    return propose(
        [{"type": "shortlist_add", "company_id": req.company_id,
          "student_id": req.student_id}],
        req.churn_cap_pct, req.time_limit_seconds)


@router.delete("/shortlist")
def remove_shortlist_entry(company_id: str, student_id: str,
                           churn_cap_pct: float = 10.0,
                           time_limit_seconds: float = 45.0,
                           _=Depends(current_user)):
    """Propose taking one student off one company's shortlist."""
    return propose(
        [{"type": "shortlist_remove", "company_id": company_id,
          "student_id": student_id}],
        churn_cap_pct, time_limit_seconds)
