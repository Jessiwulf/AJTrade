from fastapi import APIRouter

router = APIRouter()

@router.get('/v1/ping')
async def ping():
    return {"pong": True}
