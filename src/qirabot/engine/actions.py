"""Action-type and platform constants.

Mirrors the server's internal/action package (action.go + platform.go) so the
tool registry and session logic reference the same wire strings the SDK
dispatcher already understands.
"""

from __future__ import annotations

# Navigation actions
CLICK = "click"
DOUBLE_CLICK = "double_click"
RIGHT_CLICK = "right_click"
HOVER = "hover"
SCROLL = "scroll"
SCROLL_AT = "scroll_at"
DRAG = "drag"
LONG_PRESS = "long_press"
MOUSE_DOWN = "mouse_down"
MOUSE_UP = "mouse_up"
KEY_DOWN = "key_down"
KEY_UP = "key_up"

# Input actions
TYPE_TEXT = "type_text"
TYPE_TEXT_DIRECT = "type_text_direct"
CLEAR_TEXT = "clear_text"
PRESS_KEY = "press_key"

# AI-driven actions
AI_DECISION = "ai_decision"
WAIT_FOR = "wait_for"
ASSERT = "assert"
EXTRACT = "extract"
LOCATE = "locate"  # coordinate resolution only; the client does not execute

# System actions
SCREENSHOT = "take_screenshot"
WAIT = "wait"
SAVE_NOTE = "save_note"
DONE = "done"
NAVIGATE = "navigate"
GO_BACK = "go_back"
CLOSE_TAB = "close_current_tab"

# Platform constants (must match the SDK adapter device types).
PLATFORM_ANDROID = "android"
PLATFORM_IOS = "ios"
PLATFORM_CHROME = "chrome"
PLATFORM_DESKTOP = "desktop"
