#!/bin/bash
#
# Build a flashable OSMC image for the Raspberry Pi 2B from this fork,
# with a selectable kernel.
#
#   sudo ./tools/build-rbp2-image.sh stock     # current kernel (5.15.92)
#   sudo ./tools/build-rbp2-image.sh 5.15.98   # proto/kernel-5.15.98
#   sudo ./tools/build-rbp2-image.sh 6.12      # proto/kernel-6.12-rpi
#
# Run the stages in that order. Each one changes exactly one variable, so a
# failure tells you which change caused it. Do not skip to 6.12: if it
# fails you will not know whether the kernel or the fork's own changes are
# at fault.
#
# Requires: Debian or Ubuntu, root, ~25GB free, and network access to
# deb.debian.org, apt.osmc.tv, download.osmc.tv and buildroot.org.

set -eu

STAGE="${1:?usage: $0 stock|5.15.98|6.12}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEVICE="rbp2"
OUT="${REPO}/out"

case "$STAGE" in
    stock)   BRANCH="" ;;
    5.15.98) BRANCH="proto/kernel-5.15.98" ;;
    6.12)    BRANCH="proto/kernel-6.12-rpi" ;;
    *) echo "unknown stage: $STAGE" >&2; exit 1 ;;
esac

if [ "$(id -u)" -ne 0 ]; then echo "must run as root" >&2; exit 1; fi
if ! grep -q debian /etc/os-release; then
    echo "must run on Debian or Ubuntu - the build scripts check for it" >&2; exit 1
fi

mkdir -p "$OUT"
echo "=============================================="
echo " OSMC rbp2 image build - stage: ${STAGE}"
echo " kernel branch: ${BRANCH:-<master, stock 5.15.92>}"
echo "=============================================="

# ---------------------------------------------------------------- kernel
if [ -n "$BRANCH" ]; then
    echo
    echo "--> switching kernel-osmc to ${BRANCH}"
    git -C "$REPO" fetch origin "$BRANCH"
    # Take only the kernel package and its patches from the branch, so the
    # rest of the tree stays on master and the kernel is the only variable.
    git -C "$REPO" checkout "origin/${BRANCH}" -- package/kernel-osmc
fi

echo
echo "--> building kernel-osmc (${DEVICE})"
( cd "${REPO}/package/kernel-osmc" && ./build.sh "$DEVICE" )
cp "${REPO}/package/kernel-osmc/"*.deb "$OUT/" 2>/dev/null || true

# -------------------------------------------------------------- packages
echo
echo "--> building this fork's changed packages"
for pkg in base-files-osmc mediacenter-addon-osmc \
           rbp-bootloader-osmc rbp1-device-osmc rbp2-device-osmc rbp4-device-osmc; do
    echo "    ${pkg}"
    ( cd "${REPO}/package/${pkg}" && ./build.sh >/dev/null )
    cp "${REPO}/package/${pkg}/"*.deb "$OUT/" 2>/dev/null || true
done
echo "    built: $(ls -1 "$OUT"/*.deb 2>/dev/null | wc -l) packages"

# ------------------------------------------------------------ filesystem
echo
echo "--> building root filesystem (debootstrap + apt.osmc.tv)"
echo "    this pulls prebuilt OSMC packages, then installs ours over them"

FSDIR="${REPO}/filesystem/osmc-${DEVICE}-filesystem"

# Install our packages into the chroot after the apt pass (so nothing pulls
# the upstream versions back over the top) and before setup_osmc_user (so
# postinst scripts still see a clean state).
if ! grep -q "rocksolid packages" "${FSDIR}/build.sh"; then
    python3 - "$FSDIR" "$OUT" <<'PYEOF'
import io, sys
fsdir, out = sys.argv[1], sys.argv[2]
p = fsdir + "/build.sh"
s = io.open(p).read()
anchor = "setup_osmc_user ${DIR}"
assert anchor in s, "anchor 'setup_osmc_user ${DIR}' not found in " + p
inject = (
    'echo -e "\t* Installing rocksolid packages"\n'
    'mkdir -p ${DIR}/tmp/rocksolid\n'
    'cp ' + out + '/*.deb ${DIR}/tmp/rocksolid/\n'
    'chroot ${DIR} dpkg -i /tmp/rocksolid/*.deb\n'
    'verify_action\n'
    'rm -rf ${DIR}/tmp/rocksolid\n'
)
s = s.replace(anchor, inject + anchor, 1)
io.open(p, "w").write(s)
print("    injected package install into", p)
PYEOF
fi

( cd "$FSDIR" && ./build.sh )
TARBALL=$(ls -t "${FSDIR}/../osmc-${DEVICE}-filesystem-"*.tar.xz 2>/dev/null | head -1)
[ -n "$TARBALL" ] || { echo "no filesystem tarball produced" >&2; exit 1; }
echo "    filesystem: $(basename "$TARBALL") ($(du -h "$TARBALL" | cut -f1))"

# ------------------------------------------------------------- installer
echo
echo "--> building target installer image"
cp "$TARBALL" "${REPO}/installer/target/filesystem.tar.xz"
( cd "${REPO}/installer/target" && ./build.sh "$DEVICE" )

IMG=$(ls -t "${REPO}/installer/target/buildroot-"*/output/images/OSMC_TGT_${DEVICE}_*.img.gz 2>/dev/null | head -1)
[ -n "$IMG" ] || { echo "no image produced" >&2; exit 1; }
cp "$IMG" "${OUT}/OSMC_TGT_${DEVICE}_${STAGE}.img.gz"

echo
echo "=============================================="
echo " DONE - ${OUT}/OSMC_TGT_${DEVICE}_${STAGE}.img.gz"
echo "        $(du -h "${OUT}/OSMC_TGT_${DEVICE}_${STAGE}.img.gz" | cut -f1)"
echo "=============================================="
echo
echo "Flash with:  gunzip -c <image> | sudo dd of=/dev/sdX bs=4M status=progress conv=fsync"
echo "Check lsblk first - /dev/sdX must be the whole card, not a partition."
