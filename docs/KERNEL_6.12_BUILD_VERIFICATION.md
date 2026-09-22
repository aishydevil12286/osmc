# rpi-6.12.y build verification (rbp2 / Raspberry Pi 2B)

Record of the cross-compile of `proto/kernel-6.12-rpi`. This covers what
was verified and, just as importantly, what was **not**.

## Result: compiles clean

```
Toolchain : arm-linux-gnueabihf-gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0
Command   : make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- -j4 zImage modules dtbs
Source    : raspberrypi/linux rpi-6.12.y @ 6.12.110 + this branch's patch series
Errors    : 0
Warnings  : 0 fatal

zImage    : 9.6M   (Linux version 6.12.110+ SMP PREEMPT)
modules   : 1606 .ko
dtbs      : 36, including bcm2836-rpi-2-b.dtb (21K) and bcm2709-rpi-2-b.dtb
```

The full patch series applied to a pristine checkout first — see the commit
message on this branch for the per-patch results.

## Device-critical drivers, verified as built artefacts

Not "the config says y" — these are files that exist after the build.

| Driver | Purpose | Built as |
|---|---|---|
| `vc4.ko` (496K) | HDMI / display | module |
| `snd-bcm2835.ko` (31K) | audio | module |
| `bluetooth.ko` (754K) | Bluetooth | module |
| `dwc2.ko` (227K) | Pi 2B USB controller | module |
| `brcmfmac.ko` (466K) | WiFi | module |
| `CONFIG_USB_NET_SMSC95XX` | Pi 2B ethernet | built-in (`=y`) |
| `CONFIG_MMC_BCM2835_SDHOST` | SD card — needed to boot | built-in (`=y`) |

## NOT verified

**This kernel has never been booted.** A clean cross-compile proves the
source is consistent and the config is coherent. It does not prove the
device boots, that HDMI syncs, that audio works, or that the SD controller
initialises on real silicon. Treat this as "ready to test", not "stable".

Also untested:

- The OSMC packaging path. This was a bare `make`, not
  `package/kernel-osmc/build.sh`, so the initramfs build, module signing,
  and the `.deb` assembly have not run.
- `rbp2-headers-sanitised`, `rbp2-mesa-osmc` and `rbp-userland-osmc` are
  version-coupled to the kernel and have not been rebuilt against 6.12.
- `SOURCE_LINUX` points at the **branch head**, which is not reproducible.
  Pin a `stable_YYYYMMDD` tag before merging.

## Findings

**`rbp-030-add-mt7610u.patch` is dead weight.** It adds an out-of-tree
driver at `drivers/net/wireless/mt7610u/` (7.7MB, 265 hunks) gated on
`CONFIG_MT7650`. That symbol is set in neither the 5.15 config nor the
migrated 6.12 one, so the driver has never been compiled on either. This
predates the 6.12 work — it was already dead on 5.15. Candidate for
removal, or for enabling if the hardware is actually supposed to be
supported.

**`lz4` is a host build dependency.** The first build attempt failed at
`arch/arm/boot/compressed/piggy_data` with `/bin/sh: 1: lz4: not found`.
Both the 5.15 and 6.12 configs set `CONFIG_KERNEL_LZ4`. This is not a gap
in the OSMC build — `build.sh:88` already does `handle_dep "liblz4-tool"`
— it only bites outside that environment.

## Reproducing

```sh
git clone --depth 1 --branch rpi-6.12.y https://github.com/raspberrypi/linux
cd linux
for p in /path/to/osmc/package/kernel-osmc/patches/rbp-*.patch \
         /path/to/osmc/package/kernel-osmc/patches/rbp2-*.patch; do
    patch -p1 --ignore-whitespace < "$p"
done
make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- olddefconfig
make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- -j$(nproc) zImage modules dtbs
```

Needs `gcc-arm-linux-gnueabihf`, `lz4`, `bison`, `flex`, `libssl-dev`, `bc`.

## Next step

Boot test on a Pi 2B. Until that passes, this branch is not mergeable.
