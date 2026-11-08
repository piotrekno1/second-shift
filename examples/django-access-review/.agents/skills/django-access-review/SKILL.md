---
name: django-access-review
description: Review Django views, API endpoints, and ORM queries for access control gaps, such as one user or tenant reaching another's data.
---

# Django access review

Read [Sentry's Django Access Control & IDOR Review](references/sentry-django-access-review.md)
next to this skill and use it as the review method. The bundled reference
comes from [Sentry's upstream skill at a pinned revision](https://github.com/getsentry/skills/blob/e94f22214528d69899d09c627b3315ddc3544816/skills/django-access-review/SKILL.md)
and is licensed under [Apache 2.0](references/LICENSE).

Follow the repository's contributor guidance and any scope in the request.
Review the repository without changing source files or service state. Do not
probe deployed services. Use existing tests, or tests run against isolated test
data, when they can confirm a suspected gap. Report which checks were
unavailable.

Report only gaps you traced through the code. For each finding, give the
location, the request that reaches it, the missing check, the impact, and a fix
that enforces the check in code. Put gaps you could not confirm under manual
verification. List the endpoints and flows you did not review.

If the prompt holds previous reports from this job, start with the code that
changed since the last report: new endpoints, changed access helpers, and new
models or relations. Mark each finding **new** or **still open** (with the
earlier report's date), and list in one line the findings from the last report
that are now fixed. Check every finding against this commit. Never copy one
from an earlier report. Without previous reports, review the whole repository
and do not label findings.

If you find no gaps, say so in one short paragraph and summarize how access is
enforced. Never invent problems. Return the review as your final report.
