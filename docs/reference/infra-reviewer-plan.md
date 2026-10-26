# Standalone infra reviewer proposal

> Superseded proposal, retained for rationale. Current implementation: [infrastructureagent.md](../../infrastructureagent.md).

Proposed on October 12, 2026. This is a design draft, not an implemented reviewer.

Reference: [bandwidth-reviewer-demo](https://github.com/rishyendra333/bandwidth-reviewer-demo/tree/d9bf120a314d10e647288a7f50e30e99e40ebe9c), especially its security and test reviewers. Keep this reviewer independent of the general platform for the first version.

The reviewer's purpose is to identify changes that increase infrastructure consumption anywhere in the affected execution path: application code, SQL, background jobs, service integrations, observability, and deployment definitions. Terraform estimates are one evidence source within this broader cost review.

The [system prompt](../../.github/infra-reviewer/prompt.md) covers each cost driver separately. Historical [implementation notes](infra-implementation-notes.md) record the original context and evaluation proposal.

## Entry point and structure

A developer comments `@bandwidth-infra-reviewer review` on a PR. A GitHub Actions workflow runs Python from the trusted default branch, retrieves PR data, reviews relevant changes with Bedrock, and posts one infra review. The same script supports a local dry run.

```text
.github/
  workflows/
    infra-review.yml
  infra-reviewer/
    review.py
    prompt.md
    requirements.txt
    README.md
    fixtures/
```

Keep the first implementation small. Within `review.py`, separate context collection, candidate detection, model analysis, validation, and posting into functions. An internal `run_review(context) -> result` makes local evaluation possible without GitHub writes; it does not require importing or changing the general platform.

The workflow provides `GITHUB_TOKEN`, a Bedrock model and region, and optionally a cost-analysis input. Prefer AWS OIDC for model access. The trigger has its own name so it can coexist with the demo's other reviewers.

## General cost review scope

Inspect relevant source, SQL, job/scheduler, observability, and infrastructure/configuration diffs across languages. File extensions and SDK names help prioritize context; they must not restrict review to Terraform, Python, AWS, or explicit database calls. Follow local wrappers and helpers to identify the resource operation that a changed caller triggers. Dedicated parsers and pricing adapters have narrower support than the overall model review; disclose that coverage separately.

For each candidate, trace `entry point → repetition/fan-out → resource operation → consumption driver`. Compare the behavior before and after the PR, looking at invocation frequency, requests per invocation, work or bytes per request, retained data, and provisioned capacity. These drivers explain potential cost changes without requiring a dollar estimate.

| Rule | Candidate evidence | What needs confirmation |
| --- | --- | --- |
| Monthly cost increase | Comparable Infracost estimates at the PR baseline and head | Which changed resource explains the increase; whether it exceeds the configured threshold |
| Request amplification | Loops, nested fan-out, batching removal, or a helper called once per item | Whether external/database operations increase; whether bounds, batching, or caching limit them |
| More expensive data access | A scan replacing a keyed query, broader SQL, or repeated reads | What is actually read or processed; whether indexes, query plans, or table-size assumptions are known |
| Increased job frequency | A schedule, polling interval, event subscription, or trigger change | How often work runs, whether it overlaps, and what each invocation performs |
| Data movement | Repeated downloads, larger payloads, or changed service/region paths | Bytes transferred and destination; whether the route and billing basis are evidenced |
| Logging and telemetry growth | Full-payload logging, logging inside loops, or dynamic metric labels | Event volume, payload size, cardinality, and the configured telemetry backend |
| Storage accumulation | Larger stored objects, duplicated writes, longer retention, or removed cleanup | Whether persisted data grows and whether a lifecycle/cleanup policy already handles it |
| Retry amplification | Retries added at multiple layers, higher retry limits, or queue redelivery changes | Combined attempt bounds, idempotency, and whether work is actually duplicated |
| More compute or capacity | Expensive work moved into a hot path, cache removal, or changed replica/concurrency/resource settings | Execution frequency, workload bounds, and any measured utilization or duration |

Use deterministic helpers where available, such as Python AST, Terraform resource parsing, and schedule/config comparisons. They nominate candidates and prioritize context; they are not a hard gate preventing the model from identifying another evidenced cost driver. Every model-proposed finding still needs a changed-code reference and a credible path to resource consumption. Tree-sitter can improve context selection across languages later.

Examples of the underlying billing mechanisms are documented in [DynamoDB scan guidance](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-query-scan.html), [S3 pricing](https://aws.amazon.com/s3/pricing/), and [CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/). Service pricing evidence establishes the billing dimension; it does not establish this repository's traffic or actual bill.

Every finding must be attributable to a changed block. Unchanged files are supporting evidence, not a source of unrelated comments. A costly operation's mere presence is insufficient. Treat a memory increase as a sizing change to investigate; it may reduce duration and total cost. Ignore ordinary local micro-optimizations unless the reviewer can connect them to meaningful resource consumption.

## Review flow

```text
command → permission check → pin base/head SHAs
        → collect annotated diff and supporting files
        → trace resource consumption and cost candidates
        → attach optional cost evidence
        → one Bedrock analysis call
        → validate, deduplicate, cap
        → dry-run output or one GitHub review
```

Collect relevant changed functions, resource blocks, queries, and configuration, plus bounded local callers/helpers, client setup, batching/caching code, and job definitions. Include both the before and after version where it clarifies a changed access pattern or configuration. A loop calling `send_customer()` needs the helper's implementation, not just the loop text. Track the actual ranges supplied to the model and list omitted context in the result. If the relevant helper is unavailable, preserve that uncertainty rather than asserting an external call or a precise multiplication factor.

Use one bounded model call for the demo, matching the reference's implementation. This differs from the tool-using agent loop proposed in `agent-setup.md`; introduce read-only tools later if evaluation demonstrates that fixed context misses important evidence. Invalid model JSON is a review failure, not a successful review with zero findings.

## Cost evidence

Price estimation is optional; resource-consumption analysis is the core review. The reviewer must still work when pricing credentials, supported resources, or usage inputs are unavailable.

Distinguish three evidence levels in the result:

1. **Code evidence:** identifies a resource operation and how this change increases its frequency, volume, retention, or capacity. This is enough for a useful finding when the execution path is clear.
2. **Relative consumption:** describes an evidenced relationship, such as one remote read per batch becoming one per item. State bounds and conditions; do not equate an operation-count increase with the same multiplication of the total cloud bill.
3. **Dollar estimate:** requires validated prices plus comparable configuration and explicit usage assumptions. Infracost supplies this for supported infrastructure changes. A future trusted pricing/workload adapter can supply it for application-driven usage changes.

An optional workload profile can supply requests per period, batch sizes, payload sizes, retention, and region. Read it from the trusted default branch, disclose its provenance and age, and label derived values as estimates. Its absence must not suppress a well-supported cost-driver finding.

- Compare complete affected Terraform projects at matching baseline/head snapshots, using the same variables, region, pricing basis, and usage assumptions. Pricing only changed files can omit module and variable context.
- Prefer static Terraform HCL analysis. Infracost documents direct parsing and does not require cloud credentials for estimates: [Infracost FAQ](https://www.infracost.io/docs/faq/).
- Accept estimates from a trusted upstream analysis job or a pinned Infracost adapter. Verify the installed CLI's output contract before choosing commands; current documentation differs from the older `breakdown`/`diff` examples in many integrations: [current setup](https://www.infracost.io/docs/), [GitHub Actions integration](https://www.infracost.io/docs/integrations/github_actions/).
- Keep the cost threshold at `$100/month` initially, matching the existing spec. Show baseline, proposed cost, delta, and assumptions. A threshold crossing is a cost alert, not proof that the change is a mistake.
- Preserve unsupported-resource and missing-usage information. An unavailable estimate must never become `$0`. Usage-based estimates must state the supplied usage assumptions.
- Put numeric dollar estimates into the review through deterministic rendering from a validated pricing/usage result. The model cannot supply or alter dollar figures. Without comparable pricing evidence, report the consumption mechanism and mark dollar estimation unavailable. The `$100/month` threshold applies only to comparable estimates, not to whether a code-driven concern can be reported.

Infracost sends pricing parameters to its service; it is an additional data destination beyond Bedrock. Keep that feature optional and document it in setup.

## Finding and output rules

Use the standalone `infra-review/v1` result contract defined in [the prompt's required output](../../.github/infra-reviewer/prompt.md#14-required-output). Each finding identifies its rule/category, file and optional line, severity/confidence, resource operation, consumption drivers, entry point/call path, before/after behavior, impact basis, line-referenced evidence, mitigations checked, assumptions, recommended action, and any verification or pricing evidence needed. The extra fields make the causal claim reviewable rather than leaving it buried in prose. Return per-category coverage separately. Use `line: null` for a summary finding, including deleted resources; do not force it onto an unrelated added line. Dollar results live in a separate validated structure with their tool provenance and usage assumptions.

Keep qualitative findings only at confidence `>= 0.85`, with concrete code evidence. The score alone is insufficient: require a resource-consuming operation, an evidenced change to its consumption, and a useful action. Describe the verified mechanism directly and state workload uncertainty separately. Example: “This moves the same object download inside the recipient loop, repeating the remote read for each recipient. Fetch it once before the loop. The monthly impact depends on recipient volume and object size.” A finding whose only substance is “this might cost more” should be dropped.

For tool-backed cost alerts, use deterministic threshold checks and provenance rather than a model confidence score. Post valid findings on new-file diff lines, keep unplaceable findings in the summary, deduplicate by root cause, and cap inline comments at 10. Report files and checks skipped, estimate availability, reviewed SHA, model, duration, and tokens.

## Workflow behavior

Reuse the demo's comment trigger, reactions, local dry-run option, and inline review presentation. Improve four details while keeping the implementation standalone:

1. Parse the exact command on the first non-empty line, ignore bots, and check the commenter's actual repository permission through the API. The demo's `author_association` check does not establish write permission.
2. Retrieve diff and source from pinned revisions. The demo records the head SHA but subsequently reads the live PR files endpoint; a new push can mix revisions. Re-check the head before posting and identify stale results explicitly.
3. Serialize runs per PR with workflow concurrency, then use a hidden review marker keyed by head SHA and reviewer version to suppress completed duplicates. Provide `--full` for an intentional rerun.
4. Run only trusted reviewer code. Read PR files as data; do not run PR dependency installation, Terraform plans, CDK synthesis, or application code with the reviewer's credentials.

The initial runtime is GitHub Actions. GitHub-hosted runners process the source there, so the original spec's requirement that source remain inside Bandwidth's AWS account needs an AWS-hosted runner if it is retained for a pilot. Bedrock remains the model backend.

## Delivery sequence

1. Build the script and local dry-run path for general cost-driver review across source, queries, jobs, and infrastructure changes, with annotated diffs, bounded supporting context, structured causal evidence, and explicit context gaps. Add Python/Terraform helpers to improve evidence where available.
2. Add the standalone workflow, actual permission check, duplicate suppression, and inline posting.
3. Add comparable Infracost evidence and deterministic cost rendering as an optional feature.
4. Evaluate general review separately from parser/pricing support using realistic positive and negative fixtures across languages and cost-driver categories. Expand dedicated helpers based on missed cases.

Useful fixtures: a scan introduced inside a loop; a costly operation hidden behind a local wrapper; a batched or cached call that should produce no finding; a higher-frequency scheduled job; full-payload logging introduced in a hot path; removed cleanup already covered by a lifecycle policy; retries at two layers; a larger instance that reduces runtime; a Terraform capacity increase with a comparable cost artifact; missing usage inputs; a deleted resource; malformed model output; and a commit pushed during review. Include at least one non-Python application fixture and a change with no infrastructure files touched. Measure precision separately for dollar alerts and code-driven consumption findings.

General orchestrator integration, shared knowledge storage, webhooks, queues, Lambda deployment, and Step Functions remain later work.
