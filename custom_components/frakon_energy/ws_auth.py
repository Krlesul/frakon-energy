from __future__ import annotations

from homeassistant.components import websocket_api
from homeassistant.exceptions import Unauthorized


def ensure_admin(connection: websocket_api.ActiveConnection) -> None:
    """Require an authenticated Home Assistant administrator.

    Home Assistant's ActiveConnection no longer exposes the historical
    ``require_admin()`` instance method. Keep the authorization check explicit and
    version-stable while matching Home Assistant's current ``require_admin``
    decorator semantics.
    """
    user = getattr(connection, "user", None)
    if user is None or not user.is_admin:
        raise Unauthorized
