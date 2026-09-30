from __future__ import annotations

import io
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from PIL import Image
from src.aclient import DiscordClient
from src.config import BotConfig
from src.providers import CompletionResult, ImageInput, ProviderType


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color="red").save(buffer, format="PNG")
    return buffer.getvalue()


def sdk_poll():
    message = SimpleNamespace(
        created_at=datetime.now(timezone.utc),
        _state=object(),
        type=discord.MessageType.default,
        embeds=[],
        poll=None,
    )
    labels = ("Washington", "Lincoln", "FDR", "Reagan", "Obama", "Trump", "LBJ", "JFK")
    data = {
        "expiry": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "question": {"text": "Who was the best president?"},
        "answers": [
            {"answer_id": index, "poll_media": {"text": label}}
            for index, label in enumerate(labels, start=1)
        ],
        "allow_multiselect": False,
        "results": {
            "is_finalized": True,
            "answer_counts": [
                {"id": index, "count": count, "me_voted": False}
                for index, count in enumerate((0, 0, 1, 1, 0, 0, 1, 2), start=1)
            ],
        },
    }
    return discord.Poll._from_data(data=data, message=message, state=object())


class CapturingManager:
    def __init__(self):
        self.calls = []
        self.in_slot = False

    def get_provider(self, provider_type=None):
        return SimpleNamespace(default_model="fake-model")

    def get_provider_models(self, provider_type=None):
        return []

    async def complete(self, messages, *, provider_type=None, model=None, images=(), **kwargs):
        self.in_slot = True
        self.calls.append((messages, images))
        return CompletionResult(
            "answer",
            provider_type or ProviderType.GEMINI,
            model or "fake-model",
            (ProviderType.GEMINI,),
        )


def make_client(**config_values):
    manager = CapturingManager()
    config = BotConfig(discord_bot_token="token", cooldown_seconds=0, **config_values)
    return DiscordClient(config, manager), manager


class FakeChannel:
    def __init__(self, *, can_read=True, history_items=()):
        self.id = 22
        self.guild = SimpleNamespace(me=SimpleNamespace(id=77))
        self.send = AsyncMock()
        self.fetch_message = AsyncMock()
        self.history_items = list(history_items)
        self.can_read = can_read

    def permissions_for(self, member):
        allowed = self.can_read and getattr(member, "id", None) != 99
        return SimpleNamespace(view_channel=allowed, read_message_history=allowed)

    def typing(self):
        @asynccontextmanager
        async def _typing():
            yield

        return _typing()

    async def history(self, *, limit, before=None, oldest_first=True):
        for item in self.history_items[:limit]:
            yield item


def make_message(client, channel, *, content="", reference=None, mentions=(), author_id=8):
    return SimpleNamespace(
        author=SimpleNamespace(
            id=author_id,
            bot=False,
            display_name="Alex",
            name="Alex",
        ),
        webhook_id=None,
        mentions=list(mentions),
        reference=reference,
        channel=channel,
        guild=SimpleNamespace(id=11),
        attachments=[],
        content=content,
    )


def referenced_message(channel, *, content="", poll=None, attachments=(), message_id=1):
    return SimpleNamespace(
        id=message_id,
        channel_id=channel.id,
        channel=channel,
        author=SimpleNamespace(id=33, bot=False, display_name="Jordan", name="Jordan"),
        content=content,
        poll=poll,
        embeds=[],
        components=[],
        attachments=list(attachments),
    )


def submitted_text(manager):
    return manager.calls[0][0][-1]["content"]


@pytest.mark.asyncio
async def test_reply_uses_exact_older_poll_and_includes_closed_counts_alongside_recent_gif():
    recent_gif = SimpleNamespace(
        author=SimpleNamespace(bot=False, display_name="Casey", name="Casey"),
        webhook_id=None,
        content="https://example.test/recent.gif",
        poll=None,
        embeds=[],
        components=[],
    )
    client, manager = make_client(
        enable_message_content=True,
        automatic_context_count=1,
        interaction_channel_ids=frozenset({22}),
    )
    client._connection.user = SimpleNamespace(id=77)
    channel = FakeChannel(history_items=[recent_gif])
    poll_message = referenced_message(channel, poll=sdk_poll(), message_id=410)
    channel.fetch_message.return_value = poll_message
    reference = SimpleNamespace(
        message_id=410,
        channel_id=channel.id,
        resolved=referenced_message(channel, content="cached older message", message_id=410),
    )

    await client.on_message(
        make_message(
            client,
            channel,
            content="<@77> Explain the results",
            reference=reference,
            mentions=[client.user],
        )
    )

    text = submitted_text(manager)
    assert "Who was the best president?" in text
    assert "FDR: 1 votes" in text
    assert "Reagan: 1 votes" in text
    assert "LBJ: 1 votes" in text
    assert "JFK: 2 votes" in text
    assert "Washington: 0 votes" in text
    assert "Casey: https://example.test/recent.gif" in text
    assert channel.fetch_message.await_args.args == (410,)
    assert channel.fetch_message.await_count == 1


@pytest.mark.asyncio
async def test_channel_context_keeps_empty_text_poll_and_final_answer_counts():
    client, _ = make_client(enable_message_content=True)
    channel = FakeChannel(
        history_items=[
            SimpleNamespace(
                author=SimpleNamespace(bot=False, display_name="Jordan", name="Jordan"),
                webhook_id=None,
                content="",
                poll=sdk_poll(),
                embeds=[],
                components=[],
            )
        ]
    )

    context = await client._channel_context(channel, SimpleNamespace(id=8), 1)

    assert "Jordan: Poll question: Who was the best president?" in context
    assert "JFK: 2 votes" in context
    assert "Washington: 0 votes" in context


