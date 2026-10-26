"""Prepare seeded contexts, run explicit live evaluation, and score human judgments."""

import argparse
import contextlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from reviewer.infra.bedrock import BedrockClient
from reviewer.infra.config import Settings
from reviewer.infra.models import CATEGORIES
from reviewer.infra.reviewer import InfrastructureReviewer
from reviewer.infra.workspace import GitSnapshots, collect_context, safe_path


@contextlib.contextmanager
def fixture_context(case):
    with tempfile.TemporaryDirectory(prefix="infra-fixture-") as directory:
        root = Path(directory)
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": "2026-10-25T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-10-25T00:00:00Z",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }

        def git(*args):
            return (
                subprocess.check_output(
                    ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args],
                    cwd=root,
                    env=env,
                    stderr=subprocess.PIPE,
                )
                .decode()
                .strip()
            )

        git("init", "-q")
        git("config", "user.name", "Infrastructure fixture")
        git("config", "user.email", "fixture@example.invalid")
        snapshots = []
        previous = set()
        for name in ("base_files", "head_files"):
            files = {**case.get("shared_files", {}), **case[name]}
            for path in previous - files.keys():
                (root / path).unlink()
            for path, text in files.items():
                if not safe_path(path):
                    raise ValueError("Unsafe fixture path")
                destination = root / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(text)
            git("add", "-A")
            git("commit", "-qm", name, "--allow-empty")
            snapshots.append(git("rev-parse", "HEAD"))
            previous = set(files)
        with GitSnapshots(*snapshots, local_path=root) as source:
            yield collect_context(
                source,
                repo="fixture/infra",
                pr=1,
                target_base_sha=snapshots[0],
                title=case["title"],
            )


def score(cases, runs, judgments):
    expected = {
        case["id"]: {item["id"]: item["category"] for item in case["expected"]} for case in cases
    }
    if set(runs) != set(expected):
        raise ValueError(
            "Scoring requires a result for every fixture; missing runs cannot be hidden."
        )
    index, matched = {}, set()
    for judgment in judgments:
        key = (judgment["case_id"], judgment["finding_index"])
        if key in index:
            raise ValueError("Each finding can have only one human judgment.")
        index[key] = judgment.get("expected_id")
    counts = {category: Counter(tp=0, fp=0, fn=0) for category in CATEGORIES}
    valid_keys = set()
    for case_id, run in runs.items():
        if run.get("stats", {}).get("simulated") or run.get("error") or "result" not in run:
            raise ValueError(
                "Simulated, failed, or missing analyses cannot be scored as model precision."
            )
        for number, finding in enumerate(run["result"]["findings"]):
            key = (case_id, number)
            valid_keys.add(key)
            if key not in index:
                raise ValueError(
                    "Human adjudication is incomplete; every finding needs a judgment."
                )
            expected_id = index[key]
            category = finding["category"]
            if expected_id is None:
                counts[category]["fp"] += 1
                continue
            if (
                expected_id not in expected[case_id]
                or expected[case_id][expected_id] != category
                or (case_id, expected_id) in matched
            ):
                raise ValueError(
                    "Judgment refers to an unknown, wrong-category, or already matched expectation."
                )
            matched.add((case_id, expected_id))
            counts[category]["tp"] += 1
    if set(index) - valid_keys:
        raise ValueError("Judgments refer to nonexistent findings.")
    for case_id, items in expected.items():
        for expected_id, category in items.items():
            if (case_id, expected_id) not in matched:
                counts[category]["fn"] += 1
    return {
        category: {
            **dict(count),
            "precision": count["tp"] / (count["tp"] + count["fp"])
            if count["tp"] + count["fp"]
            else None,
            "recall": count["tp"] / (count["tp"] + count["fn"])
            if count["tp"] + count["fn"]
            else None,
        }
        for category, count in counts.items()
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "run", "score"])
    parser.add_argument("--fixtures", type=Path, default=Path("eval/cases.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/evaluation"))
    parser.add_argument("--prompt", type=Path, default=Path(".github/infra-reviewer/prompt.md"))
    parser.add_argument("--judgments", type=Path)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Required for run: explicitly authorize paid Bedrock calls",
    )
    args = parser.parse_args(argv)
    cases = json.loads(args.fixtures.read_text())
    if args.command == "score":
        if not args.judgments:
            parser.error("score requires --judgments")
        runs = {
            case["id"]: json.loads((args.output / f"{case['id']}.json").read_text())
            for case in cases
        }
        print(json.dumps(score(cases, runs, json.loads(args.judgments.read_text())), indent=2))
        return 0
    settings = Settings.from_env(os.environ)
    if args.command == "run" and (not args.live or settings.mode != "real"):
        parser.error(
            "Live evaluation requires --live and BEDROCK=real; prepare makes no model calls."
        )
    client = BedrockClient(settings) if args.command == "run" else None
    args.output.mkdir(parents=True, exist_ok=True)
    for case in cases:
        with fixture_context(case) as context:
            record = {"case_id": case["id"], "context": context.model_input()}
            if client:
                reviewer = InfrastructureReviewer(client, args.prompt.read_text())
                try:
                    record["result"] = reviewer.run(context).model_dump(mode="json")
                    record["stats"] = reviewer.last_outcome.stats
                except Exception as exc:
                    record["error"] = type(exc).__name__
            (args.output / f"{case['id']}.json").write_text(json.dumps(record, indent=2))
    print(
        json.dumps(
            {
                "cases": len(cases),
                "mode": args.command,
                "output": str(args.output),
                "accuracy_measured": False,
                "next": "Adjudicate live findings, then use score with --judgments.",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
