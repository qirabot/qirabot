"""Provider cache-prefix stability across consecutive decide steps.

Gemini's implicit prompt cache matches on a byte-identical request prefix.
These tests build the actual Vertex request bodies for two consecutive steps
of a simulated task and assert the machine-checkable property behind the hit
rate: system instruction, tools and the contents prefix through the
second-newest history triad must not change from one step to the next.
"""

from datetime import datetime

from qirabot.engine.prompts import build_conversation_messages, build_system_prompt
from qirabot.engine.providers.base import ChatRequest
from qirabot.engine.providers._gemini_wire import build_request_body
from qirabot.engine.tools import tool_definitions_for_platform
from qirabot.engine.types import ConversationTurn, DecisionInput

NOW = datetime(2026, 8, 2, 12, 0, 0)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def _turn(i: int) -> ConversationTurn:
    return ConversationTurn(
        action_type="click",
        action_params=f'{{"point_x":{i * 100},"point_y":{i * 100},"reason":"step {i}"}}',
        reasoning=f"step {i}",
        tool_output=f"result {i}",
    )


def _screenshot(i: int) -> bytes:
    return PNG + bytes([i])


def _request_body(step: int, notes: list[str]) -> dict:
    """Request body as the engine would build it for the given step: history
    is the last `step - 1` turns with a screenshot attached to the newest one,
    plus the current screenshot."""
    history = [_turn(i) for i in range(1, step)]
    if history:
        history[-1].screenshot_data = _screenshot(step - 1)
    input = DecisionInput(
        instruction="collect the video titles",
        platform="chrome",
        language="en",
        history=history,
        notes=notes,
        current_screenshot=_screenshot(step),
        is_first_step=step == 1,
    )
    system = build_system_prompt(
        input.platform, input.instruction, "", input.language, False, [], NOW
    )
    return build_request_body(
        ChatRequest(
            model="gemini-test",
            messages=build_conversation_messages(input),
            tools=tool_definitions_for_platform(input.platform),
            force_tool=True,
            system_prompt=system,
            params={},
        )
    )


class TestCachePrefixStability:
    def test_consecutive_steps_share_prefix(self) -> None:
        # Step 5: task, 3 text triads (6 blocks), image+triad 4, notes, screen.
        # Step 6: the same except triad 4 loses its image (it hopped to triad
        # 5) and the notes grew — both changes are at the tail.
        body_n = _request_body(5, notes=["note one"])
        body_n1 = _request_body(6, notes=["note one", "note two"])

        # Head of the token stream: byte-identical across steps.
        assert body_n1["systemInstruction"] == body_n["systemInstruction"]
        assert body_n1["tools"] == body_n["tools"]

        # Contents prefix through the second-newest triad: task message plus
        # triads 1..3 as two blocks each (functionCall + functionResponse).
        prefix_len = 1 + 3 * 2
        assert body_n1["contents"][:prefix_len] == body_n["contents"][:prefix_len]

        # And the divergence is exactly the screenshot hop, nothing earlier:
        # step N has triad 4's image here, step N+1 goes straight to its call.
        assert "inlineData" in body_n["contents"][prefix_len]["parts"][0]
        assert "functionCall" in body_n1["contents"][prefix_len]["parts"][0]

    def test_triad_text_stable_once_rendered(self) -> None:
        # A triad's text (functionCall + backfilled functionResponse) must be
        # byte-identical in every later request it appears in.
        body_n = _request_body(5, notes=[])
        body_n1 = _request_body(6, notes=[])
        # Triad 3 blocks: index 5 (call) and 6 (response) in both bodies.
        assert body_n1["contents"][5] == body_n["contents"][5]
        assert body_n1["contents"][6] == body_n["contents"][6]
