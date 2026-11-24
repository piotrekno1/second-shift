#!/bin/sh
# Test the production Compose deployment end to end on a disposable gVisor host.
#
#   scripts/production_e2e/run.sh [--from IMAGE] [--keep]
#   scripts/production_e2e/run.sh --image IMAGE [--keep]
#
# Builds the image from this working tree, starts a privileged Docker-in-Docker
# host with gVisor registered as runsc, deploys --from (default: the release
# pinned in deploy/production/compose.env.example) by following
# docs/production-deployment.md, then checks the first owner journey, an
# upgrade to the working-tree image, a restart, and a backup restored onto a
# wiped server. A second gVisor host then runs a runner from compose.runner.yaml
# that takes runs and relays its agents' tool calls. The hosts are removed
# afterwards unless the run fails or --keep.
#
# With --image, it tests that one published image as it is: no build and no
# upgrade step.
set -eu
cd "$(dirname "$0")/../.."

HOST=secondshift-e2e-host
RUNNER_HOST=secondshift-e2e-runner-host
NETWORK=secondshift-e2e
UNDER_TEST=secondshift:e2e-under-test
GVISOR_RELEASE=20261019
FROM=$(sed -n 's/^SECONDSHIFT_IMAGE=//p' deploy/production/compose.env.example)
KEEP=no
ONLY=
while [ $# -gt 0 ]; do
  case "$1" in
  --from) FROM=$2; shift 2 ;;
  --image) ONLY=$2; FROM=$2; shift 2 ;;
  --keep) KEEP=yes; shift ;;
  *) echo "Usage: $0 [--from IMAGE | --image IMAGE] [--keep]" >&2; exit 2 ;;
  esac
done

on_host() { docker exec "$HOST" sh -c "$1"; }
step_on() {
  target=$1
  name=$2
  shift 2
  if docker exec ${STEP_ENV:-} "$target" sh -c "cd /opt/secondshift && \
      sh scripts/production_e2e/host.sh $* > /var/log/e2e-$name.log 2>&1"; then
    summary=$(docker exec "$target" \
      sh -c "grep -E '^[0-9]+ checks passed' /var/log/e2e-$name.log" || true)
    echo "ok    $name ${summary:+($summary)}"
  else
    echo "FAIL  $name"
    docker exec "$target" tail -40 "/var/log/e2e-$name.log"
    echo
    echo "The test hosts are kept. Logs: docker exec $target ls /var/log"
    echo "Remove them with: docker rm -f -v $HOST $RUNNER_HOST; docker network rm $NETWORK"
    exit 1
  fi
}
step() { step_on "$HOST" "$@"; }

for name in "$HOST" "$RUNNER_HOST"; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    echo "A test host named $name already exists. Remove it with:" >&2
    echo "  docker rm -f -v $name" >&2
    exit 1
  fi
done

if [ -z "$ONLY" ]; then
  echo "Building $UNDER_TEST from the working tree"
  docker build -q -t "$UNDER_TEST" . >/dev/null
fi

# A privileged Docker-in-Docker host with gVisor registered as runsc.
start_host() {
  docker run -d --privileged --name "$1" --network "$NETWORK" docker:29-dind >/dev/null
  until docker exec "$1" docker info >/dev/null 2>&1; do sleep 1; done
  docker exec "$1" sh -c "set -e; mkdir -p /tmp/gvisor && cd /tmp/gvisor
    url=https://storage.googleapis.com/gvisor/releases/release/$GVISOR_RELEASE/\$(uname -m)
    wget -q \$url/gvisor.tar.bz2 \$url/gvisor.tar.bz2.sha512
    sha512sum -c gvisor.tar.bz2.sha512 >/dev/null
    tar -xjf gvisor.tar.bz2 -C /usr/local/bin
    runsc install >/dev/null 2>&1"
  # dind has no init system: restart the container to load the new runtime.
  docker restart "$1" >/dev/null
  until docker exec "$1" docker info >/dev/null 2>&1; do sleep 1; done
  docker exec "$1" sh -c "docker run --rm --runtime=runsc hello-world >/dev/null 2>&1" || {
    echo "gVisor does not work on this Docker host." >&2
    exit 1
  }
}

