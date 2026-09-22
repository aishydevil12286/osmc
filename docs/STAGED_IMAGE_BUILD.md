# Staged image build for the Raspberry Pi 2B

Three images, changing one variable at a time, so a failure tells you what
caused it.

| Stage | Kernel | What it proves |
|---|---|---|
| 1 | 5.15.92 (stock, master) | The fork's own changes work in a real image |
| 2 | 5.15.98 (`proto/kernel-5.15.98`) | A kernel bump works through the packaging path |
| 3 | 6.12.110 (`proto/kernel-6.12-rpi`) | The Pi tree move works |

```sh
sudo ./tools/build-rbp2-image.sh stock
sudo ./tools/build-rbp2-image.sh 5.15.98
sudo ./tools/build-rbp2-image.sh 6.12
```

Run them in order. Stage 1 first is the important part: if you go straight
to stage 3 and it fails, you cannot tell whether the kernel or the fork's
changes broke it.

The script checks out **only `package/kernel-osmc`** from the kernel
branch, leaving the rest of the tree on master. The kernel really is the
only variable between stages.

## What each stage does

1. Builds `kernel-osmc` for `rbp2`.
2. Builds the fork's six changed packages.
3. Builds the root filesystem — `debootstrap` of Debian bullseye armhf,
   then OSMC's prebuilt packages from `apt.osmc.tv`, then **ours installed
   over the top**, injected after the apt pass (so nothing pulls upstream
   versions back) and before `setup_osmc_user` (so postinst scripts see a
   clean state).
4. Feeds that tarball to `installer/target/build.sh`, which produces
   `OSMC_TGT_rbp2_<stage>.img.gz` in `out/`.

## Requirements

Debian or Ubuntu, **root**, ~25GB free, and network access to
`deb.debian.org`, `apt.osmc.tv`, `download.osmc.tv` and `buildroot.org`.

Expect 1–2 hours per stage, mostly Buildroot.

## What to check on each image

Flash, boot, and confirm in this order — each depends on the last:

1. **Boots at all** — SD controller and initramfs.
2. **HDMI output** — `vc4`.
3. **Ethernet** — `smsc95xx`; `ip a` shows `eth0` with an address.
4. **Kodi starts** — this is the one to watch on stage 3. Kodi links
   against `rbp2-mesa-osmc` built for 5.15 headers.
5. **My OSMC settings open**, and WiFi/Bluetooth panels work — this fork
   changed that code heavily.
6. **Audio plays**.

If stage 3 boots with working HDMI and ethernet but Kodi fails, that
confirms the headers/mesa/userland rebuild is the next piece of work, not a
kernel problem.

## Already verified

Both candidate kernels cross-compile clean. This does **not** mean they
boot — see `docs/KERNEL_6.12_BUILD_VERIFICATION.md`.

| | 5.15.98 | 6.12.110 |
|---|---|---|
| errors | 0 | 0 |
| zImage | 8.3M | 9.6M |
| modules | 1630 | 1606 |
| dtbs | 32 | 36 |
| `vc4.ko` | 407K | 496K |
| `snd-bcm2835.ko` | 32K | 31K |
| `bluetooth.ko` | 563K | 754K |
| `USB_NET_SMSC95XX` | `=y` | `=y` |
| `MMC_BCM2835_SDHOST` | `=y` | `=y` |

Note the Pi 2B USB controller differs: 5.15 builds the downstream
`dwc_otg` driver (15 objects), 6.12 uses mainline `dwc2.ko`. That is a real
behavioural difference and a thing to watch on stage 3 — USB storage, USB
audio and USB ethernet all sit behind it on a Pi 2B.

## Known rough edge

`SOURCE_LINUX` on `proto/kernel-6.12-rpi` points at the `rpi-6.12.y`
**branch head**, so two builds on different days can produce different
kernels. Pin a `stable_YYYYMMDD` tag before treating stage 3 as
reproducible.
