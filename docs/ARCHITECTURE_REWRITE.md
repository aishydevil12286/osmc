# Architecture: Go rewrite + RetroArch integration

Status: **Proposal / not started**
Related: #1 (critical fixes), #2 (major fixes), #3 (security hardening), `FEATURE_REQUESTS.md`

---

## 1. Thesis

The organizing principle of this rewrite is **a privilege boundary, not a language swap.**

Every security defect found in this codebase during the #1/#3 review traces back to
one design decision:

```sh
# filesystem/common/funcs.sh
echo "osmc     ALL= NOPASSWD: ALL" >${1}/etc/sudoers.d/osmc-no-sudo-password
```

The presentation layer — Kodi addon code, written in a dynamic language, running as
the same user that arbitrary third-party Kodi addons run as — holds unrestricted
root. Given that, defects escalate automatically:

| Defect | What it was | Why it reached root |
|---|---|---|
| #422 | Broken credential-masking regex | Log tool ran privileged, collected privileged files |
| #319 | `Defaults !secure_path` | sudo inherited the GUI process's `PATH` |
| #3 (a) | `os.system("sudo systemctl enable " + line_from_file)` | Shell string built in a process holding root |
| #3 (b) | WiFi PSK in world-readable `/tmp` | Secret materialised on disk to cross a process boundary |

The fixes shipped in #1 and #3 are patches to symptoms. This proposal removes the
*category*: the GUI gets no privileges at all, and privileged work happens behind a
typed, enumerated IPC contract with no general-purpose "run this" verb.

Go is the means. The daemon split is the end. A rewrite that moved 17,300 lines of
Python to 17,300 lines of Go without moving the privilege boundary would fix almost
nothing.

---

## 2. Current state (measured)

- **82 Python lib modules, ~17,300 lines**, across 9 Kodi addons
  (`package/mediacenter-addon-osmc/src/**`), excluding vendored `xmltodict`.
- **39 C++/Qt files** across `installer/host/qt_host_installer` (desktop SD-card
  writer) and `installer/target/qt_target/qt_target_installer` (on-device
  first-boot installer).
- **Bash + Makefiles** for image assembly (`filesystem/`, `package/`,
  `toolchains/`, `scripts/`).

The Python layer tangles three separable concerns:

1. **Privileged system operations** — apt, `systemctl`, connman/D-Bus,
   `/boot/config.txt`, assorted `sudo` shell-outs.
2. **Presentation** — Kodi `WindowXMLDialog` subclasses and skin wiring.
3. **Business logic** — update scheduling, config parsing, log redaction,
   backup/restore.

Only (2) has any reason to live inside Kodi.

---

## 3. Target architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Kodi (C++)                                                   │
│   └─ osmc-settings addon  (Python, ~2k LOC, VIEW ONLY)       │
│        • renders dialogs, collects input                     │
│        • no sudo, no shell, no root, no secrets on disk      │
└───────────────────────────┬──────────────────────────────────┘
                            │  protobuf over Unix domain socket
                            │  (0660 root:osmc)
┌───────────────────────────▼──────────────────────────────────┐
│ osmcd   (Go, systemd service, runs as root)                  │
│   net      connman/D-Bus: ethernet, wifi, tethering, NFS     │
│   update   apt orchestration, scheduling, hotfixes           │
│   svc      systemd unit enable/disable/status (D-Bus API)    │
│   pkg      app store: fetch, verify, install                 │
│   diag     log collection + redaction (grab-logs successor)  │
│   backup   config backup/restore                             │
│   hw       Pi config.txt, Vero display/EDID, hwid gating     │
│   session  ★ display / audio / input arbitration             │
└───────────────────────────┬──────────────────────────────────┘
                            │  session handoff:
                            │  DRM master, ALSA device, evdev nodes
