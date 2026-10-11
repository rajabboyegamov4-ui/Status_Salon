"""Text people type is shown next to other text: in Telegram messages, the admin board, the master's panel.

A name in Arabic, Persian or Hebrew script runs right-to-left, and without care it drags the rest of
the line with it ("س · +998 90 ..." turns into "07 25 524 33 998+ · س"). Hidden direction marks
(U+202E and friends) can also be typed on purpose to make a name or a number read backwards.
So: on the way in, names lose invisible control characters; on the way out, every piece of typed
text is isolated so it cannot reorder its neighbours, and phone numbers are always laid out left to right.
"""

import re
import unicodedata

FSI, LRI, PDI = "⁨", "⁦", "⁩"  # first-strong isolate, left-to-right isolate, pop isolate
_KEEP = {"‌", "‍"}  # zero-width (non-)joiners: needed inside Persian words and emoji sequences
_SPACES = re.compile(r"\s+")


def strip_controls(s: str | None) -> str:
    """Drop control and format characters (direction overrides, zero-width tricks), keep emoji joiners."""
    s = unicodedata.normalize("NFC", s or "")
    return "".join(ch for ch in s if ch in _KEEP or unicodedata.category(ch) not in ("Cc", "Cf", "Co", "Cn") or ch in "\n")


def clean_name(s: str | None, max_len: int = 120) -> str:
    """A person's name as it is stored: no hidden characters, single spaces, a sane length."""
    return _SPACES.sub(" ", strip_controls(s)).strip()[:max_len].strip()


def iso(s: str | None) -> str:
    """Typed text inside a line of other text: it keeps its own direction and leaves the rest alone."""
    return f"{FSI}{strip_controls(s)}{PDI}"


def ltr(s: str | None) -> str:
    """Numbers and codes: always left to right, even next to Arabic letters."""
    return f"{LRI}{strip_controls(s)}{PDI}"
