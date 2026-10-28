#!/bin/sh
# Test the production Compose deployment end to end on a disposable gVisor host.
#
#   scripts/production_e2e/run.sh [--from IMAGE] [--keep]
#
# Builds the image from this working tree, starts a privileged Docker-in-Docker
# host with gVisor registered as runsc, deploys --from (default: the release
# pinned in deploy/production/compose.env.example) by following
# docs/production-deployment.md, then checks the first owner journey, an
# upgrade to the working-tree image, a restart, and a backup restored onto a
# wiped server. The host is removed afterwards unless the run fails or --keep.
set -eu
cd "$(dirname "$0")/../.."

HOST=secondshift-e2e-host
UNDER_TEST=secondshift:e2e-under-test
GVISOR_RELEASE=20261019
FROM=$(sed -n 's/^SECONDSHIFT_IMAGE=//p' deploy/production/compose.env.example)
KEEP=no
while [ $# -gt 0 ]; do
  case "$1" in
  --from) FROM=$2; shift 2 ;;
  --keep) KEEP=yes; shift ;;
  *) echo "Usage: $0 [--from IMAGE] [--keep]" >&2; exit 2 ;;
  esac
done

on_host() { docker exec "$HOST" sh -c "$1"; }
step() {
  name=$1
  shift
  if on_host "cd /opt/secondshift && sh scripts/production_e2e/host.sh $* \
      > /var/log/e2e-$name.log 2>&1"; then
    summary=$(on_host "grep -E '^[0-9]+ checks passed' /var/log/e2e-$name.log" || true)
    echo "ok    $name ${summary:+($summary)}"
  else
    echo "FAIL  $name"
    on_host "tail -40 /var/log/e2e-$name.log"
    echo
    echo "The test host $HOST is kept. Logs: docker exec $HOST ls /var/log"
    echo "Remove it with: docker rm -f -v $HOST"
    exit 1
  fi
}

if docker container inspect "$HOST" >/dev/null 2>&1; then
  echo "A test host named $HOST already exists. Remove it with:" >&2
  echo "  docker rm -f -v $HOST" >&2
  exit 1
fi

echo "Building $UNDER_TEST from the working tree"
docker build -q -t "$UNDER_TEST" . >/dev/null

echo "Starting the test host with gVisor $GVISOR_RELEASE"
docker run -d --privileged --name "$HOST" docker:29-dind >/dev/null
until on_host "docker info" >/dev/null 2>&1; do sleep 1; done
on_host "set -e; mkdir -p /tmp/gvisor && cd /tmp/gvisor
  url=https://storage.googleapis.com/gvisor/releases/release/$GVISOR_RELEASE/\$(uname -m)
  wget -q \$url/gvisor.tar.bz2 \$url/gvisor.tar.bz2.sha512
  sha512sum -c gvisor.tar.bz2.sha512 >/dev/null
  tar -xjf gvisor.tar.bz2 -C /usr/local/bin
  runsc install >/dev/null 2>&1"
# dind has no init system: restart the container to load the new runtime.
docker restart "$HOST" >/dev/null
until on_host "docker info" >/dev/null 2>&1; do sleep 1; done
on_host "docker run --rm --runtime=runsc hello-world >/dev/null 2>&1" || {
  echo "gVisor does not work on this Docker host." >&2
  exit 1
}

echo "Copying the checkout and $UNDER_TEST to the host"
docker save "$UNDER_TEST" | docker exec -i "$HOST" docker load >/dev/null
on_host "mkdir -p /opt/secondshift"
# Skip files deleted from the working tree. This works with GNU tar and macOS bsdtar.
git ls-files -z --cached --others --exclude-standard |
  xargs -0 sh -c 'for path; do if [ -e "$path" ]; then printf "%s\0" "$path"; fi; done' sh |
  COPYFILE_DISABLE=1 tar --null -T - -cf - |
  docker exec -i "$HOST" tar -x -C /opt/secondshift

echo "Deploying $FROM, then upgrading to $UNDER_TEST"
echo
step setup setup "$FROM"
step first-owner drive first "\$(sh scripts/production_e2e/host.sh invite-link)"
step backup-before-upgrade backup
step upgrade upgrade "$UNDER_TEST"
step after-upgrade drive after-restart
step restart restart
step after-restart drive after-restart
step backup backup
step lose-the-server wipe
step restore restore
step after-restore drive after-restart

echo
echo "Passed."
if [ "$KEEP" = yes ]; then
  echo "The test host $HOST is kept. Remove it with: docker rm -f -v $HOST"
else
  docker rm -f -v "$HOST" >/dev/null
fi
