import builtins
import sys
from typing import Any, TextIO


def configure_utf8_stream(stream: TextIO) -> TextIO:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
    return stream


def configure_utf8_console() -> None:
    configure_utf8_stream(sys.stdout)
    configure_utf8_stream(sys.stderr)


def safe_print(*values: Any, file: TextIO = None, **kwargs: Any) -> None:
    target = file or sys.stdout
    try:
        builtins.print(*values, file=target, **kwargs)
    except UnicodeEncodeError:
        separator = str(kwargs.get("sep", " "))
        end = str(kwargs.get("end", "\n"))
        text = separator.join(str(value) for value in values) + end
        encoding = getattr(target, "encoding", None) or "ascii"
        target.write(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))
        flush = kwargs.get("flush", False)
        if flush and hasattr(target, "flush"):
            target.flush()
