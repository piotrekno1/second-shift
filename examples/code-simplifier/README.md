# Code simplifier

Review the code changed in the last week for simplifications that keep
behavior the same. The skill looks for repeated lookups, loose types, dense
one-liners, and comments the code has outgrown. It reports each suggestion
with its location and reason, and does not change files.

Copy this example's `.agents/skills/code-simplifier` directory into your
repository's `.agents/skills` and merge the sample `.secondshift.yaml` into
your root manifest. Commit the files, import or resync the repository in
SecondShift, and choose **Run now**. The sample runs every Friday. SecondShift
also declares this job in its own root manifest.

The job uses repository source and history. It needs an agent or model
connection to run. To review a different range, start it with **Run now** and
name the commits or files in the request.

The review method is by Sentry. This example includes an unmodified copy of
[Sentry's code simplifier skill](https://github.com/getsentry/skills/blob/e94f22214528d69899d09c627b3315ddc3544816/skills/code-simplifier/SKILL.md)
and its [Apache 2.0 license and copyright notice](.agents/skills/code-simplifier/references/LICENSE).
