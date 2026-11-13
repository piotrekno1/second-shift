#!/bin/sh
# Steps of the production end-to-end test. Runs on the gVisor test host from
# the deployment checkout; run.sh calls it. Follows
# docs/production-deployment.md, plus the test-only Compose override here.
#
#   host.sh setup IMAGE        fresh deployment of IMAGE, prints an invite link
#   host.sh drive first LINK   first owner, GitHub import, runs, resync
#   host.sh drive after-restart
#   host.sh restart            full compose down / up, volumes kept
#   host.sh backup             the guide's backup commands
#   host.sh wipe               lose the server: volumes, config and runner data
#   host.sh restore            the guide's restore commands, latest backup
#   host.sh upgrade IMAGE      the guide's update commands
#   host.sh images             the images tested and their digests
#   host.sh logs [SERVICE...]
#   host.sh destroy            remove everything, including the fake GitHub
set -eu
cd "$(dirname "$0")/../.."
E=scripts/production_e2e
STATE=/var/lib/secondshift-e2e
mkdir -p "$STATE"

step() { echo; echo "=== $*"; }

trust_test_ca() {
  # The image runs as published. Web, gateway, worker and the driver trust the
  # test CA through a mounted bundle: the image's own CA list plus the test CA.
  name=$(echo "$SECONDSHIFT_IMAGE" | tr -c 'a-zA-Z0-9\n' '-')
  E2E_CA_BUNDLE=$STATE/ca/$name.pem
  if [ ! -s "$STATE/ca/$name.path" ]; then
    mkdir -p "$STATE/ca"
    docker run --rm "$SECONDSHIFT_IMAGE" \
      python -c 'import certifi; print(certifi.where())' > "$STATE/ca/$name.path"
    docker run --rm "$SECONDSHIFT_IMAGE" \
      sh -c 'cat "$(python -c "import certifi; print(certifi.where())")"' > "$E2E_CA_BUNDLE"
    cat "$E/certs/ca.pem" >> "$E2E_CA_BUNDLE"
    # Public certificates only. The app runs as 10001 and setup uses umask 077.
    chmod 644 "$E2E_CA_BUNDLE"
  fi
  E2E_CERTIFI=$(cat "$STATE/ca/$name.path")
  export E2E_CA_BUNDLE E2E_CERTIFI
}

record_image() {
  # Record what is under test and check every application service runs it.
  id=$(docker image inspect --format '{{.Id}}' "$SECONDSHIFT_IMAGE")
  digest=$(docker image inspect --format '{{join .RepoDigests " "}}' "$SECONDSHIFT_IMAGE")
  for service in web gateway worker runner; do
    running=$(docker inspect --format '{{.Image}}' "$(dc ps -q "$service")")
    if [ "$running" != "$id" ]; then
      echo "$service runs $running, not $SECONDSHIFT_IMAGE ($id)" >&2
      exit 1
    fi
  done
  {
    echo "$1: $SECONDSHIFT_IMAGE"
    echo "  digest: ${digest:-none, built locally}"
    echo "  id: $id"
  } | tee -a "$STATE/images.txt"
}

load_env() {
  # shellcheck disable=SC1091
  . deploy/production/compose.env
  export SECONDSHIFT_IMAGE
  trust_test_ca
}

compose() {
  docker compose --env-file deploy/production/compose.env \
    -f compose.production.yaml -f "$E/compose.e2e.yaml" "$@"
}

# The guide's shortcut. Locally built images are not in a registry, so a
# failed pull is tolerated; a real server pulls the release from GHCR.
dc() {
  if [ "$1" = pull ]; then
    compose pull --ignore-pull-failures --quiet 2>/dev/null || true
  else
    compose "$@"
  fi
}

migrate() {
  dc run --rm --no-deps web python manage.py migrate --noinput | tail -1
  dc run --rm --no-deps runner python manage.py migrate --noinput \
    --settings=secondshift.config.settings.runner | tail -1
}

s3_copy() {
  # Stand-in for the S3 provider's own tools (aws s3 sync, rclone).
  docker run --rm --user 0:0 --network secondshift_default -v "$2:/copy" \
    -e S3_KEY="$ARTIFACT_ACCESS_KEY_ID" -e S3_SECRET="$ARTIFACT_SECRET_ACCESS_KEY" \
    -e S3_BUCKET="$ARTIFACT_BUCKET" -v "$PWD/$E:/e2e:ro" "$SECONDSHIFT_IMAGE" \
    python /e2e/s3_copy.py "$1" /copy
}

