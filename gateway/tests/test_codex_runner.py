from __future__ import annotations

from codex_gateway.codex_runner import _assistant_text_from_thread_item, _assistant_text_from_turn


def test_assistant_text_from_turn_supports_root_wrapped_thread_items() -> None:
    turn = type(
        "TurnStub",
        (),
        {
            "items": [
                type(
                    "ItemStub",
                    (),
                    {
                        "model_dump": staticmethod(
                            lambda mode="json": {
                                "root": {
                                    "type": "agentMessage",
                                    "text": "hello from assistant",
                                }
                            }
                        )
                    },
                )()
            ]
        },
    )()

    assert _assistant_text_from_turn(turn) == "hello from assistant"


def test_assistant_text_from_thread_item_supports_root_wrapped_agent_message() -> None:
    item = type(
        "ItemStub",
        (),
        {
            "model_dump": staticmethod(
                lambda mode="json": {
                    "root": {
                        "type": "agentMessage",
                        "text": "final reply",
                    }
                }
            )
        },
    )()

    assert _assistant_text_from_thread_item(item) == "final reply"
