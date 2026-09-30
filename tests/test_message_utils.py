from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from utils.message_utils import send_split_message, split_message


def test_split_message_respects_limit_and_keeps_all_text():
    source = "abc def\n" * 500
    chunks = split_message(source, limit=40)
    assert all(len(chunk) <= 40 for chunk in chunks)
    assert "".join(chunks) == source


@pytest.mark.asyncio
async def test_every_private_interaction_chunk_is_ephemeral_and_no_mentions():
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
    await send_split_message("hello " * 700, interaction, ephemeral=True)
    assert interaction.followup.send.await_count > 1
    for call in interaction.followup.send.await_args_list:
        assert call.kwargs["ephemeral"] is True
        assert call.kwargs["allowed_mentions"].everyone is False


@pytest.mark.parametrize(
    "link",
    [
        "[a useful source](<https://example.com/news>)",
        "[source](https://example.com/news)",
        r"[escaped \[title\]](<https://example.com/news>)",
    ],
)
def test_split_keeps_markdown_citations_atomic_and_preserves_text(link):
    source = "x " * 30 + link + " more" * 30
    chunks = split_message(source, limit=65)
    assert all(len(chunk) <= 65 for chunk in chunks)
    assert "".join(chunks) == source
    assert any(link in chunk for chunk in chunks)


def test_split_never_exceeds_limit_for_space_exactly_at_boundary():
    source = "a" * 40 + " " + "b" * 50
    chunks = split_message(source, limit=40)
    assert max(map(len, chunks)) <= 40
    assert "".join(chunks) == source


@pytest.mark.asyncio
async def test_channel_first_chunk_references_message_without_ping_and_other_chunks_do_not():
    reference = object()
    message = SimpleNamespace(
        channel=SimpleNamespace(send=AsyncMock()),
        to_reference=Mock(return_value=reference),
    )
    await send_split_message("hello " * 700, message)
    calls = message.channel.send.await_args_list
    assert len(calls) > 1
    message.to_reference.assert_called_once_with(fail_if_not_exists=False)
    assert calls[0].kwargs["reference"] is reference
    assert all("reference" not in call.kwargs for call in calls[1:])
    assert all(call.kwargs["allowed_mentions"].replied_user is False for call in calls)


@pytest.mark.asyncio
async def test_message_without_reference_helper_still_sends():
    message = SimpleNamespace(channel=SimpleNamespace(send=AsyncMock()))
    await send_split_message("hello", message)
    assert "reference" not in message.channel.send.await_args.kwargs
