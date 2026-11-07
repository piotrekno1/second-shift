# Django performance review

Review a Django app for database performance problems. The skill traces ORM
queries through their callers, checks existing optimizations, and reports
findings with evidence and suggested fixes.

Copy this example's `.agents/skills/django-perf-review` directory into your
repository's `.agents/skills` and merge the sample `.secondshift.yaml` into
your root manifest. Commit the files, import or resync the repository in
SecondShift, and choose **Run now**. SecondShift also declares this job in its
own root manifest.

The job uses repository source and any offline test tools already installed in
the sandbox. It needs an agent or model connection to run. The skill reports
the limits of its evidence when it cannot measure a query or verify production
impact.

The review method is by Sentry. This example includes an unmodified copy of
[Sentry's Django performance review skill](https://github.com/getsentry/skills/blob/3482d8dd35315adf34607f1c12bf793e14890bfe/skills/django-perf-review/SKILL.md)
and its [Apache 2.0 license and copyright notice](.agents/skills/django-perf-review/references/LICENSE).
