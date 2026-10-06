"""
What CreditProbe is allowed to do to a question before Opus reads it.

The rule
--------
Mechanical only. Unicode normalisation, whitespace, and the invisible
control characters that change how text *renders* without being part of it.
Nothing that touches meaning: no spelling correction, no translation, no
transliteration, no case folding, no digit conversion, no expansion of an
abbreviation, no re-ordering of a clause.

Why the line is there
---------------------
"wat is credt probe" and "construction ka stage 2 exposure last year kitna
badha" are clear requests. Correcting them here would mean CreditProbe
deciding what the user meant — which is the one thing this build gives to the
analyst and to nothing else. A pre-processing model for spelling is the V3
mistake wearing a smaller hat: it adds a call, it adds a failure mode, and it
puts a second opinion between the user and the analyst.

So the analyst receives the sentence the user typed. It infers the spelling,
the language and the intent, because it is the component that is allowed to.

What IS removed, and why
------------------------
Bidirectional override and embedding controls (U+202A..U+202E, U+2066..U+2069)
do not carry meaning; they instruct a renderer to display characters in an
order other than their logical one. Left in place they let text read one way
to the user and another way to everything downstream. Removing them changes
no word. Zero-width characters are removed for the same reason.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

#: Renderer instructions, not content. Their removal is recorded.
_BIDI_CONTROLS = "‪‫‬‭‮⁦⁧⁨⁩"
#: Invisible and non-joining characters that survive a copy-paste.
_ZERO_WIDTH = "​‌‍⁠﻿"
#: Anything the Unicode standard calls a space, folded to U+0020.
_ODD_SPACES = "          " \
              "     　"

_CRLF = re.compile(r"\r\n?")
#: Each separator becomes one newline. Runs are NOT collapsed here: a user
#: who pastes context, leaves a blank line and then asks the question has
#: written a paragraph break, and flattening it loses the shape of the ask.
_LINE_SEPARATORS = {ord(c): "\n" for c in "  \x0b\x0c"}
_HORIZONTAL_RUN = re.compile(r"[ \t]{2,}")
_BLANK_RUN = re.compile(r"\n{3,}")


@dataclass
class Normalization:
    """The question as it will be carried, and exactly what was done to it."""

    text: str
    original: str
    applied: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.text != self.original

    def report(self) -> dict[str, object]:
        return {
            "changed": self.changed,
            "applied": list(self.applied),
            "original_characters": len(self.original),
            "normalized_characters": len(self.text),
            "policy": ("Mechanical only: Unicode NFC, whitespace, and "
                       "invisible or bidirectional control characters. No "
                       "spelling, wording, language, number, date, name or "
                       "abbreviation was changed."),
        }


def normalize_question(raw: str) -> Normalization:
    """Apply the mechanical transformations, and name each one applied.

    Newlines survive as newlines: a user who pastes a paragraph of context and
    then asks the question on its own line has written structure, and
    flattening it would lose the shape of what they asked.
    """
    original = raw if isinstance(raw, str) else str(raw)
    text = original
    applied: list[str] = []

    composed = unicodedata.normalize("NFC", text)
    if composed != text:
        applied.append("unicode_nfc")
        text = composed

    stripped = text.translate({ord(c): None for c in _ZERO_WIDTH})
    if stripped != text:
        applied.append("removed_zero_width")
        text = stripped

    unbidi = text.translate({ord(c): None for c in _BIDI_CONTROLS})
    if unbidi != text:
        applied.append("removed_bidi_controls")
        text = unbidi

    spaced = text.translate({ord(c): " " for c in _ODD_SPACES})
    if spaced != text:
        applied.append("folded_unicode_spaces")
        text = spaced

    lines = _CRLF.sub("\n", text).translate(_LINE_SEPARATORS)
    if lines != text:
        applied.append("normalized_line_breaks")
        text = lines

    collapsed = _BLANK_RUN.sub("\n\n", _HORIZONTAL_RUN.sub(" ", text))
    if collapsed != text:
        applied.append("collapsed_whitespace")
        text = collapsed

    trimmed = "\n".join(line.strip() for line in text.split("\n")).strip()
    if trimmed != text:
        applied.append("trimmed")
        text = trimmed

    return Normalization(text=text, original=original, applied=applied)


__all__ = ["Normalization", "normalize_question"]
