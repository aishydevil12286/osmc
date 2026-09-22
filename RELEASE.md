# rocksolid-1

First release of this fork. **Raspberry Pi only** — Vero paths are
deliberately not carried.

Built from commit `70217f2`, which is in sync with
`osmc/osmc` master as of 2026-09-22.

## What's in it

Ten merged pull requests, on top of a full upstream sync.

| | |
|---|---|
| #1 | Critical fixes — grab-logs credential masking (`\w` matched one character, so `davs://` and `https://` URLs were never masked), sudoers `secure_path` restored |
| #2 | Major fixes — stale static IP settings no longer block DHCP reacquisition |
| #3 | Security hardening — root command injection via `/etc/osmc/apps.d` service names; WiFi passphrase written to a predictable world-readable `/tmp` path |
| #4, #6 | Service sandboxing, docs |
| #7 | Cross-process coordination moved from `/tmp` to `/run/osmc` (phase 1) |
| #9 | IPC receive loop — messages arriving in more than one chunk were silently truncated; replaced a 5ms busy-wait with a blocking read |
| #10 | grab-logs streams to disk instead of buffering every log in memory (a `readlines()` returning a string was iterated per character, ~8x blowup) |
| #12 | Periodic TRIM, enabled only where the backing device actually reports discard support, decided at run time |
| #24 | Adaptive update-daemon poll and a wall-clock free-space check |

## Packages

```
9cd2a59c10ef32f52e42cca945a16a3a70ea08c89656d21b806682b1ab94b396  base-files-osmc_3.4.8+rocksolid1_all.deb
021f97087abbb4f2a78b9b381c7690a50c2809f2c44a69bdc60cf29ff5eb5a1c  mediacenter-addon-osmc_3.0.789+rocksolid1_all.deb
ece3f6f48bde9fbb09f18407623b3a227fb618469420949152fa1692c07753b6  rbp-bootloader-osmc_6.1.39-1+rocksolid1_armhf.deb
2dadb210c82c61b29f56e92f7bacbe65c6e05c1fd93da2ade62aea7b8ce7f855  rbp1-device-osmc_1.3.2+rocksolid1_armhf.deb
d40e83a8206225961875b4a592f77c5e728a9a53a7d59134e3234d5cda725491  rbp2-device-osmc_1.6.1+rocksolid1_armhf.deb
ca1cff9e3cb2a8207af8cc0db181446d718a6ba97970ad12dee2dfacfda21df1  rbp4-device-osmc_1.1.3+rocksolid1_armhf.deb
```

Versions carry a `+rocksolid1` suffix. It sorts above the upstream version
it derives from and **below upstream's next release**, so an OSMC update
will supersede these rather than be blocked by them.

## Installing

```sh
sudo dpkg -i *.deb
sudo systemctl restart mediacenter
```

Then, if the box still has OSMC's apt repo enabled:

```sh
sudo apt-mark hold base-files-osmc mediacenter-addon-osmc \
                   rbp-bootloader-osmc rbp1-device-osmc \
                   rbp2-device-osmc rbp4-device-osmc
```

Without the hold, the next `apt upgrade` from OSMC's repo quietly replaces
every one of these and reverts the lot.

## Not included

- **No flashable image.** These are package upgrades for an existing OSMC
  install, not an `.img.gz`.
- **The sudoers `secure_path` fix from #1 is not in any deb.** It is applied
  by `filesystem/common/funcs.sh` at image build time, so it only lands on a
  freshly built image. Everything else in #1 ships here.
- **`transmission-app-osmc` and `tvheadend-app-osmc`** carry service
  sandboxing changes but build from source and need the ARM toolchain, so
  they are not in this release.
- **`#11 / #25`** (initramfs `noatime`) stays parked pending a hardware boot
  test.
