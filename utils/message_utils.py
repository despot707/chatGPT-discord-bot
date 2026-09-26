"""Safe Discord response chunking helpers."""

from __future__ import annotations

import discord


def split_message(text: str, limit: int = 1900) -> list[str]:
    """Split text at line or word boundaries when possible, under Discord limits."""
    chunks: list[str] = []
    remaining = text or "(empty response)"
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        # Include the boundary character so indentation and whitespace survive round trips.
        if cut < len(remaining) and remaining[cut] in "\n ":
            cut += 1
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


async def send_split_message(response: str, destination, *, ephemeral: bool = False) -> None:
    """Send every chunk to the original destination with consistent visibility."""
    allowed_mentions = discord.AllowedMentions.none()
    chunks = split_message(response)
    is_interaction = hasattr(destination, "followup")
    for chunk in chunks:
        if is_interaction:
            await destination.followup.send(
                chunk, ephemeral=ephemeral, allowed_mentions=allowed_mentions
            )
        else:
            await destination.channel.send(chunk, allowed_mentions=allowed_mentions)


async def send_response_with_images(
    response: dict, destination, *, ephemeral: bool = False
) -> None:
    content = response.get("content", "") or ""
    images = response.get("images") or []
    await send_split_message(content, destination, ephemeral=ephemeral)
    for image in images:
        if hasattr(destination, "followup"):
            await destination.followup.send(
                image, ephemeral=ephemeral, allowed_mentions=discord.AllowedMentions.none()
            )
        else:
            await destination.channel.send(image, allowed_mentions=discord.AllowedMentions.none())
