# InfrastructureAgent

A standalone infrastructure cost reviewer using GitHub Actions, a GitHub App bot, and Amazon Bedrock. It reviews source code, queries, jobs, data handling, telemetry, and deployment configuration. It explains evidenced consumption changes; dollar estimates require separately supplied pricing evidence.

The canonical implementation and rollout document is [infrastructureagent.md](infrastructureagent.md). Older platform designs are archived under [docs/reference](docs/reference).

## Local development

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/), Git, and a GitHub token for reading the target repository.

```sh
make setup
make check
export GITHUB_TOKEN="$(gh auth token)"
BEDROCK=fake make dry-run REPO=AlexShen12/bandwidthinfra PR=123
```

Fake mode exercises context collection and rendering only; it cannot publish findings or measure model accuracy. Use `BEDROCK=real` and the configured Bedrock authentication mode for a real local dry run. The CLI does not implicitly load `.env`; export the variables you intend to use.

## GitHub setup

Install the dev GitHub App with Contents/Metadata read and Pull requests/Issues/Checks write. Webhooks are disabled. The workflow must be on the reviewed repository's default branch.

| Setting | Location | Purpose |
| --- | --- | --- |
| `INFRA_APP_ID` | Actions variable | Registered GitHub App ID |
| `INFRA_APP_PRIVATE_KEY` | Actions secret | App signing key; the workflow mints a scoped installation token |
| `AWS_BEARER_TOKEN_BEDROCK` | Actions secret | Fresh short-term key for `api-key` mode |
| `BEDROCK_AUTH_MODE` | Actions variable | `api-key` (default) or `iam` |
| `BEDROCK_MODEL_ID` | Actions variable | Defaults to `openai.gpt-oss-120b-1:0` |
| `AWS_REGION` | Actions variable | Defaults to `us-west-2` |
| `BEDROCK_ROLE_ARN` | Actions variable | Required for `iam` mode |
| `HOSTING_ACCOUNT_ID` | Actions variable | Required account constraint for `iam` mode |
| `INFRA_TRIGGER` | Actions variable | Defaults to `@bandwidth-infra-reviewer` |

Comment `@bandwidth-infra-reviewer review` on an open PR. Only repository writers can start a review. `review --full` intentionally reruns a commit; `help` explains usage.

Setup, optional OIDC Terraform, fixtures, and human evaluation instructions are in [infrastructureagent.md](infrastructureagent.md).
