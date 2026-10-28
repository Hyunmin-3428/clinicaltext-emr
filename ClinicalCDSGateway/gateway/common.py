from __future__ import annotations

import hashlib
import json
import re
import unicodedata


class Denied(Exception):
    """Only predefined reason codes are surfaced; never source content."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def strict_json(data: bytes, max_bytes=70000):
    if len(data) > max_bytes:
        raise Denied("SIZE_LIMIT")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Denied("DUPLICATE_KEY")
            result[key] = value
        return result
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Denied("INVALID_JSON")))
    except Denied:
        raise
    except (ValueError, UnicodeError):
        raise Denied("INVALID_JSON") from None


def exact_schema(value, keys: set[str]):
    if type(value) is not dict or set(value) != keys:
        raise Denied("SCHEMA_DENIED")


def opaque(value: str):
    if type(value) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise Denied("INVALID_IDENTIFIER")


def plain_text(value: str):
    if type(value) is not str or not value or len(value.encode("utf-8")) > 32768:
        raise Denied("TEXT_LIMIT")
    if any(unicodedata.category(c) in {"Cf", "Cs", "Co", "Cn"} or
           (unicodedata.category(c) == "Cc" and c not in "\n\t") for c in value):
        raise Denied("UNSUPPORTED_ENCODING")
    if unicodedata.normalize("NFKC", value) != value:
        raise Denied("NONCANONICAL_TEXT")
    if re.search(r"(?i)<(?:html|script|svg|iframe)|data:|BEGIN (?:RSA |OPENSSH )?PRIVATE KEY|UEsDB|JVBERi0", value):
        raise Denied("UNSUPPORTED_CONTENT")
