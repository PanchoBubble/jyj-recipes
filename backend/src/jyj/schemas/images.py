from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field

from jyj.services.image_search import PhotoResult, ProviderName, SearchPage


class PhotoSearchResultOut(BaseModel):
    provider: ProviderName
    id: int | str
    alt: str
    width: int
    height: int
    photographer: str
    photographer_url: str | None
    page_url: str | None
    thumb_url: str
    preview_url: str
    title: str | None
    license: str | None
    license_url: str | None

    @classmethod
    def build(cls, photo: PhotoResult) -> Self:
        return cls(**photo.as_dict())


class PhotoSearchOut(BaseModel):
    provider: ProviderName
    query: str
    page: int
    has_more: bool
    results: list[PhotoSearchResultOut]

    @classmethod
    def build(cls, page: SearchPage) -> Self:
        return cls(
            provider=page.provider,
            query=page.query,
            page=page.page,
            has_more=page.has_more,
            results=[PhotoSearchResultOut.build(p) for p in page.results],
        )


class PhotoFromSearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderName
    # Pexels ids are integers, Openverse ids UUIDs; the provider validates its own.
    photo_id: (
        Annotated[int, Field(strict=True, ge=1, le=2**53)]
        | Annotated[str, Field(min_length=1, max_length=64)]
    )
