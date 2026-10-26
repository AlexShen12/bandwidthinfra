# InfrastructureAgent

Canonical implementation and future-work document. Updated October 26, 2026. The general-platform drafts and original proposals are preserved in [docs/reference](docs/reference). The current [system prompt](.github/infra-reviewer/prompt.md) evaluates nine categories in one analysis call.

## Architecture and integration

```text
PR comment → GitHub Actions on trusted default-branch code
           → scoped GitHub App installation token
           → exact command and repository permission check
           → pinned merge-base/head context → Bedrock Converse
           → strict result + evidence validation → one App-authored GitHub review
```

The Actions workflow owns automation; installing the App alone does not start reviews. Each reviewed repository needs the workflow, trusted reviewer package, and setup variables/secrets. Source is processed on the GitHub-hosted Ubuntu runner and sent to the configured Bedrock endpoint. An AWS-only source-processing requirement would need an AWS-hosted runner in a later rollout.

The primary repository is `AlexShen12/bandwidthinfra`. The CLI wrapper stays under `.github/infra-reviewer`; reusable code lives in `src/reviewer/infra`. `InfrastructureReviewer.run(PreparedContext)` receives a prepared context and an injected model client and returns `InfraReviewResult`. It never posts to GitHub. `InfraFinding.to_common()` adapts to the shared `Finding` type; its line can be null for summary-only findings.

## Implemented behavior

- Exact first non-empty line commands: `@bandwidth-infra-reviewer review`, `review --full`, and `help`. Bots and users without write/maintain/admin permission do not reach Bedrock. No auto-review on every push.
- Full-PR review at each new head. Normal duplicate requests are suppressed using App-authored head/version review markers. `--full` uses a request-specific marker so replaying that request remains idempotent.
- Temporary Git objects are fetched with an HTTP authorization header, not a credential-bearing remote URL. No PR checkout, dependencies, infrastructure commands, external diff drivers, or application execution. Symlinks/submodules/binaries are excluded; snapshots are removed after collection.
- Diffs and supporting excerpts retain actual snapshot/path/line metadata. Python AST and bounded text selection supply caller/helper/config context. These are hints; no file-extension or SDK-only gate restricts the nine-category model review.
- Defaults: 45,000 diff characters; 25,000 supporting-context characters; 12,000 per supporting file; eight related files; 150 diff files; 5,000 changed lines; ten findings; minimum confidence 0.85; 8,000 output tokens; 15-minute workflow timeout. Every omission is recorded. Context-limited coverage is disclosed.
- One analysis call plus at most one complete JSON regeneration after a validation failure. A second failure is an error, not a clean review. Reasoning blocks are excluded from displayed output.
- Strict `infra-review/v1` validation: nine unique coverage categories, finite confidence, matching category/rule IDs, supplied evidence ranges, changed-file attribution, and validated pricing IDs. Unplaceable inline findings become summary findings. Duplicate roots and low-confidence findings are filtered.
- The App creates a progress check and reactions, pins reviews to `commit_id`, and posts `[infra · severity]` findings. If the PR head changes before posting, the run ends neutrally and requests a rerun. A network failure after a POST triggers marker reconciliation instead of an immediate blind repost; line-placement rejection can fall back to summary output.
- App-owned analysis failures are neutral. The native Actions job can still fail and must not be configured as a required merge check. A later cancellation/runner outage may prevent final reaction cleanup; the reviewed SHA and Actions logs remain authoritative.
- Final structured results and omissions are written to ignored `artifacts/` on the runner. They contain no full snapshots or raw model reasoning. They are not automatically uploaded or persisted to S3.

## Cost-driver sections and context

| Category | Context used to assess a finding |
| --- | --- |
| Requests | Loop/caller, remote helper, batching, client setup, and caching |
| Database | Query execution, predicates, pagination, schemas/indexes and migrations |
| Compute/inference | Repeated work, model calls, input sizes, callers and reuse |
| Jobs | Schedules, triggers, handler work, checkpoints and overlap controls |
| Retries | Application/SDK/queue settings, attempt semantics and partial failures |
| Transfer | Producer/consumer, payload construction, object reads and evidenced routing |
| Storage | Write keys, duplication, lifecycle/TTL, retention and cleanup |
| Telemetry | Effective log/export settings, payloads, sampling and metric dimensions |
| Capacity | Complete resource settings, variables, minimums/ceilings and pricing evidence |

