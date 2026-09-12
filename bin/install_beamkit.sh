#!/bin/bash
# install_beamkit.sh - Install a beamkit release on CVMFS
# Run directly on cvmfsmu2e@oasiscfs.fnal.gov
# Usage: ./install_beamkit.sh [-n] [-t [DIR]] [-s SRC] (-c SERIES | -N) v0.3.0
#   -n          Dry run: check the GitHub tag and install path, make no changes.
#   -t [DIR]    Test mode: skip cvmfs_server calls and install into a local
#               writable dir (default: a fresh mktemp -d).
#   -s SRC      Install from a local checkout instead of a GitHub tag
#               (test mode only; lets you verify the venv build before tagging).
#   -c SERIES   htcondor wheel series to install, e.g. 25.0 -- the pool's
#               major.minor from `condor_version` on a gpvm. The Fermilab
#               path reads the grid queue through it.
#   -N          NERSC-only venv: no htcondor. The Fermilab path will then
#               fail loudly at its first queue read.
#
# The release directory holds the source tree plus a .venv built at its
# final path (venvs record absolute paths, and the cvmfs path is stable on
# every client). The interpreter is the ops-019 spack python on cvmfs, so
# the venv resolves on any Linux host that mounts the repo.
# Clients start the server with <release>/scripts/beamkit-mcp-cvmfs.

set -e

DRY_RUN=false
TEST_MODE=false
TEST_BASE=""
SRC_TREE=""
CONDOR_SERIES=""
NERSC_ONLY=false
while [[ "$1" == -* ]]; do
  case "$1" in
    -n) DRY_RUN=true; shift ;;
    -t)
      TEST_MODE=true
      shift
      if [[ -n "$1" && "$1" != -* && ! "$1" =~ ^v?[0-9]+\.[0-9]+ ]]; then
        TEST_BASE="$1"
        shift
      fi
      ;;
    -s) SRC_TREE="$2"; shift 2 ;;
    -c) CONDOR_SERIES="$2"; shift 2 ;;
    -N) NERSC_ONLY=true; shift ;;
    *) break ;;
  esac
done

VER=${1:?Usage: $0 [-n] [-t [DIR]] [-s SRC] (-c SERIES | -N) <version> (e.g. v0.3.0)}
if [[ -z "$CONDOR_SERIES" ]] && ! $NERSC_ONLY; then
  echo "ERROR: give -c <htcondor series> (e.g. 25.0) or -N for a NERSC-only venv."
  exit 1
fi
if [[ -n "$CONDOR_SERIES" ]] && $NERSC_ONLY; then
  echo "ERROR: -c and -N are mutually exclusive."
  exit 1
fi
if [[ -n "$SRC_TREE" ]] && ! $TEST_MODE; then
  echo "ERROR: -s is for test mode only; a CVMFS install comes from a GitHub tag."
  exit 1
fi

CVMFS_REPO=mu2e.opensciencegrid.org
GITHUB_REPO=${BEAMKIT_GITHUB_REPO:-oksuzian/beamkit}
PYTHON=${BEAMKIT_INSTALL_PYTHON:-/cvmfs/${CVMFS_REPO}/spackages/241207/spack/var/spack/environments/ops-019/.spack-env/view/bin/python3}
if $TEST_MODE; then
  INSTALL_BASE=${TEST_BASE:-$(mktemp -d /tmp/test_cvmfs_install.XXXXXX)}
  mkdir -p "${INSTALL_BASE}"
  echo "=== TEST MODE: install base is ${INSTALL_BASE} (no cvmfs_server calls) ==="
else
  INSTALL_BASE=/cvmfs/${CVMFS_REPO}/bin/beamkit
fi
TMPDIR=$(mktemp -d)
export PIP_CACHE_DIR="${TMPDIR}/pip-cache"

cleanup() {
  rm -rf "${TMPDIR}"
}
trap cleanup EXIT

abort_transaction() {
  if $TEST_MODE; then
    echo "ERROR: aborting (test mode -- no cvmfs_server abort)"
    return
  fi
  echo "ERROR: Aborting CVMFS transaction..."
  cd ~
  cvmfs_server abort -f ${CVMFS_REPO} 2>/dev/null || true
  echo "WARNING: Per CVMFS policy, run an empty transaction+publish to restore correct permissions:"
  echo "  cvmfs_server transaction ${CVMFS_REPO} && cvmfs_server publish ${CVMFS_REPO}"
}
trap abort_transaction ERR

