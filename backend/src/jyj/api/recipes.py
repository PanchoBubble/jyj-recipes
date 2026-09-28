from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from jyj.api.auth import CurrentUser
from jyj.db import get_db
from jyj.models import StockSource
from jyj.schemas.images import PhotoFromSearchIn
from jyj.schemas.recipes import (
    RecipeCreate,
    RecipeOut,
    RecipePageOut,
    RecipeSummary,
    RecipeUpdate,
    ScaledIngredientOut,
    ScaledRecipeOut,
    Servings,
)
from jyj.services import image_search, photos
from jyj.services import recipes as service

router = APIRouter(prefix="/recipes", tags=["recipes"])

Db = Annotated[Session, Depends(get_db)]


@router.get("", response_model=RecipePageOut)
def list_recipes(
    _: CurrentUser,
    db: Db,
    q: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    include_archived: bool = False,
) -> RecipePageOut:
    result = service.list_recipes(
        db,
        q,
        include_archived=include_archived,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return RecipePageOut(
        items=[RecipeSummary.build(r) for r in result.items],
        total=result.total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=RecipeOut, status_code=status.HTTP_201_CREATED)
def create_recipe(body: RecipeCreate, user: CurrentUser, db: Db) -> RecipeOut:
    recipe = service.create_recipe(db, user, StockSource.UI, **body.model_dump())
    return RecipeOut.build(recipe)


@router.get("/{recipe_id}", response_model=RecipeOut)
def get_recipe(recipe_id: int, _: CurrentUser, db: Db) -> RecipeOut:
    return RecipeOut.build(service.get_recipe(db, recipe_id))


@router.patch("/{recipe_id}", response_model=RecipeOut)
def update_recipe(recipe_id: int, body: RecipeUpdate, user: CurrentUser, db: Db) -> RecipeOut:
    recipe = service.update_recipe(
        db, user, StockSource.UI, recipe_id, body.model_dump(exclude_unset=True)
    )
    return RecipeOut.build(recipe)


@router.delete(
    "/{recipe_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={200: {"model": RecipeOut, "description": "Referenced, so archived instead"}},
)
def delete_recipe(recipe_id: int, user: CurrentUser, db: Db) -> Response:
    outcome = service.delete_recipe(db, user, StockSource.UI, recipe_id)
    if outcome is service.DeleteOutcome.ARCHIVED:
        body = RecipeOut.build(service.get_recipe(db, recipe_id))
        return Response(body.model_dump_json(), media_type="application/json")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{recipe_id}/scaled", response_model=ScaledRecipeOut)
def scaled_recipe(
    recipe_id: int, servings: Annotated[Servings, Query()], _: CurrentUser, db: Db
) -> ScaledRecipeOut:
    recipe = service.get_recipe(db, recipe_id)
    return ScaledRecipeOut(
        recipe_id=recipe.id,
        name=recipe.name,
        servings=servings,
        ingredients=[ScaledIngredientOut.build(s) for s in service.scale_recipe(recipe, servings)],
    )


@router.put("/{recipe_id}/photo", response_model=RecipeOut)
def put_photo(
    recipe_id: int, file: Annotated[UploadFile, File()], _: CurrentUser, db: Db
) -> RecipeOut:
    # Sync on purpose: FastAPI runs it in the threadpool, keeping decode off the event loop.
    service.get_recipe(db, recipe_id)
    data = photos.read_capped(file.file, photos.max_bytes())
    return RecipeOut.build(photos.set_recipe_photo(db, recipe_id, data))


@router.post("/{recipe_id}/photo/from-search", response_model=RecipeOut)
def put_photo_from_search(
    recipe_id: int, body: PhotoFromSearchIn, user: CurrentUser, db: Db
) -> RecipeOut:
    recipe = image_search.set_recipe_photo_from_search(
        db, user.id, recipe_id, body.provider, body.photo_id
    )
    return RecipeOut.build(recipe)


@router.delete("/{recipe_id}/photo", response_model=RecipeOut)
def delete_photo(recipe_id: int, _: CurrentUser, db: Db) -> RecipeOut:
    return RecipeOut.build(photos.clear_recipe_photo(db, recipe_id))
