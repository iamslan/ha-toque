"""Constants for the Toque integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "toque"
MANUFACTURER: Final = "Lidl / Toque"
DEFAULT_NAME: Final = "Toque"
DEFAULT_PORT: Final = 8730

# Name this Home Assistant gives itself when pairing (shown on the robot).
PAIRING_NAME: Final = "Home Assistant"

CONF_MODEL: Final = "model"
CONF_SW_VERSION: Final = "sw_version"

# Reconnection of the event stream: 2 s, 4 s, ... capped at 30 s. While the
# stream is down every attempt is followed by a /api/status poll, so the cap
# is also the fallback polling interval.
RECONNECT_MIN: Final = 2
RECONNECT_MAX: Final = 30

# Home Assistant states are limited to 255 characters.
MAX_STATE_LENGTH: Final = 255

# Event fired on the Home Assistant bus for every cooking event.
EVENT_TOQUE: Final = "toque_event"

# The non-"status" events of /api/events, exactly as named in the contract.
EVENT_TYPES: Final = (
    "job_started",
    "job_paused",
    "job_resumed",
    "job_finished",
    "job_aborted",
    "waiting_for_user",
    "recipe_finished",
    "lid_opened",
    "lid_closed",
    "fault",
)

ROBOT_STATES: Final = ("offline", "idle", "running", "paused", "waiting_for_user", "fault")
JOB_KINDS: Final = ("manual", "knead", "steam", "toast", "turbo")
LINK_STATES: Final = ("offline", "connecting", "ready", "fault")

SERVICE_SEARCH_RECIPES: Final = "search_recipes"
SERVICE_GET_RECIPE: Final = "get_recipe"
SERVICE_ADD_TO_SHOPPING_LIST: Final = "add_to_shopping_list"

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_QUERY: Final = "query"
ATTR_CATEGORY: Final = "category"
ATTR_FAVORITES: Final = "favorites"
ATTR_LIMIT: Final = "limit"
ATTR_OFFSET: Final = "offset"
ATTR_RECIPE_ID: Final = "recipe_id"
ATTR_RECIPE_IDS: Final = "recipe_ids"
ATTR_TODO_ENTITY: Final = "todo_entity"
