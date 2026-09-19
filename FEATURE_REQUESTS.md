# Feature Requests

Tracked here instead of GitHub Issues, which are disabled on this fork.

---

## Add a real unit-test layer for the Python addon codebase

**Status:** Not started
**Related:** #1 (critical fixes), #2 (major fixes), #3 (security hardening)

### Summary

Add proper, executing unit-test coverage across the Python addon codebase
(`package/mediacenter-addon-osmc/src/**`), replacing/augmenting the static
source-text regression checks currently in `tests/test_critical_fixes.py`,
`tests/test_major_fixes.py`, and `tests/test_security_hardening.py`.

Those three files verify fixes by asserting patterns in the source text
rather than executing the code, because almost every addon module imports
`xbmc`/`xbmcgui`/`xbmcaddon`/`xbmcvfs`/`dbus`/`apt` at import time (directly
or transitively), none of which are installable outside a real OSMC/Kodi
runtime. A proper test layer needs lightweight stub/mock modules for these
so real imports succeed and real functions can be exercised.

### Scope assessment (from a full-codebase scan)

- **82 Python lib modules, ~17,300 lines**, across 9 addons (excludes the
  vendored `xmltodict` library).
- **31 of 82 files** import `xbmc`/`xbmcgui`/`xbmcaddon`/`dbus`/`apt`
  directly; most of the remaining 51 import a sibling module that does, so
  nearly all 82 are currently unimportable standalone.
- Also **39 C++ files** in `installer/host/qt_host_installer` +
  `installer/target/qt_target/qt_target_installer` — real unit testing
  there needs a separate Qt test harness (QTest/Catch2 + CMake), out of
  scope for this item.

### Proposed test-layer components

1. Lightweight stub packages for `xbmc`, `xbmcgui`, `xbmcaddon`, `xbmcvfs`,
   `dbus`, `apt` (~6 files) so real imports succeed against fakes instead
   of crashing.
2. Shared `conftest.py` / bootstrap wiring the stubs into `sys.path`
   before test collection.
3. `pytest.ini` / runner config.
4. One `test_*.py` per addon module worth covering.

### Coverage tiers

- **Tier 1 — pure logic, no mocking needed** (~15 modules, ~60-80 tests):
  `grablogs.py` masking, `osmc_language.py`, scheduler math,
  string/config parsers, preseeder-style data builders.
- **Tier 2 — logic behind an xbmc/dbus/apt boundary, testable with stubs**
  (~30 modules, ~120-180 tests): `osmc_network.py`, `apt_cache_action.py`,
  `osmc_backups.py`, `services_gui.py` business logic, `apf_store.py`,
  bluetooth/wireless agents — the exact files where real bugs have already
  been found and fixed (#1, #2, #3).
- **Tier 3 — GUI event-handler classes** (~35 modules): `WindowXMLDialog`
  subclasses, mostly widget-wiring with little independent logic. Full
  coverage requires simulating Kodi's control/focus system (high effort,
  lower bug-catching value); thin smoke tests recommended over full
  coverage.

### Estimate

| Item | Count |
|---|---|
| New stub/mock modules | ~6 files |
| Test harness (conftest.py, pytest.ini) | 2 files |
| New test_*.py files | ~45-55 (Tier 1+2) up to ~80 (+ Tier 3) |
| New test cases | ~200 (Tier 1+2) up to ~300+ (+ Tier 3) |

### Suggested delivery