┌───────────────────────────▼──────────────────────────────────┐
│ RetroArch (C) + libretro cores                               │
│   separate process, launched and reaped by osmcd             │
└──────────────────────────────────────────────────────────────┘
```

Supporting binaries:

| Binary | Replaces | Language |
|---|---|---|
| `osmcd` | the privileged half of 82 Python modules | Go |
| `osmc-installer` | `installer/host/qt_host_installer` | Go + Fyne |
| `osmc-firstboot` | `installer/target/qt_target/qt_target_installer` | Go (sequenced last) |
| `osmc-build` | `scripts/` + Makefile image assembly | Go CLI (optional, lowest priority) |

---

## 4. Language choice

### Go for `osmcd` and tooling

- D-Bus (`godbus/dbus`), systemd, `os/exec`, HTTP, and concurrency are all
  first-class or well-served — which is precisely the workload here.
- `GOOS=linux GOARCH=arm64 go build` produces **one static binary with no runtime
  dependencies**. Compare to today: a Python interpreter, `python3-dbus`,
  `python3-apt`, `requests`, and their transitive apt dependencies, all pinned
  against a Debian release, on an SD card. This is a material reduction in both
  image size and upgrade fragility.
- `os/exec` takes an argument slice. The shape of the API makes the #3(a)
  injection bug awkward to write rather than natural to write.
- Contributor ramp-up is low. For a volunteer-maintained project this is a
  first-order concern, not a footnote.

### Where Rust, honestly

Rust's advantage over Go is memory safety under adversarial input. In *this*
codebase that surface is small — the untrusted-media parsing lives in Kodi and
FFmpeg, not in OSMC's own code. OSMC parses `preseed.cfg`, `config.txt`,
`/proc/cmdline`, and an app manifest; Go handles these safely already.

Rust is worth it in exactly one *new* component:

- **The ROM scanner / archive + header parser** (§6). It ingests large volumes of
  untrusted third-party binary data (ROM headers, zip/7z contents, DAT files).
  That is genuine adversarial-input territory.

Everything else: Go. Adopting two languages has a real cost (CI, ARM compile
times, reviewer pool) and should be paid only where it buys something. If the
team prefers a single language, the ROM parser in Go with a hardened archive
library is an acceptable trade.

**Recommendation: Go-first; Rust only for the ROM/archive parser, and only if the
team is willing to carry a second toolchain.**

---

## 5. The IPC contract — where the security win actually lands

**Today:**
```
Python addon ──► sudo systemctl enable <line read from /etc/osmc/apps.d/*> ──► root
```
Any package that can write a file into `apps.d` gets root, because the string is
interpreted by a shell. (Fixed in #3 by switching to argument lists; the design
that made it reachable is still there.)

**Proposed:**
```
Python addon ──► EnableService{ unit: "transmission.service" } ──► osmcd
                                                                     │
                     validate against allowlist derived from apps.d manifests
                                                                     │
                                       systemd D-Bus API (no shell, no exec)
```

Non-negotiable properties:

- **Schema'd messages only.** Protobuf, or strictly-validated JSON with a
  generated schema. No free-form string is ever interpreted as a command.
- **No `RunCommand` RPC. Ever.** Every mutating operation is an enumerated verb
  with typed arguments. This is the whole point; a generic escape hatch would
  reconstitute the current design inside a nicer wrapper.
- **Socket permissions `0660 root:osmc`** — only the Kodi user can speak to it.
- **Secrets never touch disk.** The WiFi PSK travels over the socket and goes
  straight to connman via D-Bus. The `/tmp/preseed_data` class of bug (#3b)
  becomes unrepresentable rather than patched.
- **`osmc ALL= NOPASSWD: ALL` is deleted.** This is the milestone that banks the
  security win; until it is gone, the old blast radius is still there.

Prefer D-Bus APIs over shelling out to `systemctl`/`apt` binaries wherever the
API exists — it removes argument-quoting from the threat model entirely.

---

## 6. RetroArch integration

### Model: standalone RetroArch, arbitrated by `osmcd`

Kodi ships a Game API (`game.libretro`) that runs libretro cores **inside Kodi's
process** via RetroPlayer. Rejected, for two reasons:

1. Core support and performance lag standalone RetroArch meaningfully.
2. A core crash takes down the media center with it.

Standalone gives process isolation and the full RetroArch feature set. The cost
is the handoff problem — and `osmcd` is already the trusted owner of privileged
system resources, so it is the natural arbiter. This is a case where the daemon
split pays for itself twice.

### The `session` module (the hard part)

1. Ask Kodi to **suspend** and release the display. (Not `Quit` — too blunt;
   playback/library state should survive.) On DRM/KMS this means Kodi drops DRM
   master.
2. `osmcd` takes DRM master and grants it to the RetroArch process.
3. Release and re-acquire the ALSA/Pulse device.
4. Hand over evdev/joystick nodes.
   **Hard prerequisite:** the `osmc` user must be in the `input` group — which is
   exactly open upstream request [osmc/osmc#748](https://github.com/osmc/osmc/issues/748),
   triaged earlier in this repo's review as a low-priority nicety. It is a
   blocker for gaming and should be reclassified.
5. On RetroArch exit: reverse the sequence, restore Kodi's display mode and
   resume.

This sequence is **per-platform**: Amlogic (Vero) and Pi KMS differ enough that
this will need separate implementations. See §8.

### Per-device core curation

Be honest about hardware. `osmcd` should auto-curate the offered core set from
`osmcdev=` in `/proc/cmdline` — reusing the hardware-gating concept already
present in `grablogs.py` (`hwid` keys, `valid_hardware()`), which supports
generic (`vero`) and negative (`!rbp`) matches.

| Device | Realistic ceiling |
|---|---|
| Vero V (Amlogic, GLES/Vulkan) | PS1 / N64 / Dreamcast-class, shaders viable |
| Pi 4 / 400 / 500 | PS1 / N64 with caveats; shaders expensive |
| Pi 2 / 3, Vero 2 / 4K | 8- and 16-bit, handhelds |

### New subsystems

- **Packaging** — `retroarch-osmc` plus per-arch core packages, distributed via
  the existing app channel (`download.osmc.tv/apps/<osmcdev>`, as used by
  `apf_store.py`).
- **Library** — ROM scan → hash → DAT match (No-Intro / Redump) → metadata and
  artwork → SQLite. Surface games in the Kodi UI through a Kodi **Game addon**
  for browsing, while *launching* routes through `osmcd`, not RetroPlayer.
- **Saves and states** — on persistent storage, and extend the `backup` module to
  cover them. Losing save states to an SD-card reimage would be the single most
  user-visible failure mode.

---

## 7. Migration: strangler-fig, not big-bang

A big-bang rewrite of a shipping media center is how projects die. Every phase
below is independently shippable and revertible, and the Python line count
decreases monotonically.

| Phase | Scope | Why here |
|---|---|---|
| **0** | Define the protobuf contract. Ship `osmcd` with **one** module (`svc`). Repoint only `services_gui.py`. | Proves the pattern; kills the highest-severity injection path first. |
| **1** | `net` module; migrate networking. | Largest and buggiest surface (14 modules; #684, #586, #3b all live here). Deletes on-disk PSK handling. |
| **2** | `update` + `pkg`. | Also: confirm the app-install path is GPG-verified end to end (the manifest fetch in `apf_store.py` relies on HTTPS transport alone; the `.deb` path needs auditing). |
| **3** | `diag`, `backup`, `hw`. Then **delete the sudoers NOPASSWD line.** | The milestone that actually banks the security win. |
| **4** | `osmc-installer` (host) in Go + Fyne. | Independent of the daemon; can run in parallel with 1–3. |
| **5** | `session` module + RetroArch. | Ships only after the privilege boundary is real — the handoff needs a trusted arbiter to exist first. |
| **6** | `osmc-firstboot`, build system. | Optional; lowest value, highest platform-specific risk. |

The `tests/` layer described in `FEATURE_REQUESTS.md` should land **before or
alongside Phase 0** — migrating logic without executable tests means migrating
bugs silently.

---

## 8. Risks, stated plainly

1. **The display handoff is the schedule risk.** Amlogic's DRM behaviour on Vero
   differs from Pi's KMS; expect per-platform code and treat any estimate for
   Phase 5 as the softest number in this document.
2. **You do not get to zero Python.** Kodi's addon API is Python-only. The
   endpoint is ~2k lines of *unprivileged view code*, not zero. If zero Python is
   a hard requirement, the answer is dropping Kodi — a different product, not a
   refactor.
3. **Contributor churn.** A volunteer Python/bash project asked to maintain Go and
   protobuf will lose some contributors and gain others. Real cost; name it up
   front rather than discovering it at Phase 2.
4. **RetroArch enlarges the C attack surface.** This deserves emphasis given that
   reducing attack surface partly motivates the rewrite: adding RetroArch adds a
   large C codebase that parses untrusted ROMs and archives. OSMC-authored code
   gets safer while total system exposure may not improve. If this matters,
   sandbox cores (seccomp-bpf, namespaces) as part of Phase 5 rather than as a
   follow-up — and note that §4's case for Rust in the ROM parser is partly a
   response to this.
5. **Two installers, two problems.** `osmc-firstboot` runs on a framebuffer at
   first boot with no desktop toolkit available. Go's story here is weaker than
   Qt's. Sequencing it last is deliberate; keeping the Qt version indefinitely is
   a legitimate outcome.