# Copy the checkout to a host. Skip files deleted from the working tree. This
# works with GNU tar and macOS bsdtar.
copy_checkout() {
  docker exec "$1" mkdir -p /opt/secondshift
  git ls-files -z --cached --others --exclude-standard |
    xargs -0 sh -c 'for path; do if [ -e "$path" ]; then printf "%s\0" "$path"; fi; done' sh |
    COPYFILE_DISABLE=1 tar --null -T - -cf - |
    docker exec -i "$1" tar -x -C /opt/secondshift
}

docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null
echo "Starting the test host with gVisor $GVISOR_RELEASE"
start_host "$HOST"

if [ -n "$ONLY" ]; then
  echo "Copying the checkout to the host"
else
  echo "Copying the checkout and $UNDER_TEST to the host"
  docker save "$UNDER_TEST" | docker exec -i "$HOST" docker load >/dev/null
fi
copy_checkout "$HOST"

if [ -n "$ONLY" ]; then
  echo "Deploying $ONLY"
else
  echo "Deploying $FROM, then upgrading to $UNDER_TEST"
fi
echo
step setup setup "$FROM"
step deployment deployment-checks
step first-owner drive first "\$(sh scripts/production_e2e/host.sh invite-link)"
if [ -z "$ONLY" ]; then
  step backup-before-upgrade backup
  step upgrade upgrade "$UNDER_TEST"
  step after-upgrade drive after-restart
fi
step agent-image agent-image
step agent-storage agent-probe
step restart restart
step after-restart drive after-restart
step backup backup
step lose-the-server wipe
step restore restore
step after-restore drive after-restart
step deployment-after-restore deployment-checks
step pat-only pat-only

echo
# A release from before compose.runner.yaml has no runner of its own to deploy.
if [ -f compose.runner.yaml ]; then
  echo "Starting a second gVisor host for a runner on another host"
  start_host "$RUNNER_HOST"
  image=${ONLY:-$UNDER_TEST}
  docker exec "$HOST" docker save "$image" | docker exec -i "$RUNNER_HOST" docker load >/dev/null
  copy_checkout "$RUNNER_HOST"
  # The runner's certificate comes from the test CA, which the control plane trusts.
  docker exec "$HOST" tar -C /opt/secondshift/scripts/production_e2e -cf - certs |
    docker exec -i "$RUNNER_HOST" tar -x -C /opt/secondshift/scripts/production_e2e
  step_on "$RUNNER_HOST" runner-host runner-host "$image"
  RUNNER_B_ADDRESS=$(docker inspect --format "{{(index .NetworkSettings.Networks \"$NETWORK\").IPAddress}}" "$RUNNER_HOST")
  # Passed through the environment, so the token is on no command line.
  RUNNER_B_TOKEN=$(docker exec "$RUNNER_HOST" \
    sed -n 's/^SECONDSHIFT_RUNNER_SERVICE_TOKEN=//p' /opt/secondshift/deploy/runner/runner.env)
  export RUNNER_B_ADDRESS RUNNER_B_TOKEN
  STEP_ENV="-e RUNNER_B_ADDRESS -e RUNNER_B_TOKEN" step second-runner second-runner
else
  echo "skip  second-runner: this release has no compose.runner.yaml"
fi

echo
on_host "cd /opt/secondshift && sh scripts/production_e2e/host.sh images"
echo "Passed."
if [ "$KEEP" = yes ]; then
  echo "The test hosts are kept. Remove them with:"
  echo "  docker rm -f -v $HOST $RUNNER_HOST; docker network rm $NETWORK"
else
  docker rm -f -v "$HOST" >/dev/null
  docker rm -f -v "$RUNNER_HOST" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null
fi