run_doc() {
  # Run the Nth bash block of the guide's "Update and back up" section, so the
  # published commands themselves are what is tested.
  awk -v n="$1" '/^## Update and back up/ { section = 1 }
    section && /^```bash$/ { count++; if (count == n) { printing = 1; next } }
    printing && /^```$/ { exit }
    printing' docs/production-deployment.md > "$STATE/doc-block.sh"
  [ -s "$STATE/doc-block.sh" ] || { echo "No block $1 in the guide" >&2; exit 1; }
  if [ -n "${BACKUP_OVERRIDE:-}" ]; then
    sed -i "s#^backup=.*#backup=$BACKUP_OVERRIDE#
s#^secrets=.*#secrets=$SECRETS_OVERRIDE#" "$STATE/doc-block.sh"
  fi
  echo "--- guide block $1:"; cat "$STATE/doc-block.sh"; echo "---"
  # shellcheck disable=SC1091
  . "$STATE/doc-block.sh"
}

hex() { head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

case "$1" in
setup)
  SECONDSHIFT_IMAGE=$2
  step "Test CA, fake-service certificate and GitHub App key"
  [ -f "$E/certs/ca.pem" ] || docker run --rm --user 0:0 -v "$PWD/$E:/out" \
    -w /out "$SECONDSHIFT_IMAGE" python /out/gen_secrets.py /out/certs >/dev/null

  step "Configuration, as in the deployment guide"
  umask 077
  cp deploy/production/compose.env.example deploy/production/compose.env
  sed -i "s#^SECONDSHIFT_IMAGE=.*#SECONDSHIFT_IMAGE=$SECONDSHIFT_IMAGE#
s#^DOMAIN=.*#DOMAIN=secondshift.e2e.test#
s#^POSTGRES_PASSWORD=.*#POSTGRES_PASSWORD=$(hex)#
s#^APP_DATABASE_PASSWORD=.*#APP_DATABASE_PASSWORD=$(hex)#
s#^RUNNER_DATABASE_PASSWORD=.*#RUNNER_DATABASE_PASSWORD=$(hex)#
s#^ARTIFACT_ACCESS_KEY_ID=.*#ARTIFACT_ACCESS_KEY_ID=e2eaccess#
s#^ARTIFACT_SECRET_ACCESS_KEY=.*#ARTIFACT_SECRET_ACCESS_KEY=$(hex)#
s#^ARTIFACT_ENDPOINT_URL=.*#ARTIFACT_ENDPOINT_URL=http://rustfs:9000#
s#^ARTIFACT_ADDRESSING_STYLE=.*#ARTIFACT_ADDRESSING_STYLE=path#" \
    deploy/production/compose.env
  # RustFS has a single key here. A real server gives the gateway its own
  # read-only key.
  . deploy/production/compose.env
  sed -i "s#^ARTIFACT_READ_ACCESS_KEY_ID=.*#ARTIFACT_READ_ACCESS_KEY_ID=$ARTIFACT_ACCESS_KEY_ID#
s#^ARTIFACT_READ_SECRET_ACCESS_KEY=.*#ARTIFACT_READ_SECRET_ACCESS_KEY=$ARTIFACT_SECRET_ACCESS_KEY#" \
    deploy/production/compose.env
  docker run --rm --user 0:0 -v "$PWD/deploy/production:$PWD/deploy/production" \
    -w "$PWD/deploy/production" "$SECONDSHIFT_IMAGE" \
    python -m secondshift.config.container_env --hostname secondshift.e2e.test
  for role in web worker; do
    {
      echo "SECONDSHIFT_GITHUB_APP_ID=424242"
      echo "SECONDSHIFT_GITHUB_APP_SLUG=secondshift-e2e"
      echo "SECONDSHIFT_GITHUB_APP_CLIENT_ID=Iv1.e2e"
      echo "SECONDSHIFT_GITHUB_APP_CLIENT_SECRET=e2e-client-secret"
      cat "$E/certs/github_app_key.env"
    } >> "deploy/production/$role.env"
  done
  load_env
  mkdir -p "$RUNNER_ROOT"

  step "Start"
  dc config --quiet
  dc up -d --wait postgres rustfs
  s3_copy create-bucket /tmp
  migrate
  dc up -d --wait
  dc ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'
  record_image deployed
  dc exec web python manage.py create_workspace "E2E team" | tee "$STATE/invite.txt"
  ;;
drive)
  load_env
  mkdir -p "$STATE/driver" && chown 10001:10001 "$STATE/driver"
  # The driver trusts only Caddy's local CA, as a browser that installed it.
  dc exec -T caddy cat /data/caddy/pki/authorities/local/root.crt \
    > "$STATE/driver/caddy-root.pem"
  chmod 644 "$STATE/driver/caddy-root.pem"
  docker run --rm --network secondshift_default \
    -e S3_KEY="$ARTIFACT_ACCESS_KEY_ID" -e S3_SECRET="$ARTIFACT_SECRET_ACCESS_KEY" \
    -e S3_BUCKET="$ARTIFACT_BUCKET" \
    -v "$PWD/$E:/e2e:ro" -v "$STATE/driver:/state" \
    -v "$E2E_CA_BUNDLE:/etc/ssl/certs/ca-certificates.crt:ro" \
    -v "$E2E_CA_BUNDLE:$E2E_CERTIFI:ro" "$SECONDSHIFT_IMAGE" \
    python /e2e/drive.py "$2" "${3:-}"
  ;;
