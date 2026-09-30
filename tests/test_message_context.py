from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
from src.message_context import serialize_message


def sdk_poll(*, include_results: bool, finalized: bool = True):
    message = SimpleNamespace(
        created_at=datetime.now(timezone.utc),
        _state=object(),
        type=discord.MessageType.default,
        embeds=[],
        poll=None,
    )
    answers = [
        {"answer_id": index, "poll_media": {"text": label}}
        for index, label in enumerate(
            ("JFK", "FDR", "Reagan", "LBJ", "Carter", "Clinton", "Obama", "Trump"),
            start=1,
        )
    ]
    data = {
        "expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "question": {"text": "Who's your GOAT?"},
        "answers": answers,
        "allow_multiselect": False,
    }
    if include_results:
        data["results"] = {
            "is_finalized": finalized,
            "answer_counts": [
                {"id": index, "count": count, "me_voted": False}
                for index, count in enumerate((2, 1, 0, 1, 0, 0, 0, 0), start=1)
            ],
        }
    return discord.Poll._from_data(data=data, message=message, state=object())


def test_sdk_final_poll_exposes_question_options_and_exact_zero_counts():
    rendered = serialize_message(SimpleNamespace(content="", poll=sdk_poll(include_results=True)))

    assert "Poll question: Who's your GOAT?" in rendered
    assert "Poll status: closed" in rendered
    assert "JFK: 2 votes" in rendered
    assert "FDR: 1 votes" in rendered
    assert "Reagan: 0 votes" in rendered
    assert "LBJ: 1 votes" in rendered
    assert rendered.count("- ") == 8
    assert "Total votes: 4" in rendered


def test_sdk_poll_without_results_does_not_turn_default_zeroes_into_votes():
    rendered = serialize_message(SimpleNamespace(content="", poll=sdk_poll(include_results=False)))

    assert "Poll status: open" in rendered
    assert "JFK: unknown votes" in rendered
    assert "Reagan: unknown votes" in rendered
    assert "Total votes:" not in rendered


def test_live_sdk_poll_labels_positive_counts_as_provisional():
    rendered = serialize_message(
        SimpleNamespace(content="", poll=sdk_poll(include_results=True, finalized=False))
    )

    assert "Poll status: open" in rendered
    assert "JFK: 2 reported (provisional) votes" in rendered
    assert "Reagan: unknown votes" in rendered


def test_embeds_and_visible_select_options_are_included_without_control_ids():
    message = SimpleNamespace(
        content="",
        poll=None,
        embeds=[
            SimpleNamespace(
                title="Poll results",
                description="Finished",
                fields=[SimpleNamespace(name="total_votes", value="4")],
            )
        ],
        components=[
            SimpleNamespace(
                label=None,
                text=None,
                custom_id="secret-control",
                options=[SimpleNamespace(label="Choice A", description="First choice")],
            ),
            SimpleNamespace(
                children=[SimpleNamespace(label=None, text=None, content="A visible card")],
                accessory=SimpleNamespace(label="Open details", custom_id="private-button-id"),
            ),
        ],
    )
    rendered = serialize_message(message)

    assert "Embed title: Poll results" in rendered
    assert "Embed field total_votes: 4" in rendered
    assert "Select option: Choice A — First choice" in rendered
    assert "Component text: A visible card" in rendered
    assert "Component label: Open details" in rendered
    assert "secret-control" not in rendered
    assert "private-button-id" not in rendered


def test_serializer_caps_output_length():
    rendered = serialize_message(SimpleNamespace(content="x" * 1000, poll=None), max_chars=120)
    assert len(rendered) == 120