@pytest.mark.asyncio
async def test_uncached_same_channel_reference_is_fetched_once_inside_request_slot():
    client, manager = make_client(enable_message_content=True, automatic_context_count=0)
    client._connection.user = SimpleNamespace(id=77)
    channel = FakeChannel()
    target = referenced_message(channel, content="fresh source text", message_id=123)

    async def fetch(message_id):
        assert message_id == 123
        assert client._capacity._value < client.config.max_concurrent_requests
        return target

    channel.fetch_message.side_effect = fetch
    reference = SimpleNamespace(message_id=123, channel_id=channel.id, resolved=None)
    await client.on_message(
        make_message(
            client,
            channel,
            content="<@77> Summarize this",
            reference=reference,
            mentions=[client.user],
        )
    )

    assert channel.fetch_message.await_count == 1
    assert "Referenced message from Jordan: fresh source text" in submitted_text(manager)


@pytest.mark.asyncio
async def test_fetched_reference_refreshes_stale_cached_text():
    client, manager = make_client(enable_message_content=True, automatic_context_count=0)
    client._connection.user = SimpleNamespace(id=77)
    channel = FakeChannel()
    cached = referenced_message(channel, content="stale cached version", message_id=124)
    fresh = referenced_message(channel, content="updated source text", message_id=124)
    channel.fetch_message.return_value = fresh
    reference = SimpleNamespace(message_id=124, channel_id=channel.id, resolved=cached)

    await client.on_message(
        make_message(
            client,
            channel,
            content="<@77> What changed?",
            reference=reference,
            mentions=[client.user],
        )
    )

    text = submitted_text(manager)
    assert "updated source text" in text
    assert "stale cached version" not in text
    assert channel.fetch_message.await_count == 1


@pytest.mark.parametrize("error", [discord.NotFound, discord.Forbidden])
@pytest.mark.asyncio
async def test_missing_or_forbidden_reference_discards_cached_text_and_attachment(error):
    client, manager = make_client(enable_message_content=True, automatic_context_count=0)
    client._connection.user = SimpleNamespace(id=77)
    channel = FakeChannel()
    image = SimpleNamespace(
        size=len(png_bytes()), content_type="image/png", read=AsyncMock(return_value=png_bytes())
    )
    cached = referenced_message(
        channel,
        content="PRIVATE cached content",
        attachments=[image],
        message_id=125,
    )
    channel.fetch_message.side_effect = error(
        response=SimpleNamespace(
            status=404 if error is discord.NotFound else 403,
            reason="gone",
            text="gone",
        ),
        message="gone",
    )
    reference = SimpleNamespace(message_id=125, channel_id=channel.id, resolved=cached)

    await client.on_message(
        make_message(
            client,
            channel,
            content="<@77> Use this context",
            reference=reference,
            mentions=[client.user],
        )
    )

    text = submitted_text(manager)
    assert "PRIVATE cached content" not in text
    assert "Referenced message content is unavailable" in text
    assert manager.calls[0][1] == ()
    image.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_channel_or_unreadable_reference_is_not_fetched_or_included():
    client, manager = make_client(enable_message_content=True, automatic_context_count=0)
    client._connection.user = SimpleNamespace(id=77)
    channel = FakeChannel()
    secret_channel = SimpleNamespace(id=999)
    secret = referenced_message(secret_channel, content="cross-channel secret", message_id=126)
    reference = SimpleNamespace(message_id=126, channel_id=secret_channel.id, resolved=secret)

    await client.on_message(
        make_message(
            client,
            channel,
            content="<@77> handle this",
            reference=reference,
            mentions=[client.user],
        )
    )

    assert channel.fetch_message.await_count == 0
    assert "cross-channel secret" not in submitted_text(manager)

    denied_client, denied_manager = make_client(
        enable_message_content=True, automatic_context_count=0
    )
    denied_client._connection.user = SimpleNamespace(id=77)
    denied_channel = FakeChannel(can_read=False)
    denied_secret = referenced_message(
        denied_channel, content="permission denied secret", message_id=127
    )
    denied_reference = SimpleNamespace(
        message_id=127, channel_id=denied_channel.id, resolved=denied_secret
    )
    await denied_client.on_message(
        make_message(
            denied_client,
            denied_channel,
            content="<@77> handle this",
            reference=denied_reference,
            mentions=[denied_client.user],
        )
    )

    assert denied_channel.fetch_message.await_count == 0
    assert "permission denied secret" not in submitted_text(denied_manager)


@pytest.mark.asyncio
async def test_fresh_image_only_reference_supplies_image_to_vision_request():
    client, manager = make_client(enable_message_content=True, automatic_context_count=0)
    client._connection.user = SimpleNamespace(id=77)
    channel = FakeChannel()
    image = SimpleNamespace(
        size=len(png_bytes()), content_type="image/png", read=AsyncMock(return_value=png_bytes())
    )
    fresh = referenced_message(channel, attachments=[image], message_id=128)
    channel.fetch_message.return_value = fresh
    reference = SimpleNamespace(message_id=128, channel_id=channel.id, resolved=None)

    await client.on_message(
        make_message(
            client,
            channel,
            content="<@77> What is shown?",
            reference=reference,
            mentions=[client.user],
        )
    )

    assert manager.calls[0][1] == (ImageInput(png_bytes(), "image/png"),)
    image.read.assert_awaited_once()
