---
name: django-perf-review
description: Review Django code for database performance problems such as N+1 queries, unbounded querysets, costly query plans, and per-row writes.
---

# Django performance review

Read [Sentry's Django Performance Review](references/sentry-django-perf-review.md)
next to this skill and use it as the review method. The bundled reference
comes from [Sentry's upstream skill at a pinned revision](https://github.com/getsentry/skills/blob/3482d8dd35315adf34607f1c12bf793e14890bfe/skills/django-perf-review/SKILL.md)
and is licensed under [Apache 2.0](references/LICENSE).

Follow the repository's contributor guidance and any scope in the request.
Review the repository without changing source files or service state. For
checks, use installed tools and isolated test data. Use existing focused tests,
query counts, or query plans when they can verify a suspected issue. Keep
validation offline and report which checks were unavailable.

Distinguish evidence from the code, measurements on test data, and any supplied
production evidence. A reproducible result on test data can support a finding
when the workload is stated. Production volume, request frequency, and latency
require production evidence. Leave suspicions without enough evidence out of
the findings and describe relevant validation gaps in the coverage summary.

For each finding, give the location, the path that triggers it, the evidence,
and a suggested fix. Match severity to the measured or demonstrated impact.
Return the review as your final report.
