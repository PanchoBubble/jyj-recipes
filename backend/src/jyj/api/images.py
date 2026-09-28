from typing import Annotated

from fastapi import APIRouter, Query, Response

from jyj.api.auth import CurrentUser
from jyj.schemas.images import PhotoSearchOut
from jyj.services import image_search
from jyj.services.image_search import ProviderName

router = APIRouter(prefix="/images", tags=["images"])


@router.get("/search", response_model=PhotoSearchOut)
def search_images(
    user: CurrentUser,
    q: Annotated[str, Query(min_length=1, max_length=image_search.QUERY_MAX)],
    page: Annotated[int, Query(ge=1, le=image_search.PAGE_MAX)] = 1,
) -> PhotoSearchOut:
    # Sync on purpose: the upstream call runs in the threadpool, off the event loop.
    return PhotoSearchOut.build(image_search.search(user.id, q, page))


@router.get(
    "/thumb",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}}}},
)
def thumbnail(
    _: CurrentUser,
    provider: ProviderName,
    id: Annotated[str, Query(min_length=1, max_length=64)],
) -> Response:
    data, content_type = image_search.thumbnail(provider, id)
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
