import pytest
from src.request_intent import parse_request_intent


@pytest.mark.parametrize(
    "text",
    [
        "What is the answer?",
        "why do you reason that way?",
        "reason for the delay was unclear",
        "think of a game we could play",
        "I can draw a map, but should I?",
        "draw a conclusion from this",
        "draw conclusions from this",
        "draw attention to this",
        "draw a comparison",
        "draw from experience",
        "https://example.test/draw-a-cat",
        '"draw a cat"',
        "'think carefully about this'",
        "`draw a cat`",
        "```text\nthink step by step\n```",
        "> please draw a cat",
        "please don't draw a cat",
        "Do not reason about this.",
    ],
)
def test_ordinary_quoted_code_and_negated_text_does_not_request_extras(text):
    assert parse_request_intent(text).reasoning_requested is False
    assert parse_request_intent(text).draw_prompt is None


@pytest.mark.parametrize(
    "text",
    [
        "reason through the tradeoffs",
        "Please reason step by step about the answer",
        "reason about the tradeoffs",
        "Can you think carefully about this?",
        "could you please think step by step and explain",
        "think deeply about this",
    ],
)
def test_leading_reasoning_request_is_explicit(text):
    assert parse_request_intent(text).reasoning_requested is True
    assert parse_request_intent(text).draw_prompt is None


@pytest.mark.parametrize(
    ("text", "prompt"),
    [
        ("draw a blue bird", "a blue bird"),
        ("Please draw: a blue bird", "a blue bird"),
        ("can you please generate an image of a red fox", "a red fox"),
        ("Create a picture: a cabin in snow", "a cabin in snow"),
    ],
)
def test_leading_image_request_is_explicit(text, prompt):
    assert parse_request_intent(text).draw_prompt == prompt
