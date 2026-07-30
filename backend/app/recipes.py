"""Global reusable formula recipes stored as one human-readable JSON file."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence

from . import config
from .formula import FormulaError, validate_recipe_formulas
from .locks import ReaderWriterLock
from .store import write_json_atomic


RECIPE_FILE_NAME = "formula_recipes.json"
RECIPE_VERSION = 1
MAX_RECIPE_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 2000
_recipe_lock = ReaderWriterLock()


class RecipeError(ValueError):
    """The recipe document or requested recipe is invalid."""


def _path():
    # Resolve dynamically so isolated deployments/tests can override DATA_DIR.
    return config.DATA_DIR / RECIPE_FILE_NAME


def validate_recipe_name(name: str) -> str:
    if not isinstance(name, str):
        raise RecipeError("recipe name must be a string")
    normalized = name.strip()
    if not normalized:
        raise RecipeError("recipe name cannot be empty")
    if len(normalized) > MAX_RECIPE_NAME_LENGTH:
        raise RecipeError(
            f"recipe name cannot exceed {MAX_RECIPE_NAME_LENGTH} characters")
    if any(ord(character) < 32 for character in normalized):
        raise RecipeError("recipe name cannot contain control characters")
    if "/" in normalized or "\\" in normalized:
        raise RecipeError("recipe name cannot contain '/' or '\\'")
    return normalized


def _empty_document() -> dict:
    return {"version": RECIPE_VERSION, "recipes": []}


def _load_unlocked() -> dict:
    path = _path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty_document()
    except json.JSONDecodeError as exc:
        raise RecipeError(
            f"formula recipe file is invalid JSON: {exc.msg}") from None
    if not isinstance(payload, dict) or not isinstance(
            payload.get("recipes"), list):
        raise RecipeError(
            "formula recipe file must contain an object with a recipes list")
    version = payload.get("version", RECIPE_VERSION)
    if version != RECIPE_VERSION:
        raise RecipeError(
            f"unsupported formula recipe version {version!r}")
    recipes: list[dict] = []
    for index, recipe in enumerate(payload["recipes"]):
        if not isinstance(recipe, dict) or not isinstance(
                recipe.get("name"), str):
            raise RecipeError(
                f"formula recipe entry {index + 1} must be an object with a "
                "string name")
        try:
            validate_recipe_name(recipe["name"])
            validate_recipe_formulas(recipe.get("formulas"))
        except (RecipeError, FormulaError, TypeError) as exc:
            raise RecipeError(
                f"formula recipe entry {index + 1} is invalid: {exc}"
            ) from None
        if not isinstance(recipe.get("description", ""), str):
            raise RecipeError(
                f"formula recipe entry {index + 1} has a non-string "
                "description")
        recipes.append(dict(recipe))
    names = [recipe["name"] for recipe in recipes]
    if len(names) != len(set(names)):
        raise RecipeError("formula recipe file contains duplicate names")
    recipes.sort(key=lambda recipe: recipe["name"].casefold())
    return {"version": RECIPE_VERSION, "recipes": recipes}


def list_recipes() -> dict:
    with _recipe_lock.read():
        return _load_unlocked()


def get_recipe(name: str) -> dict | None:
    normalized = validate_recipe_name(name)
    with _recipe_lock.read():
        for recipe in _load_unlocked()["recipes"]:
            if recipe["name"] == normalized:
                return recipe
    return None


def put_recipe(
    name: str,
    description: str,
    formulas: Sequence[Any],
) -> dict:
    normalized = validate_recipe_name(name)
    if not isinstance(description, str):
        raise RecipeError("recipe description must be a string")
    description = description.strip()
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise RecipeError(
            f"recipe description cannot exceed "
            f"{MAX_DESCRIPTION_LENGTH} characters")
    try:
        normalized_formulas = validate_recipe_formulas(formulas)
    except FormulaError as exc:
        raise RecipeError(str(exc)) from None

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _recipe_lock.write():
        document = _load_unlocked()
        prior = next(
            (recipe for recipe in document["recipes"]
             if recipe["name"] == normalized),
            None,
        )
        recipe = {
            "name": normalized,
            "description": description,
            "formulas": normalized_formulas,
            "created_at": (
                prior.get("created_at", now) if prior is not None else now
            ),
            "updated_at": now,
        }
        document["recipes"] = [
            current for current in document["recipes"]
            if current["name"] != normalized
        ]
        document["recipes"].append(recipe)
        document["recipes"].sort(
            key=lambda current: current["name"].casefold())
        write_json_atomic(_path(), document)
        return recipe


def delete_recipe(name: str) -> bool:
    normalized = validate_recipe_name(name)
    with _recipe_lock.write():
        document = _load_unlocked()
        kept = [
            recipe for recipe in document["recipes"]
            if recipe["name"] != normalized
        ]
        if len(kept) == len(document["recipes"]):
            return False
        document["recipes"] = kept
        write_json_atomic(_path(), document)
        return True
