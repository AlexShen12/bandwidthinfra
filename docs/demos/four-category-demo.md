# Four-category InfrastructureAgent demo

Demo PR: [#6 — notification delivery and reporting updates](https://github.com/AlexShen12/bandwidthinfra/pull/6).

The PR compares `sandbox/four-category-regressions` with `sandbox/four-category-baseline`. Both contain a standalone application snapshot. The deployed reviewer and its workflow remain on trusted `main`. Do not execute the fixture application, install its dependencies, apply its Terraform, or merge these demo branches into main.

The PR description is neutral. This answer key is outside the reviewed snapshots and is not supplied to the model.

## Four intended cost findings

| Category | Changed file | Before | After | Useful action |
| --- | --- | --- | --- | --- |
| Requests | `app/asset_manifest.py` | One S3 `head_object` call for the shared asset | The same metadata call inside the recipient loop; N calls for N recipients | Fetch the invariant metadata once before the loop |
| Database | `app/invoices.py` | Parameterized account/date predicate and `LIMIT 100` in PostgreSQL | Fetch and sort all invoices, then filter and slice in Python | Restore filtering and the limit in SQL; the supporting schema includes the account/date index |
| Storage | `infra/retention.tf` | Export objects expire after 30 days | Expiration moves to 3,650 days; the writer creates fresh keys under `exports/` | Confirm the retention requirement; restore or justify the longer lifecycle |
| Telemetry | `app/delivery_metrics.py` | A metric with service/status dimensions | Add a per-notification UUID dimension, producing separate custom metric identities | Keep metric dimensions bounded; place individual IDs in appropriately configured logs or traces |

These are four separate root causes. Do not count request/transfer consequences of the same operation as extra independent bugs. Do not infer dollar amounts, production traffic, or a measured bill multiplier.

Background references: [CloudWatch metric identities and custom metrics](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/cloudwatch_concepts.html), [S3 lifecycle expiration](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-expire-general-considerations.html), [Psycopg query execution](https://www.psycopg.org/psycopg3/docs/basic/usage.html).

## Three real defects the infra reviewer should leave alone

| Specialist | File | Seeded defect | Why it is outside this review |
| --- | --- | --- | --- |
| Security | `app/access.py` | Removes `user_id == owner_id`; any signed-in user now passes the ownership predicate | Broken authorization, with no supported resource-consumption mechanism in this change |
| Security | `app/preview.py` | Removes HTML escaping while returning user input in a `text/html` response | HTML injection/XSS; no supported infrastructure cost change |
| Correctness | `app/billing.py` | Multiplies by `quantity - 1`, so one item totals zero | Incorrect business arithmetic; not cloud or infrastructure consumption |

These defects are intentional review data. The infrastructure agent should not relabel them as speculative compute/request costs. Other specialists in a later combined platform would own them.

## Presenting the demo

1. Open the PR's **Files changed** view. Show that ordinary Python, SQL context, and a lifecycle change are reviewed together.
2. Show the four cost findings and their concrete actions. Explain that the agent identifies resource-consumption changes; trusted pricing data would be needed for dollar estimates.
3. Show the authorization, escaping, and arithmetic changes. Explain why they remain for security/correctness reviewers.
4. Show the coverage list, reviewed commit, model, elapsed time, and token footer.
5. Repeating `@bandwidth-infra-reviewer review` should return an existing-review link. For an intentional paid rerun, use `@bandwidth-infra-reviewer review --full` with a fresh Bedrock key.

Coverage is not the finding count. Four intended finding categories do not require exactly four `reviewed` flags: transfer or compute can be considered as consequences of a database change while still belonging to the same root cause.

## Context recorded before the live review

- Baseline: `50ee39f08edd781fd9118990fb8ce9cb7f350719`.
- Reviewed head: `87c5f7e09124c4b0f9558608c9383afae5103dca`.
- Seven changed files; nine source/context files supplied.
- 3,963 annotated diff characters and 6,638 supporting-context characters.
- No omissions. No fixture source or infrastructure commands were executed.

## Observed live result

[App review on PR #6](https://github.com/AlexShen12/bandwidthinfra/pull/6#pullrequestreview-5647612486), reviewer `0.1.1`, found four issues and posted four inline comments:

| Category | Expected | Found | Missed | Actual inline review |
| --- | ---: | ---: | ---: | --- |
| Requests | 1 | 1 | 0 | [S3 metadata in the recipient loop](https://github.com/AlexShen12/bandwidthinfra/pull/6#discussion_r4374098548) |
| Database | 1 | 1 | 0 | [SQL predicate and limit removed](https://github.com/AlexShen12/bandwidthinfra/pull/6#discussion_r4374098542) |
| Storage | 1 | 1 | 0 | [Longer export lifecycle](https://github.com/AlexShen12/bandwidthinfra/pull/6#discussion_r4374098558) |
| Telemetry | 1 | 1 | 0 | [Per-notification metric dimension](https://github.com/AlexShen12/bandwidthinfra/pull/6#discussion_r4374098555) |

None of the three out-of-scope defects received an infra finding. Coverage was `reviewed` for requests, database, storage, and telemetry, and `not_applicable` for the other five categories. The App check completed neutrally and the request received its completion reaction.

The run used one Bedrock analysis call, no repair, 9,475 input tokens, 3,408 output tokens, and 17.9 seconds of model time. All posted comments are pinned to the head SHA above. No actual fixture application, PostgreSQL query, S3 operation, or Terraform deployment was executed.

Presentation nuance: the database comment uses “full table scan,” but no `EXPLAIN` plan was supplied. The source proves an unbounded all-row read/filter in application code, not a particular PostgreSQL execution plan. The account/date index is already present; restoring the SQL predicate and limit is the primary action. Likewise, explain the telemetry concern as custom metric-series cardinality, not a measured storage-byte bill.

These counts are agent-inspected results for one deliberately seeded demo, not a held-out quality benchmark or human pilot approval.
