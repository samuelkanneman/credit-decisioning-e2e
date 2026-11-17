# ADR-005: Rules engine and decision service

Status: accepted · 2026-11-17

## Context

The credit policy has to be readable by people who do not write Python, changed through review, tested rule by rule, and recorded with every decision. It also has to run a champion and a challenger side by side.

## Decision

**Policy as a GoRules decision graph.** `policy/credit-policy.json` is a JDM graph evaluated by the open-source ZEN engine (`zen-engine`): knock-outs, cut-offs and limits as decision tables with first-hit policy, and the approved amount as an expression. Each table passes the context through to the next one, and each row writes the identifier of the rule that fired.

**A manifest pins the graph.** `policy/manifest.json` carries the policy version and the SHA-256 of the graph. Loading checks the hash. Editing a rule without bumping the version fails the tests and the service start-up.

**The arm is an input to the graph**, not a second file. The challenger differs in one row of the cut-off table, so one graph holds both and the difference is visible in one place. The arm is assigned by hashing the application ID.

**A separate decision service** orchestrates: validate the application, call the scoring API, assign the arm, evaluate the graph, write the decision to PostgreSQL, then answer. The scoring API owns the input contract: the decision service forwards the application and passes its 422 back unchanged.

**The decision record is the audit trail.** It is written before the response. If the scoring API is unreachable or the record cannot be written, the service answers 503 and no decision exists. A repeated application ID returns the recorded decision instead of deciding again.

## Consequences

- Rules are data: a policy change is a diff of a JSON file, with a test per boundary and an impact table in CI.
- The impact numbers in `docs/policy.md` come from a vectorised copy of the policy in `credit_score.policy_analysis`. A test holds the graph and that copy together on 6,000 cases, and the running stack matched it on 3,000 real 2017 applications.
- ZEN evaluates a decision in about 0.35 ms. End to end, the decision service answered in 7.5 ms at the median and 9.4 ms at p95 on the same 3,000 applications, including the call to the scoring API and the database write.
- The arm is fixed per application ID, so a re-submitted application cannot shop between policies, and the challenger share converges to 10% (10.5% on the 3,000).
- The decision service image carries `zen-engine` but no pandas or training libraries (249 MB).

## Alternatives considered

| Option | Why not |
| --- | --- |
| Rules in Python inside the service | Fast to write, but every change is a code change and nobody outside engineering can read the policy |
| A commercial decision engine | Not reproducible from a public repository; the graph format carries over to such engines conceptually |
| Two graph files, champion and challenger | Duplicates every rule that is the same in both, which is all but one |
| Score and decide in one service | Mixes two release cycles: the model changes with retraining, the policy with business decisions |
