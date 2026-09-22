# Building an OSMC installer for the Raspberry Pi 2B

How to go from a fresh clone of this repository to a flashable
`OSMC_TGT_rbp2_<date>.img.gz`, on a Linux host, including this fork's own
packages.

OSMC's device code for the Pi 2B is **`rbp2`**. The same image also covers
the Pi 3 and 3B+ — `rbp2` is the armv7 target, and the build keeps the
`bcm2836`/`bcm2837` device trees, stripping only `bcm2711` (Pi 4). It does
**not** cover the Pi 1, Pi Zero (`rbp1`) or Pi 4 (`rbp4`).

The upstream wiki page this replaces
(`osmc.tv/wiki/development/building-installers`) is gone; its parent,
[OSMC Build Architecture Overview](https://osmc.tv/wiki/development/osmc-build-architecture-overview/),
still exists but only says that host and target installers are different
things.

---

## The one thing to understand first

The installer image does **not** contain OSMC.

It is a 320 MB FAT32 image holding a small Buildroot-built installer
environment plus a single file, `filesystem.tar.xz`, which is the real
Debian armhf root filesystem. You flash the installer to an SD card, boot
the Pi from it, and the installer unpacks that tarball onto the card.

So there are two independent builds:

| | Produces | Time | Needs |
|---|---|---|---|
| **Filesystem** | `osmc-rbp2-filesystem-<date>.tar.xz` (~400 MB) | 20–40 min | debootstrap, qemu-user-static, binfmt |
| **Target installer** | `OSMC_TGT_rbp2_<date>.img.gz` | 40–90 min | Buildroot toolchain, kpartx, parted, root |

You do **not** have to compile Kodi. The filesystem build installs
*prebuilt* OSMC packages from `apt.osmc.tv`; it never builds them from
source. That is also why injecting this fork's packages is easy — see
[Building with this fork's packages](#building-with-this-forks-packages).

You can also skip the filesystem build entirely and use OSMC's published
tarball — see [Shortcut](#shortcut-use-osmcs-published-filesystem).

---

## Host requirements

- **Debian or Ubuntu.** `scripts/common.sh:check_platform()` greps
  `/etc/os-release` for `debian` and aborts otherwise. Ubuntu passes
  (`ID_LIKE=debian`); Fedora and Arch do not.
- **Root.** Both builds `chroot`, `mount`, and use `kpartx` on loop
  devices. Neither script elevates itself — they assume they are already
  running as root — so use `sudo -i` or prefix each invocation with `sudo`.
  (The per-package `Makefile`s differ: their `all` target already runs
  `sudo bash build.sh`.)
- **x86_64 is fine.** ARM code runs under `qemu-user-static` via `binfmt_misc`.
- **~25 GB free** and a real filesystem. Buildroot will not build on a
  path containing spaces, and `/tmp` on tmpfs will run out.
- The build scripts `apt-get install` their own dependencies as they go.

```sh
sudo -i
apt-get update
apt-get install -y git wget xz-utils
git clone https://github.com/aishydevil12286/osmc.git
cd osmc
```

---

## Step 1 — Build the root filesystem

```sh
cd filesystem/osmc-rbp2-filesystem
./build.sh
```

What it does (`filesystem/osmc-rbp2-filesystem/build.sh`):

1. Installs `debootstrap`, `qemu`, `binfmt-support`, `qemu-user-static`.
2. `debootstrap --foreign --variant=minbase --arch=armhf bullseye` — stage
   one only, since the host cannot execute ARM binaries yet.
3. Copies `qemu-arm-static` into the chroot (`emulate_arm`), then runs
   `/debootstrap/debootstrap --second-stage` inside it.
4. Writes `/etc/apt/sources.list` pointing at `deb.debian.org` for bullseye
   plus `apt.osmc.tv` for `bullseye` and `bullseye-security`, and adds the
   OSMC repository GPG key.
5. Installs the OSMC stack, in this order — `rbp-userland-osmc` **first**,
   because the kernel package's postinst rules depend on it:

   ```
   rbp-userland-osmc
   rbp2-device-osmc      # the Pi 2 metapackage: kernel, mediacenter, network, remote, splash, bootloader…
   ssh-app-osmc
   cron-app-osmc
   usrmerge
   ```
6. Configures the image: creates the `osmc` user (password `osmc`, locked
   root, passwordless sudo), hostname, hosts, fstab, TTYs, BusyBox
   symlinks, legacy ELF support, `rc.local`, legacy iptables, release info.
7. Sets `/etc/ld.so.preload` to `/usr/lib/libarmmem.so` — this is done
   *after* qemu is removed, because it breaks qemu.
8. `create_fs_tarball` → `tar -cf - * | xz -9` →
   `osmc-rbp2-filesystem-<date>.tar.xz` plus a `.md5`.

Output lands in `filesystem/osmc-rbp2-filesystem/`, one level up from the
chroot directory.

> **Bullseye is EOL (2026-08-31).** Upstream already works around the
> `bullseye-security` pool being reaped by pointing at `apt.osmc.tv`
> instead — see the comment in the build script. The main `bullseye` suite
> on `deb.debian.org` may also disappear into `archive.debian.org` in time.
> If `debootstrap` fails to fetch `Release`, export a mirror before
> building; the script passes `${URL}` to debootstrap and leaves it unset
> by default:
>
> ```sh
> export URL=http://archive.debian.org/debian
> ```

---

## Step 2 — Build the target installer

```sh
cd installer/target
./build.sh rbp2
```

What it does (`installer/target/build.sh`):

1. Installs `build-essential rsync texinfo libncurses5-dev whois bc kpartx
   dosfstools parted cpio python3 python-is-python3 bison flex libssl-dev`.
2. Downloads **Buildroot 2018.11** from `buildroot.org`.
3. Applies patches from `installer/target/patches/` by prefix, in sorted
   order — `all-*` (10 patches: the OSMC package, BusyBox XZ, Qt fixes,
   fakeroot/m4/CMake bumps), then `rbp-*` (Raspberry Pi kernel, firmware,
   device tree and overlays), then `rbp2-*` (kernel config and device
   defconfig).
4. Swaps Buildroot's `rpi-firmware` for OSMC's `rpi-firmware-osmc`, and
   writes `cmdline.txt`:

   ```
   dwc_otg.fiq_fix_enable=1 sdhci-bcm2708.sync_after_dma=0 dwc_otg.lpm_enable=0 \
   console=tty1 root=/dev/ram0 quiet init=/init loglevel=2 osmcdev=rbp2
   ```
5. `make osmc_defconfig || make osmc_defconfig` — retried once on failure,
   working around a Buildroot 2018.x bug — then `make`. This is the long part.
6. Finds the filesystem tarball:
   - if `installer/target/filesystem.tar.xz` exists, it uses it;
   - otherwise it walks back up to **150 days** looking for
     `https://download.osmc.tv/filesystems/osmc-rbp2-filesystem-<date>.tar.xz`.
7. Builds the image: a 320 MB sparse file, msdos label, one FAT32
   partition labelled `OSMCInstall`, via `parted` + `kpartx` +
   `mkfs.vfat`, mounted at `/mnt`.
8. Copies in `zImage` → `kernel.img`, `INSTALLER/*`, `*.dtb`, `overlays`,
   removes the `bcm2711` (Pi 4) device trees, then drops in
   `filesystem.tar.xz`.
9. `gzip` + `md5sum` → `OSMC_TGT_rbp2_<date>.img.gz`.

Output lands in
`installer/target/buildroot-2018.11/output/images/`.

> `build.sh` mounts at `/mnt` unconditionally. Do not run it on a machine
> with something already mounted there.

---

## Step 3 — Flash and install

```sh
gunzip OSMC_TGT_rbp2_<date>.img.gz
sudo dd if=OSMC_TGT_rbp2_<date>.img of=/dev/sdX bs=4M status=progress conv=fsync
```

Check `lsblk` first — `/dev/sdX` must be the whole card, not a partition.

Boot the Pi from it. The installer runs, unpacks `filesystem.tar.xz` onto
the card, and reboots into OSMC. Default login is `osmc` / `osmc`.

---

## Building with this fork's packages

The filesystem build installs OSMC's *prebuilt* packages, so this fork's
changes are not in a stock build. Two ways to get them in.

### Option A — inject during the filesystem build (recommended)

Build the packages first:

```sh
cd package/mediacenter-addon-osmc && ./build.sh && cd -
cd package/base-files-osmc        && ./build.sh && cd -
cd package/rbp-bootloader-osmc    && ./build.sh && cd -
cd package/rbp2-device-osmc       && ./build.sh && cd -
```

Then, in `filesystem/osmc-rbp2-filesystem/build.sh`, after the last
`apt-get install` line (the `usrmerge` one) and **before** `setup_osmc_user`,
add:

```sh
echo -e "	* Installing rocksolid packages"
mkdir -p ${DIR}/tmp/rocksolid
cp ${wd}/../../package/*/*.deb ${DIR}/tmp/rocksolid/
chroot ${DIR} dpkg -i /tmp/rocksolid/*.deb
verify_action
rm -rf ${DIR}/tmp/rocksolid
```

This is the right insertion point: apt has finished, so nothing will pull
the upstream versions back over the top, and `setup_osmc_user` has not yet
run, so package postinst scripts still see a clean state.

### Option B — patch a prebuilt filesystem

See [Shortcut](#shortcut-use-osmcs-published-filesystem) below.

### Why an image build, not just `dpkg -i` on a running box

One fix only exists in an image build. `filesystem/common/funcs.sh:19`
writes `/etc/sudoers.d/osmc-secure-path` during `setup_osmc_user`. That is
a filesystem-build action, not a package action, so it cannot ship in any
`.deb` — installing this fork's packages onto a running OSMC system gets
everything in `#1` *except* the `secure_path` hardening. Building the image
is the only way to get parity.

---

## Shortcut — use OSMC's published filesystem

Skips `debootstrap` and the whole apt install pass. Around 10 minutes
instead of 40, at the cost of the `secure_path` fix noted above (add it by
hand in the chroot if you want it).

```sh
# pick a recent date from https://download.osmc.tv/filesystems/
wget https://download.osmc.tv/filesystems/osmc-rbp2-filesystem-<date>.tar.xz

mkdir rootfs && tar xf osmc-rbp2-filesystem-<date>.tar.xz -C rootfs
cp /usr/bin/qemu-arm-static rootfs/usr/bin/

cp package/*/*.deb rootfs/tmp/
chroot rootfs dpkg -i /tmp/*.deb
rm rootfs/tmp/*.deb rootfs/usr/bin/qemu-arm-static

cd rootfs && tar -cf - * | xz -9 -c - > ../installer/target/filesystem.tar.xz && cd ..

cd installer/target && ./build.sh rbp2
```

`build.sh` checks `../../../filesystem.tar.xz` from inside
`installer/target/buildroot-2018.11/output/images/`, which resolves to
**`installer/target/filesystem.tar.xz`** — beside `build.sh` itself, not in
`installer/`.

---

## Host installer (optional)

`installer/target` produces the image. `installer/host` is the separate Qt
desktop application that writes an image to a card — you do not need it if
you are using `dd`.

- `make win` — Qt 4.8 static via MinGW32; also needs the Windows 7 SDK for
  the `requireAdministrator` manifest.
- `make osx` — Qt 5.15.2 static DMG.
- `make obs` — source tarball for the OpenSUSE Build Service, which is how
  the Debian and RPM builds are produced.

`installer/host/README.md` has the full Qt static-build recipes and is
more detailed than the wiki page ever was.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Build aborts immediately | Not Debian/Ubuntu — `check_platform()` greps `/etc/os-release` for `debian` |
| `debootstrap` cannot fetch `Release` | Bullseye is EOL; set `URL=http://archive.debian.org/debian` |
| `Exec format error` in the chroot | `binfmt_misc` not registered — `update-binfmts --enable qemu-arm` |
| `No filesystem available for target` | No local `installer/target/filesystem.tar.xz` and nothing found on download.osmc.tv within 150 days |
| `mkfs.vfat` on the wrong device | Something was already mounted at `/mnt` |
| Buildroot fails on `configure` | The script exports `FORCE_UNSAFE_CONFIGURE=1` for running as root; do not unset it |
| Image boots but installer stalls | `filesystem.tar.xz` truncated — check it against the `.md5` |
