from typing import Annotated

from fastapi import APIRouter, Query

from jyj.api.auth import CurrentUser
from jyj.schemas.images import PhotoSearchOut
from jyj.services import image_search

router = APIRouter(prefix="/images", tags=["images"])


@router.get("/search", response_model=PhotoSearchOut)
def search_images(
    user: CurrentUser,
    q: Annotated[str, Query(min_length=1, max_length=image_search.QUERY_MAX)],
    page: Annotated[int, Query(ge=1, le=image_search.PAGE_MAX)] = 1,
) -> PhotoSearchOut:
    # Sync on purpose: the upstream call runs in the threadpool, off the event loop.
    return PhotoSearchOut.build(image_search.search(user.id, q, page))
