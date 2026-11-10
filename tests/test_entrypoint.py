"""The entrypoint wraps the real command, so its own messages must never land on the command's stdout."""

from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parent.parent / "docker" / "entrypoint.sh"


def test_entrypoint_messages_go_to_stderr() -> None:
    lines = ENTRYPOINT.read_text(encoding="utf-8").splitlines()
    echoes = [(number, line) for number, line in enumerate(lines, start=1) if "echo " in line.strip()]

    assert echoes, "expected the entrypoint to print at least one message"
    stdout_echoes = [f"line {number}: {line.strip()}" for number, line in echoes if ">&2" not in line]
    assert stdout_echoes == []
