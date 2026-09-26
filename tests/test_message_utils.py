from types import SimpleNamespace
from unittest.mock import AsyncMock

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
