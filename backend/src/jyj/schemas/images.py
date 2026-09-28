from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from jyj.services.image_search import PhotoResult, SearchPage


class PhotoSearchResultOut(BaseModel):
    id: int
    alt: str
    width: int
    height: int
    photographer: str
    photographer_url: str | None
    page_url: str | None
    thumb_url: str
    preview_url: str

    @classmethod
    def build(cls, photo: PhotoResult) -> Self:
        return cls(**photo.as_dict())


class PhotoSearchOut(BaseModel):
    provider: Literal["pexels"] = "pexels"
    query: str
    page: int
    has_more: bool
    results: list[PhotoSearchResultOut]

    @classmethod
    def build(cls, page: SearchPage) -> Self:
        return cls(
            query=page.query,
            page=page.page,
            has_more=page.has_more,
            results=[PhotoSearchResultOut.build(p) for p in page.results],
        )


class PhotoFromSearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["pexels"]
    photo_id: int = Field(ge=1, le=2**53)