invite-link)
  grep -o 'https://[^ ]*/invite/[^ ]*' "$STATE/invite.txt"
  ;;
restart)
  load_env
  step "Full stop and start (named volumes kept)"
  dc down
  dc up -d --wait
  ;;
backup)
  load_env
  step "Back up, with the guide's commands"
  run_doc 1
  backup=$(find /srv/secondshift-backups -mindepth 1 -maxdepth 1 | sort | tail -1)
  s3_copy download "$backup/s3"
  run_doc 2
  if [ -e "$backup/config.tgz" ]; then
    echo "The data backup must not contain the server secrets." >&2
    exit 1
  fi
  echo "$backup" > "$STATE/last-backup"
  ls -la "$backup" "/srv/secondshift-secrets/$(basename "$backup")"
  ;;
wipe)
  load_env
  step "Lose the server (the fake GitHub's volume is kept: GitHub is external)"
  dc down
  for volume in postgres_data artifacts caddy_data caddy_config rustfs_data; do
    docker volume rm "secondshift_$volume" >/dev/null
  done
  rm -rf "$RUNNER_ROOT" deploy/production/compose.env deploy/production/*.env \
    deploy/production/runner
  ;;
restore)
  backup=$(cat "$STATE/last-backup")
  secrets=/srv/secondshift-secrets/$(basename "$backup")
  step "Restore $backup, with the guide's commands"
  # The provider's bucket restore comes first, as the guide says.
  tar -xzf "$secrets/config.tgz" deploy/production/compose.env
  load_env
  dc up -d --wait rustfs
  s3_copy create-bucket /tmp
  s3_copy upload "$backup/s3"
  BACKUP_OVERRIDE=$backup SECRETS_OVERRIDE=$secrets run_doc 3
  dc ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'
  record_image restored
  ;;
upgrade)
  load_env
  step "Update from $SECONDSHIFT_IMAGE to $2, with the guide's commands"
  sed -i "s#^SECONDSHIFT_IMAGE=.*#SECONDSHIFT_IMAGE=$2#" deploy/production/compose.env
  load_env
  run_doc 4
  dc ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'
  record_image upgraded
  ;;
agent-probe)
  load_env
  docker build -q -t secondshift-agent:e2e-probe -f "$E/Dockerfile.agent-probe" "$E" >/dev/null
  # gVisor cannot use Docker's embedded DNS. The probe reaches the broker by IP.
  printf 'nameserver 1.1.1.1\n' > "$RUNNER_ROOT/agent-resolv.conf"
  image=$(docker image inspect --format '{{.Id}}' secondshift-agent:e2e-probe)
  printf '\nAGENT_IMAGE=%s\n' "$image" >> deploy/production/compose.env
  for storage in s3 filesystem; do
    printf '\nARTIFACT_STORAGE=%s\n' "$storage" >> deploy/production/compose.env
    dc up -d --wait web gateway worker runner
    dc exec -T web python /scripts/production_e2e/agent_run.py
  done
  printf '\nARTIFACT_STORAGE=s3\nAGENT_IMAGE=\n' >> deploy/production/compose.env
  dc up -d --wait web gateway worker runner
  ;;
pat-only)
  load_env
  for role in web worker; do
    sed '/^SECONDSHIFT_GITHUB_APP_/d' "deploy/production/$role.env" > "$STATE/$role.env"
    cat "$STATE/$role.env" > "deploy/production/$role.env"
  done
  echo 'SECONDSHIFT_REPOSITORY_SYNC_INTERVAL_SECONDS=2' >> deploy/production/worker.env
  dc up -d --wait web worker
  sh "$0" drive pat-only
  dc exec -T web python /scripts/production_e2e/pat_publish.py
  ;;
images)
  cat "$STATE/images.txt"
  ;;
logs)
  load_env
  shift
  dc logs --tail 60 "$@"
  ;;
destroy)
  if [ -f deploy/production/compose.env ]; then
    load_env
    dc down -v --remove-orphans
  fi
  docker volume rm secondshift_fake_git >/dev/null 2>&1 || true
  rm -rf /srv/secondshift/runner deploy/production/*.env deploy/production/runner \
    "$E/certs" /srv/secondshift-backups /srv/secondshift-secrets "$STATE"
  ;;
*)
  echo "Unknown step: $1" >&2
  exit 2
  ;;
esac
