"""Validate the shipped UI translation resource and its printf placeholders."""
from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "asi" / "cdmodkit" / "data" / "locales.tsv"
EXPECTED = ["en", "zh-CN", "zh-TW", "de", "fr", "ko", "ja", "es", "pt-BR", "ru", "tr"]
LANGUAGE_NAMES = [
    "Auto (system)", "English", "Simplified Chinese", "Traditional Chinese", "German", "French",
    "Korean", "Japanese", "Spanish", "Portuguese (Brazil)", "Russian", "Turkish",
]
UNTRANSLATED_ALLOWLIST = {
    "World Builder [%s]",
    "0.1 m", "0.25 m", "0.5 m", "1 m", "2 m", "%lu s", "0x%llx", "in %s",
    "%lu s  %s  actor %p", "Browser", "COLLECTIONS",
}
PRINTF = re.compile(r"%(?!%)(?:[-+#0 ]*(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:hh|h|ll|l|I64|I32|z|t|j|L)?[diuoxXfFeEgGaAcspn])")


def unescape_pack(value: str) -> str:
    """Match the small escape set used by the runtime TSV loader."""
    return re.sub(r"\\([nrt\\])", lambda m: {"n": "\n", "r": "\r", "t": "\t", "\\": "\\"}[m.group(1)], value)


def strip_cpp_comments(source: str) -> str:
    """Remove C++ comments while preserving string literals and source offsets."""
    out = []
    i = 0
    quote = None
    escaped = False
    line_comment = block_comment = False
    while i < len(source):
        c = source[i]
        n = source[i + 1] if i + 1 < len(source) else ""
        if line_comment:
            if c == "\n":
                out.append("\n")
                line_comment = False
            else:
                out.append(" ")
        elif block_comment:
            if c == "*" and n == "/":
                out.extend("  ")
                block_comment = False
                i += 1
            else:
                out.append("\n" if c == "\n" else " ")
        elif quote:
            out.append(c)
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == quote:
                quote = None
        elif c == "/" and n == "/":
            out.extend("  ")
            line_comment = True
            i += 1
        elif c == "/" and n == "*":
            out.extend("  ")
            block_comment = True
            i += 1
        else:
            out.append(c)
            if c in "\"'":
                quote = c
        i += 1
    return "".join(out)


def translation_arguments(source: str):
    """Yield the source text inside each T/TStable call."""
    for match in re.finditer(r"\bT(?:Stable)?\s*\(", source):
        start = match.end()
        i = start
        depth = 0
        quote = None
        escaped = False
        while i < len(source):
            c = source[i]
            if quote:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == "(":
                depth += 1
            elif c == ")":
                if depth == 0:
                    break
                depth -= 1
            i += 1
        yield source[start:i], match.start()


def visible_translation_keys(argument: str):
    """Read one or more literal keys, including adjacent C++ strings and ternaries."""
    literals = []
    i = 0
    quote = None
    escaped = False
    conditional = False
    while i < len(argument):
        c = argument[i]
        if quote:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == quote:
                raw = argument[start + 1:i]
                try:
                    literals.append(json.loads('"' + raw + '"'))
                except json.JSONDecodeError:
                    literals.append(raw.replace(r"\n", "\n").replace(r"\t", "\t").replace(r"\\", "\\").replace(r'\"', '"'))
                quote = None
        elif c == '"':
            quote = c
            start = i
        elif c == "?":
            conditional = True
        i += 1
    if not literals:
        return []
    values = literals if conditional else ["".join(literals)]
    return [value.split("##", 1)[0].strip() for value in values if value.split("##", 1)[0].strip()]


def main() -> int:
    lines = PACK.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"empty locale pack: {PACK}")
    header = lines[0].split("\t")
    if header != ["key", *EXPECTED]:
        raise ValueError(f"unexpected locale columns: {header}")

    seen = set()
    count = 0
    for line_number, line in enumerate(lines[1:], 2):
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != len(header):
            raise ValueError(f"line {line_number}: expected 12 columns, got {len(fields)}")
        key, translations = fields[0], fields[1:]
        if not key:
            raise ValueError(f"line {line_number}: empty key")
        if key in seen:
            raise ValueError(f"line {line_number}: duplicate key {key!r}")
        if any(not value for value in translations):
            raise ValueError(f"line {line_number}: empty translation for {key!r}")
        if translations[0] != key:
            raise ValueError(f"line {line_number}: English value does not match key {key!r}")
        expected_tokens = PRINTF.findall(key)
        for language, value in zip(EXPECTED, translations):
            if PRINTF.findall(value) != expected_tokens:
                raise ValueError(f"line {line_number}: printf placeholders differ for {key!r} ({language})")
            if language != "en" and value == key and key not in UNTRANSLATED_ALLOWLIST:
                raise ValueError(f"line {line_number}: untranslated UI text for {key!r} ({language})")
        seen.add(key)
        count += 1

    missing_names = [name for name in LANGUAGE_NAMES if name not in seen]
    if missing_names:
        raise ValueError(f"missing localized language names: {missing_names}")

    runtime_keys = {unescape_pack(key) for key in seen}
    missing_keys = []
    for source_path in (ROOT / "asi" / "cdmodkit").glob("*.cpp"):
        source = strip_cpp_comments(source_path.read_text(encoding="utf-8"))
        for argument, offset in translation_arguments(source):
            for key in visible_translation_keys(argument):
                if key not in runtime_keys:
                    line_number = source.count("\n", 0, offset) + 1
                    missing_keys.append(f"{source_path.relative_to(ROOT)}:{line_number}: {key!r}")
    if missing_keys:
        raise ValueError("UI translation keys missing from locales.tsv:\n" + "\n".join(missing_keys))

    print(f"Locale pack OK: {count} entries, {len(EXPECTED)} languages, placeholders match and all UI keys are translated.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print(f"Locale validation failed: {exc}", file=sys.stderr)
        sys.exit(1)
