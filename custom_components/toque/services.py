"""Services of the Toque integration: recipe search and shopping list."""

from __future__ import annotations

from typing import Any, NoReturn

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .api import ToqueAuthError, ToqueConnectionError, ToqueError, ToqueNotFoundError
from .const import (
    ATTR_CATEGORY,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_FAVORITES,
    ATTR_LIMIT,
    ATTR_OFFSET,
    ATTR_QUERY,
    ATTR_RECIPE_ID,
    ATTR_RECIPE_IDS,
    ATTR_TODO_ENTITY,
    DOMAIN,
    SERVICE_ADD_TO_SHOPPING_LIST,
    SERVICE_GET_RECIPE,
    SERVICE_SEARCH_RECIPES,
)
from .coordinator import ToqueConfigEntry, ToqueCoordinator

TODO_DOMAIN = "todo"
TODO_SERVICE_ADD_ITEM = "add_item"

# Which robot: only needed when several are configured.
_ROBOT_SELECTOR = {
    vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    vol.Optional(ATTR_DEVICE_ID): cv.string,
}

SEARCH_RECIPES_SCHEMA = vol.Schema(
    {
        **_ROBOT_SELECTOR,
        vol.Optional(ATTR_QUERY, default=""): cv.string,
        vol.Optional(ATTR_CATEGORY): cv.string,
        vol.Optional(ATTR_FAVORITES, default=False): cv.boolean,
        vol.Optional(ATTR_LIMIT, default=50): vol.All(vol.Coerce(int), vol.Range(min=1, max=200)),
        vol.Optional(ATTR_OFFSET, default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)

GET_RECIPE_SCHEMA = vol.Schema(
    {
        **_ROBOT_SELECTOR,
        vol.Required(ATTR_RECIPE_ID): cv.string,
    }
)

ADD_TO_SHOPPING_LIST_SCHEMA = vol.All(
    vol.Schema(
        {
            **_ROBOT_SELECTOR,
            # Each accepts one id or a list of ids.
            vol.Optional(ATTR_RECIPE_ID): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional(ATTR_RECIPE_IDS): vol.All(cv.ensure_list, [cv.string]),
            vol.Required(ATTR_TODO_ENTITY): cv.entity_domain(TODO_DOMAIN),
        }
    ),
    cv.has_at_least_one_key(ATTR_RECIPE_ID, ATTR_RECIPE_IDS),
)


def _error(key: str, **placeholders: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders
    )


@callback
def _async_get_coordinator(hass: HomeAssistant, call: ServiceCall) -> ToqueCoordinator:
    """Find the robot a service call is about."""
    loaded: list[ToqueConfigEntry] = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    entry_id: str | None = call.data.get(ATTR_CONFIG_ENTRY_ID)

    if entry_id is None and (device_id := call.data.get(ATTR_DEVICE_ID)) is not None:
        device = dr.async_get(hass).async_get(device_id)
        if device is not None:
            entry_id = next(
                (entry.entry_id for entry in loaded if entry.entry_id in device.config_entries),
                None,
            )
        if entry_id is None:
            raise _error("robot_not_found")

    if entry_id is not None:
        for entry in loaded:
            if entry.entry_id == entry_id:
                return entry.runtime_data
        raise _error("robot_not_found")

    if not loaded:
        raise _error("no_robot")
    if len(loaded) > 1:
        raise _error("robot_required")
    return loaded[0].runtime_data


def _raise_for(coordinator: ToqueCoordinator, err: ToqueError) -> NoReturn:
    """Turn a client error into a Home Assistant service error."""
    if isinstance(err, ToqueAuthError):
        coordinator.config_entry.async_start_reauth(coordinator.hass)
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    if isinstance(err, ToqueConnectionError):
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="cannot_connect"
        ) from err
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="api_error",
        translation_placeholders={"error": str(err)},
    ) from err


async def _async_fetch_recipe(coordinator: ToqueCoordinator, recipe_id: str) -> dict[str, Any]:
    try:
        return await coordinator.client.recipe(recipe_id)
    except ToqueNotFoundError as err:
        raise _error("recipe_not_found", recipe_id=recipe_id) from err
    except ToqueError as err:
        _raise_for(coordinator, err)


def ingredient_lines(recipe: dict[str, Any]) -> list[str]:
    """The shopping-list lines of a recipe: each ingredient's ``text``."""
    lines: list[str] = []
    for group in recipe.get("ingredients") or []:
        if not isinstance(group, dict):
            continue
        for item in group.get("items") or []:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                # Not expected from the contract; rebuild a line if it happens.
                text = " ".join(
                    str(item[key]) for key in ("quantity", "unit", "name") if item.get(key)
                )
            text = text.strip()
            if text:
                lines.append(text)
    return lines


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services (once, from async_setup)."""

    async def search_recipes(call: ServiceCall) -> ServiceResponse:
        coordinator = _async_get_coordinator(hass, call)
        try:
            result = await coordinator.client.recipes(
                call.data[ATTR_QUERY],
                category=call.data.get(ATTR_CATEGORY),
                favorites=call.data[ATTR_FAVORITES],
                limit=call.data[ATTR_LIMIT],
                offset=call.data[ATTR_OFFSET],
            )
        except ToqueError as err:
            _raise_for(coordinator, err)
        items = result.get("items")
        if not isinstance(items, list):
            items = []
        return {"total": result.get("total", len(items)), "items": items}

    async def get_recipe(call: ServiceCall) -> ServiceResponse:
        coordinator = _async_get_coordinator(hass, call)
        return {"recipe": await _async_fetch_recipe(coordinator, call.data[ATTR_RECIPE_ID])}

    async def add_to_shopping_list(call: ServiceCall) -> ServiceResponse:
        coordinator = _async_get_coordinator(hass, call)
        todo_entity: str = call.data[ATTR_TODO_ENTITY]
        recipe_ids: list[str] = list(
            dict.fromkeys([*call.data.get(ATTR_RECIPE_ID, []), *call.data.get(ATTR_RECIPE_IDS, [])])
        )
        if not hass.services.has_service(TODO_DOMAIN, TODO_SERVICE_ADD_ITEM):
            raise _error("todo_unavailable")

        # Read every recipe first, so that a wrong id adds nothing at all.
        recipes = [await _async_fetch_recipe(coordinator, recipe_id) for recipe_id in recipe_ids]

        added = 0
        summary: list[dict[str, Any]] = []
        for recipe_id, recipe in zip(recipe_ids, recipes, strict=True):
            lines = ingredient_lines(recipe)
            for line in lines:
                await hass.services.async_call(
                    TODO_DOMAIN,
                    TODO_SERVICE_ADD_ITEM,
                    {"entity_id": todo_entity, "item": line},
                    blocking=True,
                    context=call.context,
                )
                added += 1
            summary.append({"id": recipe_id, "title": recipe.get("title"), "added": len(lines)})
        return {"added": added, "recipes": summary}

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEARCH_RECIPES,
        search_recipes,
        schema=SEARCH_RECIPES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_RECIPE,
        get_recipe,
        schema=GET_RECIPE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ADD_TO_SHOPPING_LIST,
        add_to_shopping_list,
        schema=ADD_TO_SHOPPING_LIST_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
