"""Finds the texts that need translating by reading the source: no import, so nothing can be missed by a code path
that tests do not run."""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "apbot"
MESSAGE_CALLS = {"t", "translatable", "UserFacingError"}  # first argument is an English text for MESSAGES
COMMAND_CALLS = {"locale_str"}  # first argument is an English text for COMMANDS


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def scan() -> tuple[set[str], set[str], list[str]]:
    """(texts for MESSAGES, texts for COMMANDS, places where a command text is not a plain string)."""
    messages: set[str] = set()
    commands: set[str] = set()
    problems: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.parent.name == "i18n" and path.name != "__init__.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = _called_name(node)
            first = node.args[0]
            literal = isinstance(first, ast.Constant) and isinstance(first.value, str)
            if name in MESSAGE_CALLS and literal:
                messages.add(first.value)
            elif name in COMMAND_CALLS:
                if literal:
                    commands.add(first.value)
                else:
                    problems.append(f"{path.name}:{node.lineno} locale_str() needs a plain string")
    return messages, commands, problems
