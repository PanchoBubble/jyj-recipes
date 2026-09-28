"""Recipe photo tools: thin wrappers over ``jyj.services.image_search``."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jyj.chat.tools.common import Id
from jyj.chat.tools.registry import Tool, ToolContext
from jyj.services import image_search
from jyj.services import recipes as recipes_service

RESULTS_MAX = 6
ALT_MAX = 120


class FindRecipePhotosArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=image_search.QUERY_MAX,
        description="What the photo should show, e.g. the dish name in English.",
    )
    recipe_id: Id | None = Field(default=None, description="Recipe the photo is for, if known.")


class SetRecipePhotoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_id: Id
    provider: Literal["pexels"]
    photo_id: Id = Field(description="A photo id from find_recipe_photos.")


def find_recipe_photos(ctx: ToolContext, args: FindRecipePhotosArgs) -> dict:
    recipe = None
    if args.recipe_id is not None:
        recipe = recipes_service.get_recipe(ctx.db, args.recipe_id)
    page = image_search.search(ctx.user.id, args.query, per_page=RESULTS_MAX)
    return {
        "provider": image_search.PROVIDER,
        "query": page.query,
        "recipe_id": recipe.id if recipe else None,
        "recipe_name": recipe.name if recipe else None,
        "has_photo": bool(recipe and recipe.photo_path),
        "photos": [
            {
                "id": p.id,
                "alt": p.alt[:ALT_MAX],
                "photographer": p.photographer,
                "thumb_url": p.thumb_url,
            }
            for p in page.results[:RESULTS_MAX]
        ],
    }


def replaces_photo(ctx: ToolContext, args: SetRecipePhotoArgs) -> bool:
    return recipes_service.get_recipe(ctx.db, args.recipe_id).photo_path is not None


def preview_set_recipe_photo(ctx: ToolContext, args: SetRecipePhotoArgs) -> dict:
    recipe = recipes_service.get_recipe(ctx.db, args.recipe_id)
    return {
        "recipe_id": recipe.id,
        "name": recipe.name,
        "photo_id": args.photo_id,
        "replaces_photo": recipe.photo_path is not None,
    }


def set_recipe_photo(ctx: ToolContext, args: SetRecipePhotoArgs) -> dict:
    recipe = image_search.set_recipe_photo_from_search(
        ctx.db, ctx.user.id, args.recipe_id, args.provider, args.photo_id
    )
    credit = recipe.photo_credit or {}
    return {
        "recipe_id": recipe.id,
        "name": recipe.name,
        "photo_id": args.photo_id,
        "photographer": credit.get("photographer"),
    }


TOOLS = (
    Tool(
        name="find_recipe_photos",
        description=(
            "Search stock photos (Pexels) for a recipe. Returns up to 6 photo ids with "
            "descriptions; the user sees them as thumbnails."
        ),
        args_model=FindRecipePhotosArgs,
        kind="read",
        handler=find_recipe_photos,
        card=True,
    ),
    Tool(
        name="set_recipe_photo",
        description=(
            "Use a photo from find_recipe_photos as a recipe's photo. Replacing an existing "
            "photo asks the user to confirm first."
        ),
        args_model=SetRecipePhotoArgs,
        kind="write",
        handler=set_recipe_photo,
        preview=preview_set_recipe_photo,
        confirm_if=replaces_photo,
    ),
)
