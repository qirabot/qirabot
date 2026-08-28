"""Knowledge resolution for bot.ai(): turn the ``knowledge`` argument into text.

``knowledge`` accepts the knowledge text itself (``str``), a local file
(``pathlib.Path``, read as UTF-8), or a list mixing both. Resolution happens
up front — the engine only ever receives the final text. Remote
sources are deliberately unsupported: fetch them yourself (e.g.
``requests.get(url).text``) so auth, timeouts, and failure handling stay in
your code, and a broken fetch raises instead of silently injecting garbage
(an unauthenticated intranet wiki typically answers 200 with a login page).
"""

from __future__ import annotations

from pathlib import Path

# Combined-size ceiling, in UTF-8 bytes — the engine's authoritative limit
# (declared once, in engine.custom_tools); checking it here too fails before
# any model call. Large enough for a real rules document, small enough that
# the knowledge section cannot drown the rest of the prompt.
from qirabot.engine.custom_tools import MAX_KNOWLEDGE_BYTES


def resolve_knowledge(knowledge: str | Path | list[str | Path]) -> str:
    """Resolve ``knowledge`` into the text handed to the engine.

    Raises ``ValueError`` for unsupported entry types, unreadable files, or a
    combined size over ``MAX_KNOWLEDGE_BYTES``. The over-limit error names each
    source with its size so the caller knows what to trim.
    """
    parts = knowledge if isinstance(knowledge, list) else [knowledge]
    texts: list[tuple[str, str]] = []  # (source label, text)
    for i, part in enumerate(parts):
        if isinstance(part, Path):
            try:
                text = part.read_text(encoding="utf-8")
            except OSError as e:
                raise ValueError(f"knowledge: cannot read {part}: {e}") from None
            except UnicodeDecodeError:
                raise ValueError(f"knowledge: {part} is not UTF-8 text") from None
            texts.append((str(part), text))
        elif isinstance(part, str):
            texts.append((f"text #{i + 1}", part))
        else:
            raise ValueError(
                f"knowledge: entries must be str or pathlib.Path, got {type(part).__name__}"
            )

    combined = "\n\n".join(text.strip() for _, text in texts if text.strip())
    total = len(combined.encode("utf-8"))
    if total > MAX_KNOWLEDGE_BYTES:
        breakdown = ", ".join(
            f"{label}: {len(text.encode('utf-8'))} bytes" for label, text in texts
        )
        raise ValueError(
            f"knowledge: {total} bytes exceeds the {MAX_KNOWLEDGE_BYTES}-byte limit"
            f" ({breakdown})"
        )
    return combined
