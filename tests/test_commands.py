import pytest

from reviewer.core.commands import parse_command


@pytest.mark.parametrize(
    "body,name,args",
    [
        ("@bandwidth-reviewer-dev", "review", []),
        ("\n \n@BANDWIDTH-REVIEWER-DEV ReViEw --full generic", "review", ["--full", "generic"]),
        ("@bandwidth-reviewer-dev help\nignored", "help", []),
        ("@bandwidth-reviewer-dev revew", "revew", []),
    ],
)
def test_commands(body, name, args):
    command = parse_command(body, "@bandwidth-reviewer-dev")
    assert command and (command.name, command.args) == (name, args)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "normal comment",
        "prefix @bandwidth-reviewer-dev review",
        "@bandwidth-reviewer review",
        "@bandwidth-reviewer-dev-alex review",
        "first line\n@bandwidth-reviewer-dev review",
        "@bandwidth-reviewer-dev, review",
    ],
)
def test_unrelated_text(body):
    assert parse_command(body, "@bandwidth-reviewer-dev") is None


def test_unknown_suggestion():
    command = parse_command("@bot revew", "@bot")
    assert command and command.suggestion == "review"