[[ -x "$PYTHON" ]] || { echo "ERROR: interpreter ${PYTHON} not found (set BEAMKIT_INSTALL_PYTHON)."; exit 1; }
"$PYTHON" - <<'PY' || { echo "ERROR: beamkit needs python >= 3.10."; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

if [[ -z "$SRC_TREE" ]]; then
  # -L is required: github.com archive URLs 302-redirect to S3.
  echo "Checking GitHub release ${VER} on ${GITHUB_REPO}..."
  curl -fsIL "https://github.com/${GITHUB_REPO}/archive/refs/tags/${VER}.tar.gz" > /dev/null \
    || { echo "ERROR: Release ${VER} not found on GitHub (${GITHUB_REPO})."; exit 1; }
  echo "Release ${VER} found."
else
  [[ -f "${SRC_TREE}/pyproject.toml" ]] || { echo "ERROR: ${SRC_TREE} is not a beamkit checkout."; exit 1; }
  echo "Installing from local checkout ${SRC_TREE}."
fi

if [ -d "${INSTALL_BASE}/${VER}" ]; then
  echo "ERROR: ${INSTALL_BASE}/${VER} already exists. Aborting."
  exit 1
fi

if $DRY_RUN; then
  echo "[dry-run] Would install to ${INSTALL_BASE}/${VER} with ${PYTHON}"
  if $NERSC_ONLY; then echo "[dry-run] Would build a NERSC-only venv (no htcondor)"; else echo "[dry-run] Would install htcondor==${CONDOR_SERIES}.*"; fi
  echo "[dry-run] Would update ${INSTALL_BASE}/current -> ${VER}"
  echo "[dry-run] No changes made."
  exit 0
fi

if $TEST_MODE; then
  echo "Skipping cvmfs_server transaction (test mode)"
else
  echo "Opening CVMFS transaction..."
  cvmfs_server transaction ${CVMFS_REPO}
fi

REL="${INSTALL_BASE}/${VER}"
# First release: the base directory does not exist yet (inside the
# transaction for a real install, plain mkdir in test mode).
mkdir -p "${INSTALL_BASE}"
if [[ -z "$SRC_TREE" ]]; then
  echo "Downloading and extracting beamkit ${VER}..."
  curl -fsSL "https://github.com/${GITHUB_REPO}/archive/refs/tags/${VER}.tar.gz" \
    | tar -xz -C "${TMPDIR}"
  SRC_DIR=$(ls -d "${TMPDIR}"/beamkit-*/)
  mv "${SRC_DIR}" "${REL}"
else
  mkdir -p "${REL}"
  # No .git, no .venv, no scratch: the release is the tracked tree.
  (cd "${SRC_TREE}" && git ls-files -z | tar --null -cf - -T -) | tar -xf - -C "${REL}"
fi

echo "Building venv at ${REL}/.venv with ${PYTHON}..."
# Built AFTER the move: a venv records its absolute path. PYTHONPATH is
# unset so pip resolves against the venv, not a spack site-packages.
env -u PYTHONPATH "$PYTHON" -m venv "${REL}/.venv"
env -u PYTHONPATH "${REL}/.venv/bin/pip" install --no-cache-dir --upgrade pip 1>&2
if ! $NERSC_ONLY; then
  echo "Installing htcondor==${CONDOR_SERIES}.* ..."
  env -u PYTHONPATH "${REL}/.venv/bin/pip" install --no-cache-dir "htcondor==${CONDOR_SERIES}.*" 1>&2
fi
env -u PYTHONPATH "${REL}/.venv/bin/pip" install --no-cache-dir "${REL}" 1>&2
# pip leaves its build tree and egg-info in the source directory.
rm -rf "${REL}/build" "${REL}"/src/*.egg-info
echo "binding to: ${PYTHON}" > "${REL}/.venv-binding"
env -u PYTHONPATH "${REL}/.venv/bin/python" -c "import beamkit.server, mcp, requests, authlib; print('OK: beamkit', beamkit.server.__name__)"

echo "Updating 'current' symlink to ${VER}..."
ln -sfn "${VER}" "${INSTALL_BASE}/current"

if $TEST_MODE; then
  echo "Skipping cvmfs_server publish (test mode)"
else
  echo "Publishing to CVMFS..."
  cd ~
  cvmfs_server publish ${CVMFS_REPO}
fi

echo "Done. beamkit ${VER} is now available at ${REL}"
echo "       'current' symlink points to ${VER}"
echo "       MCP command: ${INSTALL_BASE}/current/scripts/beamkit-mcp-cvmfs"
