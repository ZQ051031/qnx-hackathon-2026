# qnxport

**Catch Linux-to-QNX 8.0 porting friction before you open the SDK.** qnxport is a Linux host CLI for C/C++ developers moving software to QNX 8.0. It identifies Linux-specific APIs, porting decisions and link flags, checks QNX image buildfiles, and generates a starting layout for the actual cross-build. It does not build or boot a QNX image. This tool was written by Devin AI and is released under the MIT license.

## Install and try it

Requires Linux and Python 3.10 or newer. The installed CLI uses only the Python standard library; it does not need QNX SDP, QEMU or target hardware.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .

qnxport doctor
qnxport scan examples/linux-only --fail-on none
qnxport buildfile lint examples/demo.build
qnxport scaffold example --build-system cmake
```

From a source checkout, `python3 -m qnxport` also works without installation. See [the codelab](docs/CODELAB.md) for a start-to-finish porting exercise.

### Scan

```sh
qnxport scan /path/to/source --format markdown --fail-on none
qnxport scan examples/linux-only --relative-to . --format sarif --fail-on none > qnxport.sarif
```

The scanner covers common C/C++ source and header extensions, CMake, Make, Meson and Autoconf. Text, JSON, SARIF 2.1.0 and Markdown report the file/line/column, severity, ID, observed token, fix hint and QNX 8.0 reference. Paths are relative to the scan directory unless you specify `--relative-to` with an ancestor (useful for CI artifact locations).

Levels are **error** (known portability blocker), **warning** (likely porting change), and **note** (review semantics or configuration). `--fail-on error` is the default. `--fail-on warning` and `--fail-on note` include all higher levels; `--fail-on none` reports without failing. Exit codes: **0** below threshold or successful action, **1** threshold reached, **2** invalid arguments or input/IO error. For intentional Linux-only code, suppress a specific rule on its line or on the preceding standalone comment:

```c
// Linux backend, excluded by the QNX build:
#include <sys/epoll.h> // qnxport: ignore[QNX001]
```

| Rule | Level | What to check |
| --- | --- | --- |
| QNX001 | error | epoll → poll/select or a redesigned QNX native event loop |
| QNX002 | error | eventfd → pipe or QNX pulse, with explicit counter semantics |
| QNX003 | error | signalfd → synchronous signal waiting and an event-loop bridge |
| QNX004 | error | timerfd → POSIX timer with signal or QNX pulse |
| QNX005 | note | QNX inotify is available via libfsnotify; review event support |
| QNX006 | error | sys/prctl.h / prctl operations need per-operation mapping |
| QNX007 | error | sys/sysinfo.h → query required fields with sysconf/uname |
| QNX008 | warning | Linux-style /proc/self entries need a QNX procfs review |
| QNX009 | error | Linux/ASM UAPI headers belong in a separate backend |
| QNX010 | warning | -ldl: QNX documents dlopen in libc |
| QNX011 | warning | -lrt: QNX documents POSIX timers in libc |
| QNX012 | warning | -lpthread: QNX provides the pthread functions in libc |
| QNX013 | note | Check libsocket linkage for targets using QNX networking |
| QNX014 | note | Review a Linux platform guard without a visible QNX branch |
| QNX015 | error | affinity calls → ThreadCtl runmask, with different inheritance details |
| QNX016 | note | QNX documents gettid; don't assume Linux thread ID semantics |
| QNX017 | warning | MAP_POPULATE has Linux-specific prefault behavior |
| QNX018 | note | SO_REUSEPORT exists; check behavior and network/reuseport ability |
| QNX019 | warning | execinfo/glibc backtrace differs from the QNX debugging interfaces |
| QNX020 | note | mallinfo exists; don't assume glibc fields or mallinfo2 compatibility |

### Buildfile lint

```sh
qnxport buildfile lint examples/demo.build --fail-on warning
qnxport buildfile lint my-fragment.build --nonbootable
```

Checks follow the [QNX 8.0 mkifs reference](https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.utilities/topic/m/mkifs.html). The parser recognizes attributes, search settings, target/host assignments, bootstrap and script blocks, and inline file bodies. IFS001–IFS008 diagnose structural syntax, missing virtual boot header, missing procnto, duplicate targets, invalid uid/gid/perms, missing libc or runtime linker, and an unresolved linker path. The loader check looks for an ELF interpreter in local binaries and uses known system commands and script references as hints when SDK/BSP files aren't available. It doesn't resolve transitive library dependencies.

### Scaffold and doctor

```sh
qnxport scaffold myproject --build-system autotools
qnxport doctor --format json
```

`scaffold` supports `cmake`, `autotools` and `make`. It creates a new `ports/<project>/` with a recursive Makefile, `common.mk`, `nto-aarch64-le` and `nto-x86_64-o` variant directories, a CMake toolchain file for CMake, and build instructions. The layout follows the style of [qnx-ports/build-files](https://github.com/qnx-ports/build-files); adapt recipes to upstream dependencies and install rules. It won't overwrite an existing port directory. `doctor` checks `QNX_HOST`, `QNX_TARGET` and `qcc`/`q++` on PATH without running them. Missing SDK is a normal, successful diagnostic.

## Architecture and tests

- `qnxport/rules.json` is the versioned portability database; each rule cites an official QNX 8.0 reference.
- `scan.py` lexes inputs, matches rules and resolves comment suppressions without invoking a compiler.
- `buildfile.py` produces a buildfile model and runs structural and image checks; `report.py` renders both sets of findings.
- `scaffold.py` renders starter files; `doctor.py` probes the environment; `cli.py` handles arguments and exits.
- `examples/` includes an original MIT-licensed C project and an illustrative AArch64 buildfile. They do not redistribute QNX binaries.

```sh
python -m pip install pytest ruff
python -m pytest
ruff check .
```

GitHub Actions runs the checks on Ubuntu/Python 3.10–3.13 and uploads an example SARIF artifact. To sanity-check a real codebase independently:

```sh
git clone --depth 1 https://github.com/libuv/libuv.git /tmp/qnxport-libuv
qnxport scan /tmp/qnxport-libuv --format markdown --fail-on none
```

### Limits and verification status

No QNX SDP compilation, license activation, `mkifs` invocation, target boot or generated-port build was verified. QNX Everywhere provides the SDP for personal non-commercial use, but download and use require a myQNX account and deployed license; the host CLI does not use them. The sample buildfile is an example, not an image known to boot. The linter does not expand all mkifs variables, resolve SDK/BSP paths or inspect binary dependencies. The scanner is not a C preprocessor or build-system evaluator: it can report intentionally isolated Linux code and can miss macro-generated calls, complex link expressions or platform tests. Review findings in the context of the target build.

## Devpost submission notes

- **Category:** Developer Tooling & Host Tools.
- **QNX version:** 8.0. **Execution target:** Linux host; no QNX hardware or QEMU required. **Toolchain to run qnxport:** Python 3.10+; no QNX toolchain required. Actual QNX cross-compilation is a separate, licensed step.
- **QNX evidence:** show the rule database references, the Linux demo scan, the valid buildfile lint and a single introduced error, and the two variant directories from scaffold. For the optional Best Codelab / Tutorial bonus, provide the public URL of [the tutorial](docs/CODELAB.md).
- **User steps only:** register free on [Devpost](https://qnx.devpost.com/), record and upload a public YouTube demo under three minutes, and submit the public repository, video and project description before **December 15, 2026, 2:00 p.m. EST / December 16, 03:00 China Standard Time**. No video or submission is included here. See the [Official Rules](https://qnx.devpost.com/rules) for eligibility and winner forms.
