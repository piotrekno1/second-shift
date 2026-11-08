# Django access review

Review a Django app for access control gaps. The skill learns how the app
authorizes requests, traces each endpoint from the ID it receives to the query
that loads data, and reports places where one user or tenant can reach
another's data.

Copy this example's `.agents/skills/django-access-review` directory into your
repository's `.agents/skills` and merge the sample `.secondshift.yaml` into
your root manifest. Commit the files, import or resync the repository in
SecondShift, and choose **Run now**. SecondShift also declares this job in its
own root manifest.

The job uses repository source and any offline test tools already installed in
the sandbox. It needs an agent or model connection to run. It runs on the 15th
of each month at 08:00 UTC once you enable it. The job sets `history: 1`, so
each run reads the previous report, starts with code that changed since then,
and marks findings as new, still open, or fixed.

The review method is by Sentry. This example includes an unmodified copy of
[Sentry's Django access review skill](https://github.com/getsentry/skills/blob/e94f22214528d69899d09c627b3315ddc3544816/skills/django-access-review/SKILL.md)
and its [Apache 2.0 license and copyright notice](.agents/skills/django-access-review/references/LICENSE).
