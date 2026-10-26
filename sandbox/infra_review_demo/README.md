# Static live-review acceptance fixtures

These files are source data for InfrastructureAgent acceptance reviews. They are never imported or executed by the reviewer or its test suite. Do not run them against AWS.

The baseline reads an invariant attachment once for each notification batch. The regression PR moves the uncached S3 read into the recipient loop. The control PR moves a cached helper call into that loop while retaining its lru_cache mitigation.

Both acceptance PRs target the dedicated sandbox baseline branch. The reviewer workflow and dependencies still come from trusted main. Do not merge the seeded regression into main.
