from fastapi import APIRouter, Depends

from app.api.dependencies import require_role
from app.core.scheduler import get_scheduler_status


router = APIRouter()


@router.get('/scheduler-status')
async def scheduler_status(_: dict = Depends(require_role('admin'))):
    return get_scheduler_status()