A finding must connect a changed block to a resource operation, consumption effect, controls checked, remaining uncertainty, and useful action. The model does not infer production traffic or automatically equate a resource-count increase with an equal bill increase.

## Bedrock configuration

Local mode defaults to `BEDROCK=fake`; simulated output cannot be posted or scored as model quality. The workflow explicitly uses `BEDROCK=real`. Default region/model match the prototype: `us-west-2` and `openai.gpt-oss-120b-1:0`.

`BEDROCK_AUTH_MODE=api-key` uses only the bearer authentication path and requires `AWS_BEARER_TOKEN_BEDROCK`. Short-term keys last no longer than the generating session or 12 hours; replace the repository secret when it expires. The script distinguishes credential expiration, access denial, throttling, request configuration, and transport errors without logging token values.

`BEDROCK_AUTH_MODE=iam` uses SigV4 and the SDK credential chain. The runtime removes the bearer token from its environment, and the workflow obtains a role session through OIDC. An OIDC setup failure is reported before inference instead of falling back to an unrelated credential source. Model calls have a 180-second read timeout and at most two SDK attempts per call.

The optional Terraform root `infra/actions-oidc` has separate S3 state, an approved-account constraint, exact OIDC audience/subject trust, and permission to invoke the configured foundation model only. It does not manage the older ingress deployment. Inference profiles or cross-region models would require an explicit reviewed policy extension.

Read-only inventory:

```sh
make oidc-inventory
```

Ask the approved account administrator to confirm the account and inspect existing providers. Reference an existing GitHub provider by ARN or explicitly create one; do not create a duplicate.

