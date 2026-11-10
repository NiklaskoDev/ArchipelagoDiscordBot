import pytest

from apbot.manager.views import FEED_FIELDS, FeedEntry
from apbot.ui.feed import FEED_KIND_LABELS, MAX_MESSAGES, TEXT_LIMIT, batch_lines, feed_line
from apbot.ui.format import ZERO_WIDTH_SPACE
from apbot.ui.style import MESSAGE


def make(kind: str, **fields) -> FeedEntry:
    return FeedEntry(seq=1, at=0.0, kind=kind, fields=fields)


def test_every_kind_has_a_line_and_a_label():
    assert set(FEED_KIND_LABELS) == set(FEED_FIELDS)
    for kind in FEED_FIELDS:
        assert feed_line(make(kind), "en")  # even an entry with no fields at all makes a line


@pytest.mark.parametrize(
    ("entry", "line"),
    [
        (make("goal", player="Alice"), "**Alice** reached their goal"),
        (make("release", player="Alice"), "**Alice** released all remaining items from their world"),
        (make("collect", player="Alice"), "**Alice** collected their items from other worlds"),
        (make("join", player="Alice", game="Timespinner"), "**Alice** joined (Timespinner)"),
        (make("part", player="Alice"), "**Alice** left"),
        (make("server", text="Server restarts soon"), "Server: Server restarts soon"),
        (make("chat", player="Alice", text="hello there"), "**Alice**: hello there"),
        (
            make("item", finder="Bob", item="Sword", receiver="Alice", location="Chest"),
            "**Bob** sent Sword to **Alice** (Chest)",
        ),
        (
            make("hint", receiver="Alice", item="Sword", location="Chest", finder="Bob", status="priority"),
            "Hint: **Alice**'s Sword is at Chest in **Bob**'s world (priority)",
        ),
        (make("status", player="Alice", status="stuck"), "**Alice** changed their status to Stuck"),
        (make("status", player="Alice", status="away", note="back at 8"), "**Alice** changed their status to Away — back at 8"),
        (make("status", player="Alice"), "**Alice** cleared their status"),
        (
            make("handover", player="Alice", **{"from": "alice", "to": "bob", "note": "moving"}),
            "**alice** offers **Alice** to **bob** — moving",
        ),
        (make("handover", player="Alice", **{"from": "alice"}), "**alice** offers **Alice** to anyone who takes it"),
        (
            make("handover_done", player="Alice", to="bob", **{"from": "alice"}),
            "**bob** took over **Alice** from **alice**",
        ),
        (make("handover_done", player="Alice", to="bob"), "**bob** took over **Alice**"),
    ],
)
def test_each_kind_reads_as_one_plain_line(entry, line):
    assert feed_line(entry, "en") == line


def test_lines_follow_the_language_of_the_server():
    assert feed_line(make("goal", player="Alice"), "de") == "**Alice** hat das Ziel erreicht"
    assert feed_line(make("part", player="Alice"), "fr") == "**Alice** est parti"
    assert feed_line(make("item", finder="B", item="I", receiver="R", location="L"), "nl") == (
        "**B** heeft I naar **R** gestuurd (L)"
    )


def test_hint_statuses_are_translated_and_unknown_ones_shown_as_they_are():
    fields = {"receiver": "A", "item": "I", "location": "L", "finder": "F"}
    assert feed_line(make("hint", status="found", **fields), "de").endswith("(gefunden)")
    assert feed_line(make("hint", status="unspecified", **fields), "en").endswith("(not found)")
    assert feed_line(make("hint", status="mystery", **fields), "en").endswith("(mystery)")


def test_missing_names_show_a_question_mark():
    assert feed_line(make("goal"), "en") == "**?** reached their goal"


def test_what_players_wrote_cannot_ping_format_or_pose_as_the_bot():
    line = feed_line(make("chat", player="*Evil*", text="@everyone <@123> **bold** [x](http://e.vil)\nsecond line"), "en")
    assert "\n" not in line
    assert "@everyone" not in line and f"@{ZERO_WIDTH_SPACE}everyone" in line
    assert "<@" not in line.replace(f"<{ZERO_WIDTH_SPACE}", "")
    assert "\\*\\*bold\\*\\*" in line and "\\*Evil\\*" in line
    assert "\\[x](" in line  # the link syntax is broken up, so nothing is clickable text


def test_a_long_chat_line_is_cut():
    line = feed_line(make("chat", player="A", text="x" * 5000), "en")
    assert len(line) < TEXT_LIMIT + 50 and line.endswith("…")


def test_a_player_name_cannot_break_out_of_the_bold_part():
    line = feed_line(make("goal", player="a** and **b"), "en")
    assert line == "**a\\*\\* and \\*\\*b** reached their goal"


# --- batching ----------------------------------------------------------------------------------------------------------
def test_a_few_lines_become_one_message():
    assert batch_lines(["one", "two", "three"], "en") == ["one\ntwo\nthree"]


def test_no_lines_make_no_message():
    assert batch_lines([], "en") == []


def test_lines_are_split_into_messages_that_fit_without_cutting_a_line():
    lines = [f"line {i} " + "x" * 190 for i in range(30)]
    messages = batch_lines(lines, "en")
    assert 1 < len(messages) <= MAX_MESSAGES
    assert all(len(m) <= MESSAGE for m in messages)
    assert "\n".join(messages).split("\n") == lines  # nothing lost, nothing cut, order kept


def test_after_five_messages_the_rest_is_counted_not_dropped_silently():
    lines = [f"line {i} " + "x" * 900 for i in range(40)]  # two lines per message
    messages = batch_lines(lines, "en")
    assert len(messages) == MAX_MESSAGES
    assert all(len(m) <= MESSAGE for m in messages)
    shown = sum(len(m.split("\n")) for m in messages) - 1  # minus the overflow line
    assert messages[-1].endswith(f"…and {len(lines) - shown} more events")
    assert shown < len(lines)


def test_the_overflow_line_makes_room_for_itself_when_the_last_message_is_full():
    lines = ["y" * (MESSAGE - 1)] * 8  # one line per message, each nearly full
    messages = batch_lines(lines, "en")
    assert len(messages) == MAX_MESSAGES
    assert all(len(m) <= MESSAGE for m in messages)
    assert messages[-1] == "…and 4 more events"  # the last line made way for the count (3 left out + itself)


def test_the_overflow_line_is_in_the_servers_language():
    lines = [f"line {i} " + "x" * 900 for i in range(40)]
    assert batch_lines(lines, "de")[-1].split("\n")[-1].startswith("…und ")


def test_a_single_line_longer_than_a_message_is_cut_to_fit():
    [message] = batch_lines(["z" * 5000], "en")
    assert len(message) == MESSAGE and message.endswith("…")