Phased, one branch/PR per addon or logical group (e.g. `osmccommon` +
`updates` first since real bugs were already found there, then
`networking`, `services`, `apfstore`, etc.) rather than one large PR -
mirrors how the critical/major/security fix batches were delivered
(#1, #2, #3).

---

## Move cross-process `/tmp` coordination to `/run/osmc/`

**Status:** Phases 1 and 2 done; phase 3 (drop the fallbacks) outstanding
**Blocks:** `PrivateTmp=true` on `mediacenter.service` (Kodi)

`/tmp` and `/var/tmp` are used as a cross-process coordination medium,
which prevents sandboxing Kodi with `PrivateTmp`. `PrivateTmp=true` gives
a unit a private `/tmp` **and** `/var/tmp`, so setting it on
`mediacenter.service` severs anything coordinating through either.

| Path | Used by | Status |
|---|---|---|
| `/tmp/reboot-needed` | 15 package maintainer scripts (root) → addon | **done** |
| `/tmp/NO_UPDATE` | `ftr` first-run scripts (root) → addon | **done** |
| `/var/tmp/osmc.settings.sockfile` | Unix socket, addon ↔ settings service | **done** |
| `/var/tmp/osmc.settings.update.sockfile` | Unix socket, addon ↔ update daemon | **done** |
| `/var/tmp/.suppress_osmc_update_checks` | addon (as `osmc`) ↔ `apt_cache_action.py` (as root) | **done** |
| `/var/tmp/.osmc_failed_update` | read/removed by the addon | **done** |

Phase 1 covered the flags written by *other* packages, via
`/usr/bin/osmc-runtime-flag`, which mirrors each flag to its legacy `/tmp`
path while pre-upgrade addon code may still be loaded in the running Kodi
process.

Phase 2 covered the four `/var/tmp` paths. Canonical locations live in
`osmccommon/osmc_paths.py`; servers bind via `preferred()` and clients try
`/run/osmc` then the legacy path. `apt_cache_action.py`, `call_parent.py`
and `call_osmc_parent.py` are launched as bare `python3 <script>` with no
Kodi addon `sys.path`, so they carry their own copies of the path lists —
a drift test keeps those in step.

### Phase 3: remove the fallbacks — and only then set `PrivateTmp`

**The compatibility fallbacks and `PrivateTmp` are mutually exclusive.**
This is the constraint that decides the sequencing, and it is easy to miss:

- If `/run/osmc` is not usable, a server falls back to binding
  `/var/tmp/...`.
- Under `PrivateTmp=true` that `/var/tmp` is **private to the unit**, so a
  helper in a different process would connect to its own empty namespace.
- IPC would fail silently rather than loudly — the worst failure shape.

So `PrivateTmp=true` must not be set while any fallback remains. The order
has to be:

1. Phases 1 and 2 ship, with fallbacks. *(done)*
2. One release passes so every reader and writer is on `/run/osmc`.
3. Remove the legacy entries from `osmc_paths.py`, the standalone scripts'
   copies, `osmc-runtime-flag`'s `legacy_path()`, and `REBOOT_REQUIRED_FILES`.
4. Only then add `PrivateTmp=true` to `mediacenter.service`.

Step 4 also needs a packaging guard: `/run/osmc` is created by
**base-files-osmc**, the addons ship in **mediacenter-addon-osmc**, and the
unit file ships in **mediacenter-osmc** — three packages that upgrade
independently. Without the fallback to absorb version skew, `mediacenter-osmc`
should declare a versioned dependency on `base-files-osmc` before enabling
the sandbox.

Once done, this would have made the WiFi passphrase leak fixed in #3
unexploitable by anything outside Kodi.

Two reasons to do this beyond enabling the sandbox:

1. `/tmp` and `/var/tmp` are world-writable, so those sockets are exposed
   to squatting and symlink attacks by any local process.
2. `/run` is the correct location for runtime state. `/run/osmc/` owned
   `0770 root:osmc` fixes both problems at once. Group-write is required,
   not merely convenient: `.suppress_osmc_update_checks` is written by the
   addon as `osmc` and removed by `apt_cache_action.py` as root.

Deliberately left alone:

- `/tmp/.reboot-needed` (note the leading dot) in `networking_gui.py` is
  written *and* read only within Kodi, so it survives `PrivateTmp`. It is
  confusingly named next to `/tmp/reboot-needed` but is a different flag.
- `/tmp/kernel-updated`, written by `kernel-osmc`'s postinst, has no reader
  anywhere in this repository. It may have an out-of-tree consumer, so it
  was not migrated on a guess.
- Paths written and then `sudo mv`-ed by the same process tree
  (`/tmp/fstab`, `/tmp/guisettings.xml`, `/tmp/end_of_life_message`, …) are
  not cross-process: a child process inherits the private namespace, so
  they work unchanged under `PrivateTmp`.

Note `NoNewPrivileges=true` on `mediacenter.service` remains blocked on
something larger — it would break every `sudo` call the settings addons
make. That one needs the privilege split described in
`docs/ARCHITECTURE_REWRITE.md`, not just a path change.

---

## Other feature ideas from the security/stability review

Not yet scoped in detail; noted here for later triage.

- **Atomic + scoped temp files sweep** — `wifi_connect`'s fix (#3) is the
  template; audit the rest of the codebase for similar fixed/predictable
  `/tmp` paths.
- **Background/non-blocking apt updates** (osmc/osmc#622) — update checks
  currently block the checking thread; move package download to idle time.
- **grab-logs log rotation** — now that masking works correctly (#1),
  cap/rotate `/var/tmp/uploadlog.txt` and Kodi logs to avoid unbounded
  growth on constrained storage (SD cards/eMMC).
- **Watchdog/auto-restart for Kodi crashes** — no supervised-restart
  mechanism found for the Kodi process; a systemd `Restart=on-failure` +
  backoff would improve stability on HDMI/driver crashes without a manual
  power cycle.
- **`os.system`/`os.popen` → `subprocess` sweep** — beyond the one fixed
  in #3, ~10 other call sites (`osmc_walkthru.py`, `osmc_hotfix.py`,
  `service_entry.py`, `apf_store.py`) use the same lower-risk-today but
  injection-prone pattern; standardizing on `subprocess.call([...])`
  repo-wide closes off this whole class of future bugs.
