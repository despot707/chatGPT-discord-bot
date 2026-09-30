"""Apply the supplied bot avatar and application icon once per asset revision."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


async def apply_branding(client, *, image_path=None, state_path=None):
    image = (
        Path(image_path)
        if image_path
        else Path(__file__).resolve().parent.parent / "assets/luna-avatar.jpg"
    )
    state_file = Path(
        state_path or os.getenv("BOT_BRANDING_STATE_PATH", "data/branding-state.json")
    )
    data = image.read_bytes()
    fingerprint = hashlib.sha256(data).hexdigest()
    try:
        state = json.loads(state_file.read_text())
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    if not client.user:
        return
    key = f"{client.user.id}:{fingerprint}"
    for part in ("avatar", "application"):
        if state.get(part) == key:
            continue
        try:
            if part == "avatar":
                await client.user.edit(avatar=data)
            else:
                app = await client.application_info()
                await app.edit(icon=data)
            state[part] = key
            state_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = state_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(state))
            temporary.replace(state_file)
            logger.info("Branding %s applied successfully", part)
        except Exception as exc:
            logger.warning("Branding %s could not be applied (%s)", part, type(exc).__name__)
