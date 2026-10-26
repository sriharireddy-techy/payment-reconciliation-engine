"""Dashboard summary."""

from fastapi import APIRouter

from services import dashboard_service

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard", summary="Live KPIs, chart data and recent activity")
def dashboard() -> dict:
    return dashboard_service.get_dashboard()
