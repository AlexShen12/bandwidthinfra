# Running the standalone infra prompt

> Historical design notes, consolidated into [infrastructureagent.md](../../infrastructureagent.md). The current prompt is [here](../../.github/infra-reviewer/prompt.md).

`prompt.md` is the proposed system prompt. The reviewer runtime and workflow are not implemented yet. Keep the prompt separate from PR data and version it with the reviewer.

## What the runtime must supply

Use a structured user message with these sections, serialized rather than interpolating PR text into system instructions:

```text
review_metadata: repo, PR number, base SHA, head SHA, reviewer version
review_policy: confidence minimum, finding cap, cost threshold, ignored paths
diffs: file status, annotated base/head lines, commentable head lines
file_context: snapshot, path, actual start/end lines, content
candidate_hints: parser results or structural changes, with provenance
workload_evidence: optional facts, units, environment, source, timestamp
pricing_evidence: optional validated artifacts keyed by ID
context_limits: omitted files/ranges, unresolved references, available tools
```

Read review policy and workload inputs from the trusted default branch or an operator-controlled input. Code from both PR snapshots remains untrusted data even if it contains config, instructions, or billing claims. Mark its provenance separately from trusted policy.

The before snapshot must match the selected diff's comparison baseline. For a full PR using a three-dot diff, use the pinned merge-base snapshot; record the PR target-base SHA separately. Label this comparison baseline `base` in model evidence. Fetch the pinned head separately and verify every excerpt against its recorded snapshot.

Do not assume the same budget is suitable for all models. Select a model explicitly, measure the prompt and input together, and reserve output tokens for findings and coverage. Truncate at useful code boundaries and retain the exact range metadata; do not silently pretend a partial file is complete.

## Context collection by category

The diff identifies where to start; these supporting files determine whether a concern is real. Lightweight parsers and search supply context, not verdicts.

| Category | Practical context selection | Useful deterministic helper |
| --- | --- | --- |
| Requests | Changed function, called wrapper, client creation, callers, batching/cache code | Calls within loops; removed batch operations; changed cache usage |
| Database | Query construction and execution, models/schema, migrations/indexes, pagination | Changed predicates/projections/limits; ORM execution points; scan/query changes |
| Compute | Callers, job/request path, input limits, model request construction, cache | Loop nesting; repeated invariant calls; inference call and input-limit changes |
| Jobs | Schedule/trigger config, handler, checkpoint, lock, concurrency settings | Parse supported schedule formats; compare intervals and trigger filters |
| Retries | App retry wrapper, SDK setup, workflow/queue settings, partial-batch handling | Compare retry bounds and timeout settings; label attempts versus retries |
| Transfer | Producer/consumer, object fetch, payload construction, regions/routes | Repeated object reads; removed field selection/compression; route/config changes |
| Storage | Write path, object key/ID construction, lifecycle/TTL, cleanup/failure paths | Retention changes; append versus overwrite; added copies/versioning |
| Telemetry | Emission site, logger/exporter config, deployment levels, sampling/retention | Payload logging; dimensions/labels; effective collection setting changes |
| Capacity | Full resource/module, variable values, environment overrides, pricing artifacts | Compare normalized resource properties and minimum/maximum capacity |

Prioritize changed functions/resources and include a bounded set of relevant callers and helpers from both snapshots. Shared client settings, lifecycle policies, and exporter settings often disprove an apparent issue. Search references at the pinned revision and include exact paths/lines. Track unresolved dynamic calls and do not claim the search is complete.

A basic implementation can provide all context up front and make one Bedrock analysis call, matching the demo. If missing helper context dominates failed reviews, use the same prompt with a bounded read-only tool loop. Tools can expose `read_file(snapshot, path, start_line, end_line)`, `grep(snapshot, pattern, glob)`, `list_dir(snapshot, path)`, and `get_diff(path)`. Resolve paths inside the snapshot, prevent symlink escape, cap results, and label truncated output. No tool may execute repository code or post to GitHub.