Before using IAM mode, have the repository administrator inspect its existing OIDC subject customization and configure `repo`, `ref`, and `workflow_ref` using the prepared [subject template](.github/infra-reviewer/oidc-subject.json). This repository-wide setting can affect existing OIDC workflows, so coordinate their trust policies before changing it. Obtain the **actual** resulting subject, including immutable owner/repository IDs when enabled, and supply that exact string to Terraform. A branch-only subject would allow other workflows on that branch; this Terraform root requires the specific `infra-review.yml` workflow in the subject. GitHub documents [custom subjects and workflow claims](https://docs.github.com/en/actions/reference/security/oidc). No subject customization has been applied by this implementation.

```sh
terraform -chdir=infra/actions-oidc init -backend-config=/path/to/approved-backend.hcl
terraform -chdir=infra/actions-oidc plan -var-file=/path/to/approved.tfvars
```

Use the role output as `BEDROCK_ROLE_ARN`, configure `HOSTING_ACCOUNT_ID`, then select `iam` in repository variables. No AWS infrastructure has to be created for API-key mode.

## App setup and rollout

1. Register the dev App using the settings in [.github/infra-reviewer/app-manifest.json](.github/infra-reviewer/app-manifest.json): Contents/Metadata read; Pull requests/Issues/Checks write; no subscribed events; webhook inactive. The registration helper below supports the manifest flow and stores the generated key without printing it.
2. Install on **selected repositories**, first `AlexShen12/bandwidthinfra`.
3. Set repository variable `INFRA_APP_ID` and secret `INFRA_APP_PRIVATE_KEY`. The helper can set these with `gh`; the workflow creates and revokes the installation token per run.
4. Confirm the approved Bedrock account/region/model. Add a fresh `AWS_BEARER_TOKEN_BEDROCK` secret for the initial API-key mode, or obtain the approved OIDC role.
5. Merge the implementation so its workflow and reviewer code exist on the default branch. Open the seeded cost-regression PR and request a review. A workflow introduced only on the reviewed PR branch will not handle `issue_comment`.
6. Verify comment placement, skip/duplicate behavior, and a negative/control PR. Then ask the team demo's administrator to install the App and configure the same reviewed code/workflow.

The App setup helper uses a localhost callback and GitHub's manifest confirmation screen:

```sh
uv run --no-sync python scripts/register_app.py --repo AlexShen12/bandwidthinfra
```

No client secret or webhook service is needed by the reviewer. The key and local metadata are saved under ignored `.secrets/`. The real review and live fixture evaluation incur Bedrock usage; offline checks do not.

## CLI, policy, and pricing input

```sh
make setup
make check
export GITHUB_TOKEN="$(gh auth token)"
BEDROCK=fake make dry-run REPO=AlexShen12/bandwidthinfra PR=123
# Export real Bedrock credentials first for this mode:
BEDROCK=real uv run --no-sync python .github/infra-reviewer/review.py OWNER/REPO PR --dry-run
```

The CLI does not read `.env` automatically. For a local publication, supply an App installation token, `GITHUB_APP_SLUG`, `--actor USER`, `BEDROCK=real`, and `--post`. The default is a dry run.

Optional `.github/bandwidth-reviewer.yml` is loaded at a pinned trusted default-branch ref. Supported keys are `version: 1`, `reviewers.infra.{enabled,min_confidence,cost_threshold_usd_month}`, `max_comments`, `skip_label`, and `ignore_paths`. Invalid config reports a warning and uses defaults.

`--pricing-evidence PATH` accepts a trusted operator-provided JSON file on the local/default-branch checkout. It must have `schema_version: "infra-pricing/v1"` and `records` matching `PricingRecord` in the Python package. Each record identifies an ID, changed file/resource, exact comparison SHAs, USD baseline/head monthly amounts, region, assumptions, availability/comparability, unsupported resources, source/version, and timezone-aware generation timestamp. Unknown amounts remain null/unavailable. Input is limited to 200 KB and 100 unique records.

The model sees pricing metadata/IDs, not the amounts. Python renders estimates and flags comparable increases at or above the configured threshold (default 100 USD/month). V1 does not run Infracost or invent application-level price/traffic data. No trusted workload-profile input is implemented yet; claims of `workload_supported` impact are rejected.

## Verification and evaluation

Local verification on October 26, 2026:

- 129 offline tests passed; Ruff lint and formatting checks passed.
- Both workflows passed actionlint 1.7.12; optional Terraform configuration passed `terraform validate` without applying resources.
- All 18 evaluation contexts prepared successfully. A read-only fake-model dry run also completed against the prototype's PR 2. Neither run measures model accuracy, and neither invoked Bedrock or posted a review.
- Documentation links resolve. The dev App `bandwidth-infra-reviewer-dev` is registered (App ID `5474940`) and installed only on `AlexShen12/bandwidthinfra` (installation `175332427`). Its repository variable and signing-key secret are configured. A temporary installation token verified the repository scope, exact permissions, and commenter-permission lookup; the token was revoked immediately afterward. Webhook subscriptions are empty.
- Fresh Bedrock credentials, account/model approval, default-branch deployment, and live model acceptance remain pending. No paid inference has run.

`make check` runs lint and offline tests. The tests stub GitHub/Bedrock and create isolated local Git fixtures; they do not execute fixture source or call live AWS.

`eval/cases.json` contains one seeded positive and one negative/control example per category. It includes TypeScript, source-only changes, caching, checkpoints, partial-batch acknowledgment, lifecycle cleanup, and an autoscaling ceiling. Labels are hypotheses for human adjudication, not proof of model accuracy.

```sh
# Prepare all contexts without any model calls:
make eval-prepare
# Explicit live run after approved credentials are available:
BEDROCK=real uv run --no-sync python -m reviewer.infra.evaluate run --live
# Score only after inspecting actual live findings:
uv run --no-sync python -m reviewer.infra.evaluate score --judgments /path/to/judgments.json
```

A judgment is `{"case_id":"requests-positive","finding_index":0,"expected_id":"repeated-object-read"}`; use `expected_id: null` for a false positive. Every returned finding needs one judgment. Expected issues not matched by a human count as misses. Scoring rejects simulated/failed/missing runs and duplicate matches, and reports true positives, false positives, misses, precision, and recall separately for each category.

## Remaining rollout and future work

- Live acceptance requires the registered/installed App, repository secrets, default-branch workflow, and sponsor-confirmed model access. Offline checks alone do not establish live acceptance or review quality.
- Add automated Infracost execution only when an approved pricing integration is available; retain the existing supplied-evidence boundary.
- Add read-only model tools if observed context gaps justify moving beyond one prepared-context call.
- Add a trusted workload-profile input, broader parsers/reference indexing, larger datasets and held-out fixtures after live adjudication.
- Integrate with the general reviewer orchestrator through the prepared-context/result/common-finding boundary. Consider install-only webhooks/workers, production rollout, and persistent run storage when scaling warrants them.

References: [prototype PR reviews](https://github.com/rishyendra333/bandwidth-reviewer-demo/pull/2), [GitHub App tokens](https://github.com/actions/create-github-app-token), [AWS Bedrock keys](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html), [GitHub OIDC for AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws).
