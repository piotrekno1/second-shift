# Production end-to-end test

Tests the [single-server Compose deployment](../../docs/production-deployment.md)
on a disposable host with gVisor, without real GitHub, model or S3 accounts:

```bash
scripts/production_e2e/run.sh            # deploy the last release, upgrade to this tree
scripts/production_e2e/run.sh --keep     # keep the test host afterwards
scripts/production_e2e/run.sh --from ghcr.io/piotrekno1/second-shift:0.1.0b2
```

It needs Docker that can run privileged containers, and network access to pull
images and gVisor. A run takes about five minutes and ends with `Passed.`

## What it checks

1. A fresh deployment of `--from` (default: the release pinned in
   `deploy/production/compose.env.example`) by following the guide.
2. The first owner signs up from the invite, connects GitHub, imports a
   repository, connects a model provider and a webhook, approves and enables
   the jobs. A report runs in the gVisor sandbox at the pinned commit; a failing
   run shows a readable reason; both reach the webhook. A pushed commit is
   detected, resynced and run. Artifacts are in S3. The provider key and the
   GitHub tokens never appear in prompts, webhooks or artifacts.
3. The guide's backup, then its update commands to the image built from this
   working tree.
4. After the upgrade, a full restart, and a restore of a backup onto a wiped
   server: password sign-in, run history, artifacts, saved credentials and new
   runs all work.

The backup, restore and update steps run the bash blocks of the guide's
"Update and back up" section, so the published commands are what is tested.

## How it works

`run.sh` builds the image, starts a `docker:dind` host, installs a pinned gVisor
release and registers `runsc`, copies in the working tree and runs the steps
in `host.sh`. Everything else runs in the app image, so there are no extra
dependencies:

- `compose.e2e.yaml` adds the test services to `compose.production.yaml`:
  RustFS for S3, Caddy with its internal CA, and `fake_services.py`.
- `fake_services.py` serves GitHub's API, OAuth and Git smart HTTP (through
  `git http-backend`), the release smoke test's fake model, and a webhook
  receiver. Docker DNS aliases send `api.github.com`, `github.com` and the test
  hosts to it.
- The test network uses an address range that counts as public, so
  production's public-HTTPS rules for model providers and webhooks apply
  unchanged. `gen_secrets.py` creates a test CA, and `Dockerfile.e2e` adds it
  to the trusted certificates of web, gateway and worker. The application code
  is unchanged.
- `drive.py` uses the deployment over HTTPS like a browser and checks results.
- `s3_copy.py` stands in for the S3 provider's tools when backing up the bucket.

Not covered: real GitHub.com and model providers, and Caddy's public
certificate issuance.

If a step fails, the host is kept and the step's log is printed. See service
logs with `docker exec secondshift-e2e-host sh
/opt/secondshift/scripts/production_e2e/host.sh logs`, and remove the host with
`docker rm -f -v secondshift-e2e-host`.