## Pricing evidence stays outside the model

Normalize pricing artifacts into records with a stable ID, resource identity, baseline/head values, region, usage assumptions, availability, provenance/version, and unsupported-resource information. Validate compatible units and comparison assumptions before marking a record usable. Never substitute zeros for unavailable values.

The model references evidence IDs and explains the affected resource. Application code renders currency and applies the configured threshold. For future application-driven estimates, compute quantities from explicitly supplied workload facts and validated formulas; the model cannot invent traffic or a price. Keep usage assumptions and source timestamps visible.

## Validation and posting

Before posting, ordinary code must validate:

1. Strict JSON structure, allowed enum values, all required fields, finite confidence within `0..1`, and a findings array within the configured cap.
2. Exact pinned head SHA; category/rule pairing; exactly one coverage entry per category; evidence paths and ranges present in supplied context or recorded tool reads.
3. At least one evidence reference connecting the finding to a changed block. A deleted-line finding can remain summary-only. Existing code is supporting context.
4. Inline line membership in the head-side diff map. Convert an otherwise valid unplaceable finding into a summary item rather than inventing a location.
5. Cost evidence IDs exist and were validated. Currency comes exclusively from deterministic rendering; reject model-generated monetary claims.
6. Minimum confidence plus the required causal fields, followed by duplicate removal, sanitization, and the comment cap.

Structural validation does not prove the semantic claim. Log the evidence and evaluated findings so a person or an evaluation harness can audit false positives. If JSON is malformed, allow one bounded repair using the validation errors. If repair fails, report a failed review rather than “no issues.”

Use the demo's script/workflow boundary: model analysis returns findings; Python owns GitHub writes. Re-check the PR head, use a reviewer-version/head-SHA marker for completed-run deduplication, and disclose stale results. Keep findings and analysis logs distinct from posted text. Avoid logging raw secrets or full unnecessary file contents.

## Evaluation cases that matter

Pair each positive fixture with an example that superficially looks expensive but is controlled:

| Positive case | Negative or uncertain case |
| --- | --- |
| Remote read moved inside recipient loop | Helper reads from a request-scoped cache |
| Scan replaces keyed lookup and drains all pages | Bounded maintenance scan is unchanged |
| Inference called per item instead of per batch | A new call replaces equivalent existing work |
| Full-history job scheduled more frequently | Incremental checkpoint keeps total processed data stable |
| Nested retries replay successful paid operations | Partial-batch acknowledgment and idempotency avoid the replay |
| Same object downloaded repeatedly | Transfer path/region is unknown; no egress-price claim |
| Removed deletion leaves durable artifacts | Existing lifecycle policy still removes them |
| Unique request IDs become metric dimensions | Request ID is only an unindexed log field |
| Minimum replicas increase with validated estimate | Only autoscaling maximum rises; no claimed guaranteed bill increase |

Judge whether each finding identifies the right change, traces the real resource operation, survives the mitigating context, states uncertainty correctly, and offers a useful action. Measure results by category and language. Do not judge success by comment count or model confidence alone.

## Technical references

These sources informed guardrails in the prompt; they are not blanket proof of this repository's costs. The runtime must supply any provider-specific facts it expects the model to rely on.

- [DynamoDB query and scan guidance](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-query-scan.html): read work, filtering, pagination, and maintenance tradeoffs.
- [Lambda best practices](https://docs.aws.amazon.com/lambda/latest/dg/best-practices.html): memory/runtime tradeoffs, batching, duplicate delivery, and partial-batch handling.
- [Timeouts, retries, and backoff with jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/): retries across layers and failure-case amplification.
- [CloudWatch cost analysis](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_billing.html): logs, metrics, storage, queries, and distinct billing dimensions.
- [S3 pricing](https://aws.amazon.com/s3/pricing/): storage, requests, transfer, and configuration-dependent costs.
- [Infracost FAQ](https://www.infracost.io/docs/faq/): direct IaC parsing, supported estimates, and pricing API data handling.
