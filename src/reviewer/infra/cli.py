"""Command-line entry point used by Actions and local dry runs."""

import argparse
import json
import os
import sys
from pathlib import Path

from reviewer.infra.bedrock import BedrockClient, FakeModelClient, ModelError
from reviewer.infra.config import Settings
from reviewer.infra.github import GitHub
from reviewer.infra.pipeline import HELP, event_request, run_review
from reviewer.infra.safety import display


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Review infrastructure cost drivers with Amazon Bedrock."
    )
    parser.add_argument("repo", nargs="?")
    parser.add_argument("pr", nargs="?", type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true", help="Default: render locally without GitHub writes"
    )
    mode.add_argument(
        "--post",
        action="store_true",
        help="Publish using an installed App token; fake results are forbidden",
    )
    parser.add_argument(
        "--full", action="store_true", help="Intentionally review an already reviewed commit again"
    )
    parser.add_argument("--actor", help="Verify this user's write permission before a local --post")
    parser.add_argument("--event-path", type=Path, help="GitHub issue_comment event JSON")
    parser.add_argument(
        "--authorize-only",
        action="store_true",
        help="Authorize the event and write step outputs; never invoke Bedrock",
    )
    parser.add_argument("--pricing-evidence", type=Path)
    parser.add_argument("--prompt", type=Path, default=Path(".github/infra-reviewer/prompt.md"))
    parser.add_argument("--artifact-dir", type=Path, default=Path("artifacts"))
    args = parser.parse_args(argv)
    try:
        github = GitHub(
            os.environ.get("GITHUB_TOKEN", ""), app_slug=os.environ.get("GITHUB_APP_SLUG")
        )
        request = None
        if args.event_path:
            request = event_request(
                json.loads(args.event_path.read_text()),
                github,
                os.environ.get("INFRA_TRIGGER", "@bandwidth-infra-reviewer"),
            )
            if args.authorize_only:
                if output := os.environ.get("GITHUB_OUTPUT"):
                    with Path(output).open("a") as stream:
                        stream.write(
                            f"authorized={'true' if request else 'false'}\ncommand={request['command'] if request else 'none'}\n"
                        )
                print(
                    json.dumps(
                        {
                            "authorized": bool(request),
                            "command": request["command"] if request else None,
                        }
                    )
                )
                return 0
            if not request:
                print("No authorized review command; no model call was made.")
                return 0
            if request["command"] == "help":
                if args.post:
                    github.app_identity(request["repo"])
                    github.reply(
                        request["repo"],
                        request["pr"],
                        HELP.replace(
                            "@bandwidth-infra-reviewer",
                            os.environ.get("INFRA_TRIGGER", "@bandwidth-infra-reviewer"),
                        ),
                    )
                else:
                    print(HELP)
                return 0
        elif args.authorize_only:
            parser.error("--authorize-only requires --event-path")
        if not request and (not args.repo or args.pr is None):
            parser.error("Supply OWNER/REPO PR or --event-path.")
        settings = Settings.from_env(os.environ)

        def model_factory():
            if settings.mode == "fake":
                return FakeModelClient()
            if settings.auth_mode == "iam" and os.environ.get("OIDC_SETUP_OUTCOME") == "failure":
                raise ModelError(
                    "GitHub could not assume the Bedrock OIDC role. Check the role ARN, trust subject, and approved account; no model call was made."
                )
            return BedrockClient(settings)

        result = run_review(
            github,
            repo=request["repo"] if request else args.repo,
            pr=request["pr"] if request else args.pr,
            actor=request["actor"] if request else args.actor,
            comment_id=request["comment_id"] if request else None,
            full=request["full"] if request else args.full,
            force_id=os.environ.get("GITHUB_RUN_ID"),
            post=args.post,
            prompt_path=args.prompt,
            model_factory=model_factory,
            pricing_path=args.pricing_evidence,
            artifact_dir=args.artifact_dir,
            trusted_ref=os.environ.get("TRUSTED_CONFIG_REF"),
        )
        print(
            json.dumps(
                {
                    key: result.get(key)
                    for key in ("status", "run_id", "review_url", "stats", "error")
                    if key in result
                },
                indent=2,
            )
        )
        if not args.post and "summary" in result:
            print(result["summary"])
            for comment in result["comments"]:
                print(f"\n{display(comment['path'])}:{comment['line']}\n{comment['body']}")
        if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(summary_path).open("a") as stream:
                stream.write(
                    result.get(
                        "summary",
                        f"Infrastructure reviewer: {result['status']}\n{result.get('error', '')}",
                    )
                )
        return 1 if result.get("status") == "error" else 0
    except Exception as exc:
        print(
            f"Infrastructure reviewer setup failed ({type(exc).__name__}): {display(str(exc), 1500)}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
