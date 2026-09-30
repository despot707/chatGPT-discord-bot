"""Safe Discord response chunking helpers."""

from __future__ import annotations

import re

import discord

_MARKDOWN_LINK = re.compile(r"\[(?:\\.|[^\]\\])*\]\((?:<[^>\r\n]+>|[^\s)]+)\)")


def split_message(text: str, limit: int = 1900) -> list[str]:
    """Split text at line or word boundaries when possible, under Discord limits."""
    if limit <= 0:
        raise ValueError("Message length limit must be positive.")
    chunks: list[str] = []
    remaining = text or "(empty response)"
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        # Include the boundary character so indentation and whitespace survive round trips.
        if cut < limit and remaining[cut] in "\n ":
            cut += 1
        # Move the boundary before a link rather than splitting its title or URL.
        for link in _MARKDOWN_LINK.finditer(remaining):
            if link.start() >= cut:
                break
            if link.start() < cut < link.end() and len(link.group()) <= limit:
                cut = link.start() or link.end()
                break
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
    for index, chunk in enumerate(chunks):
        if is_interaction:
            await destination.followup.send(
                chunk, ephemeral=ephemeral, allowed_mentions=allowed_mentions
            )
        else:
            options = {"allowed_mentions": allowed_mentions}
            if index == 0 and callable(getattr(destination, "to_reference", None)):
                options["reference"] = destination.to_reference(fail_if_not_exists=False)
            await destination.channel.send(chunk, **options)


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
