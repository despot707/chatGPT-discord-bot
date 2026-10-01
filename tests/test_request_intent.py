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


@pytest.mark.parametrize(
    ("text", "prompt"),
    [
        ("make me an image of a cat", "a cat"),
        ("could you make a picture of a paper boat", "a paper boat"),
        ("I want an image of a cat", "a cat"),
        ("I want a picture: a castle at dawn", "a castle at dawn"),
        ("I want an illustration showing a robot", "showing a robot"),
        ("Please make a picture of a blue fox", "a blue fox"),
        ("Could you generate me an illustration of a cabin?", "a cabin?"),
        ("draw me a red fox", "a red fox"),
        ("create an artwork: mountains at sunset", "mountains at sunset"),
    ],
)
def test_natural_image_request_routes_directly_without_other_capabilities(text, prompt):
    intent = parse_request_intent(text)
    assert intent.draw_prompt == prompt
    assert intent.reasoning_requested is False
    assert intent.search_requested is False


@pytest.mark.parametrize(
    "text",
    [
        "draw up a plan for launch",
        "draw a card from this deck",
        "draw lots to pick a winner",
        "draw the curtains",
        "create an image prompt for a blue fox",
        "make me a picture prompt for a dragon",
        "generate an image-generation prompt for a cat",
        "draw a conclusion from these results",
        "Can you explain how to make an image of a cat?",
        "What happens if I say think harder?",
        "Tell me what 'search the web' means",
        "I wish you could make an image of a cat",
        "make an image",
        "search the web",
        "look up",
        '"search the web for weather"',
        "`make me an image of a cat`",
        "> use more effort on this",
        "```\nno extra effort\n```",
        "Please don't make me an image of a cat",
        "do not search the web for weather",
        "Please never use more effort on this",
        "I want an image prompt for a cat",
        "I want an image explained to me",
        "I want an image editor recommendation",
        "I want to understand image generation",
        "I want an image",
        "Think this through carefully is what the message said",
        "Explain how to think this through carefully",
        "Find the latest information/news about",
        "Find the latest information about",
        "Check current",
        "Check current through this resistor",
        "Check current variable in this code",
        "Find the latest value in this array",
        "Find the latest information in the pasted text",
        "I need the latest news summarized from this quote",
        "What is the latest news?",
        '"find the latest news about Mars"',
        "Please don't check current weather",
    ],
)
def test_discussion_idioms_quotes_and_incomplete_commands_do_not_request_extras(text):
    intent = parse_request_intent(text)
    assert intent.draw_prompt is None
    assert intent.reasoning_requested is False
    assert intent.search_requested is False


@pytest.mark.parametrize(
    "text",
    [
        "think harder about the tradeoffs",
        "Please use more effort on this answer",
        "could you please think hard about this?",
        "reason carefully about the alternatives",
        "think this through carefully: why did the test fail?",
        "could you think it through carefully before answering?",
    ],
)
def test_plain_language_more_effort_request(text):
    intent = parse_request_intent(text)
    assert intent.reasoning_requested is True
    assert intent.reasoning_disabled is False
    assert intent.search_requested is False
    assert intent.draw_prompt is None


@pytest.mark.parametrize(
    "text",
    [
        "no extra effort: just tell me the answer",
        "Please don't think hard about this",
        "please don’t think hard about this",
        "do not use more effort on this answer",
        "no need to think carefully about this",
    ],
)
def test_explicit_no_effort_overrides_a_stored_preference(text):
    intent = parse_request_intent(text)
    assert intent.reasoning_disabled is True
    assert intent.reasoning_requested is False
    assert intent.search_requested is False
    assert intent.draw_prompt is None


@pytest.mark.parametrize(
    "text",
    [
        '"no extra effort: answer this"',
        "Please explain the phrase don't think hard",
        "I said no extra effort yesterday",
        "`don't think hard`",
    ],
)
def test_discussing_no_effort_does_not_change_request_preference(text):
    assert parse_request_intent(text).reasoning_disabled is False


@pytest.mark.parametrize(
    "text",
    [
        "search the web for today’s weather",
        "Please look up the latest launch date",
        "could you search online for the results?",
        "search the internet for train schedules",
        "look up how to draw a cat",
        "search the web for the phrase think harder",
        "find the latest information about the launch",
        "Please find the latest news about Mars",
        "check current weather in London",
        "could you check the current price of gold?",
        "check current train schedules for tomorrow",
    ],
)
def test_explicit_web_request_selects_search_without_inference_from_query(text):
    intent = parse_request_intent(text)
    assert intent.search_requested is True
    assert intent.reasoning_requested is False
    assert intent.reasoning_disabled is False
    assert intent.draw_prompt is None


@pytest.mark.parametrize(
    "text",
    [
        "think carefully and search the web for today's weather",
        "use more effort and look up the current price",
    ],
)
def test_explicit_combined_web_and_reasoning_is_visible_to_the_caller(text):
    intent = parse_request_intent(text)
    assert intent.reasoning_requested is True
    assert intent.search_requested is True
    assert intent.draw_prompt is None


def test_no_effort_prefix_can_precede_a_direct_web_request():
    intent = parse_request_intent("No extra effort: search the web for today's news")
    assert intent.reasoning_disabled is True
    assert intent.reasoning_requested is False
    assert intent.search_requested is True
