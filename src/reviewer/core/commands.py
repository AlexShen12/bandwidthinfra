from dataclasses import dataclass
from difflib import get_close_matches


@dataclass(frozen=True)
class Command:
    name: str
    args: list[str]

    @property
    def suggestion(self) -> str | None:
        matches = get_close_matches(self.name, ["review", "help"], n=1, cutoff=0.5)
        return matches[0] if matches else None


def parse_command(body: str, trigger: str) -> Command | None:
    """Only the first non-empty line is a command; the trigger is an exact token."""
    line = next((line.strip() for line in body.splitlines() if line.strip()), "")
    tokens = line.split()
    if not tokens or tokens[0].casefold() != trigger.casefold():
        return None
    return Command(tokens[1].casefold() if len(tokens) > 1 else "review", tokens[2:])
