#!/bin/bash
#
# Check whether the OSMC kernel patch series still applies to a given
# upstream kernel version, without building anything.
#
#   ./tools/check-kernel-patch-series.sh rbp2 5.15.98
#   ./tools/check-kernel-patch-series.sh rbp2 5.15.221
#
# Exits 0 if the whole series applies, 1 otherwise, and prints a per-file
# breakdown of failures so you can see whether they land anywhere that
# matters for the target device.
#
# Needs: git, patch, ~2GB disk per version, network access to a kernel
# mirror. kernel.org is used for tarballs by the real build; this uses the
# GitHub stable mirror because it lets us fetch a single tag shallowly.

set -u

DEVICE="${1:?usage: $0 <device> <version>   e.g. rbp2 5.15.98}"
VERSION="${2:?usage: $0 <device> <version>   e.g. rbp2 5.15.98}"

MIRROR="${KERNEL_MIRROR:-https://github.com/gregkh/linux}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PATCH_DIR="${REPO_ROOT}/package/kernel-osmc/patches"
WORK="${WORKDIR:-/var/tmp/osmc-kernel-check}"
TREE="${WORK}/linux"

# The build applies "rbp-*" then "<device>-*", each sorted - see
# install_patch() in scripts/common.sh and the call sites in
# package/kernel-osmc/build.sh.
case "$DEVICE" in
    rbp2|rbp464) PREFIXES="rbp ${DEVICE}" ;;
    *)           PREFIXES="${DEVICE}" ;;
esac

echo "Device   : ${DEVICE}"
echo "Version  : ${VERSION}"
echo "Patches  : ${PATCH_DIR}"
echo

mkdir -p "${WORK}"
if [ ! -d "${TREE}/.git" ]; then
    echo "Cloning ${MIRROR} at v${VERSION} (this is large, once only)..."
    git clone --depth 1 --branch "v${VERSION}" --single-branch \
        "${MIRROR}" "${TREE}" || exit 1
else
    echo "Fetching v${VERSION}..."
    git -C "${TREE}" fetch --depth 1 --quiet origin "tag" "v${VERSION}" || exit 1
fi

# Reset hard first: a previous run leaves the series applied to tracked
# files, and git checkout does not discard those - it would silently stack
# this run's patches on top of the last one's and report bogus failures.
git -C "${TREE}" reset --quiet --hard HEAD
git -C "${TREE}" clean --quiet -fdx
git -C "${TREE}" checkout --quiet --detach "v${VERSION}" || exit 1
git -C "${TREE}" reset --quiet --hard "v${VERSION}"
git -C "${TREE}" clean --quiet -fdx

series=""
for prefix in $PREFIXES; do
    series="${series} $(find "${PATCH_DIR}" -name "${prefix}-*.patch" -printf '%P\n' | sort)"
done

echo
rc=0
out="${WORK}/apply.log"
: > "$out"

for pt in $series; do
    printf '  %-46s ' "$pt"
    if grep -q "GIT binary patch" "${PATCH_DIR}/${pt}"; then
        if git -C "${TREE}" apply --whitespace=nowarn "${PATCH_DIR}/${pt}" 2>>"$out"; then
            echo "applied (binary)"
        else
            echo "FAILED (binary)"; rc=1
        fi
    else
        result=$( cd "${TREE}" && patch -p1 --ignore-whitespace --force \
                  < "${PATCH_DIR}/${pt}" 2>&1 )
        echo "$result" >> "$out"
        failed=$(echo "$result" | grep -c 'FAILED')
        total=$(grep -c '^@@' "${PATCH_DIR}/${pt}")
        if [ "$failed" -gt 0 ]; then
            echo "FAILED (${failed}/${total} hunks)"; rc=1
        else
            echo "applied (${total} hunks)"
        fi
    fi
done

if [ "$rc" -ne 0 ]; then
    echo
    echo "Failures by file:"
    awk '/^patching file|^checking file/ { f=$3 }
         /FAILED/ { c[f]++ }
         END { for (x in c) printf "  %4d  %s\n", c[x], x }' "$out" | sort -rn
    echo
    echo "Rejects are in ${TREE} as *.rej"
    echo "Full log: ${out}"
fi

echo
if [ "$rc" -eq 0 ]; then
    echo "PASS: the ${DEVICE} series applies cleanly to ${VERSION}"
else
    echo "FAIL: the ${DEVICE} series does not apply cleanly to ${VERSION}"
fi
exit $rc
