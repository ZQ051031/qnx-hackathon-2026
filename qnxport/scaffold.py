import re
from pathlib import Path
from shutil import rmtree

ARCHITECTURES = {
    "nto-aarch64-le": ("aarch64le", "aarch64-unknown-nto-qnx8.0.0"),
    "nto-x86_64-o": ("x86_64", "x86_64-pc-nto-qnx8.0.0"),
}
BUILD_SYSTEMS = ("cmake", "autotools", "make")
TOP = """ARCHES := nto-aarch64-le nto-x86_64-o
BUILD_ARCHES ?= $(ARCHES)
.PHONY: all install clean $(ARCHES)
all install clean: $(BUILD_ARCHES)
$(ARCHES):
	$(MAKE) -C $@ $(if $(MAKECMDGOALS),$(filter-out $(ARCHES),$(MAKECMDGOALS)),all)
"""
COMMON = """
QNX_PROJECT_ROOT ?= $(abspath ../../..)
BUILD_DIR ?= $(CURDIR)/build
STAGE_DIR ?= $(CURDIR)/stage
PREFIX ?= /usr/local
QNX_CC = qcc -Vgcc_nto$(QNX_ARCH)
QNX_CXX = q++ -Vgcc_nto$(QNX_ARCH)
.DEFAULT_GOAL := all
.PHONY: all install clean check-env
check-env:
	@test -d "$(QNX_HOST)" && test -d "$(QNX_TARGET)" && command -v qcc >/dev/null || \\
\t    { echo "Source the QNX SDP 8.0 environment (qnxsdp-env.sh) first." >&2; exit 1; }
clean:
	$(RM) -r "$(BUILD_DIR)" "$(STAGE_DIR)"
"""
RECIPES = {
    "cmake": """
all: check-env
	cmake -S "$(QNX_PROJECT_ROOT)" -B "$(BUILD_DIR)" \\
\t    -DCMAKE_TOOLCHAIN_FILE="$(CURDIR)/../qnx.nto.toolchain.cmake" \\
\t    -DQNX_ARCH="$(QNX_ARCH)" -DCMAKE_INSTALL_PREFIX="$(PREFIX)"
	cmake --build "$(BUILD_DIR)"
install: all
	DESTDIR="$(STAGE_DIR)" cmake --install "$(BUILD_DIR)"
""",
    "autotools": """
HOST_TRIPLE ?= $(QNX_TRIPLE)
all: check-env
	mkdir -p "$(BUILD_DIR)"
	cd "$(BUILD_DIR)" && CC="$(QNX_CC)" CXX="$(QNX_CXX)" \\
\t    "$(QNX_PROJECT_ROOT)/configure" --host="$(HOST_TRIPLE)" --prefix="$(PREFIX)"
	$(MAKE) -C "$(BUILD_DIR)"
install: all
	$(MAKE) -C "$(BUILD_DIR)" DESTDIR="$(STAGE_DIR)" install
""",
    "make": """
UPSTREAM_BUILD_TARGET ?= all
UPSTREAM_INSTALL_TARGET ?= install
all: check-env
	$(MAKE) -C "$(QNX_PROJECT_ROOT)" CC="$(QNX_CC)" CXX="$(QNX_CXX)" $(UPSTREAM_BUILD_TARGET)
install: all
	$(MAKE) -C "$(QNX_PROJECT_ROOT)" CC="$(QNX_CC)" CXX="$(QNX_CXX)" \\
\t    PREFIX="$(PREFIX)" DESTDIR="$(STAGE_DIR)" $(UPSTREAM_INSTALL_TARGET)
""",
}
CMAKE = """
set(CMAKE_SYSTEM_NAME QNX)
set(QNX_ARCH "${QNX_ARCH}" CACHE STRING "QNX CPU: aarch64le or x86_64")
if(NOT QNX_ARCH MATCHES "^(aarch64le|x86_64)$")
    message(FATAL_ERROR "Set QNX_ARCH to aarch64le or x86_64")
endif()
if(NOT IS_DIRECTORY "$ENV{QNX_HOST}" OR NOT IS_DIRECTORY "$ENV{QNX_TARGET}")
    message(FATAL_ERROR "Source the QNX SDP 8.0 environment before configuring")
endif()
set(CMAKE_SYSTEM_PROCESSOR "${QNX_ARCH}")
set(CMAKE_C_COMPILER "$ENV{QNX_HOST}/usr/bin/qcc")
set(CMAKE_CXX_COMPILER "$ENV{QNX_HOST}/usr/bin/q++")
set(CMAKE_C_COMPILER_TARGET "gcc_nto${QNX_ARCH}")
set(CMAKE_CXX_COMPILER_TARGET "gcc_nto${QNX_ARCH}")
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES QNX_ARCH)
set(CMAKE_SYSROOT "$ENV{QNX_TARGET}")
set(CMAKE_FIND_ROOT_PATH "$ENV{QNX_TARGET}/${QNX_ARCH}" "$ENV{QNX_TARGET}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
"""


def scaffold(project, build_system, root=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", project):
        raise ValueError("project must be a single name: letters, digits, dots, dashes or underscores")
    if build_system not in BUILD_SYSTEMS:
        raise ValueError(f"unsupported build system: {build_system}")
    destination = (root or Path.cwd()) / "ports" / project
    destination.mkdir(parents=True)
    try:
        top = TOP
        if build_system == "make":
            top = TOP.replace("BUILD_ARCHES ?= $(ARCHES)", "BUILD_ARCHES ?= nto-aarch64-le")
        (destination / "Makefile").write_text(top, encoding="utf-8")
        (destination / "common.mk").write_text(COMMON.lstrip() + RECIPES[build_system], encoding="utf-8")
        for name, (arch, triple) in ARCHITECTURES.items():
            directory = destination / name
            directory.mkdir()
            (directory / "Makefile").write_text(
                f"QNX_ARCH := {arch}\nQNX_TRIPLE := {triple}\ninclude ../common.mk\n",
                encoding="utf-8",
            )
        if build_system == "cmake":
            (destination / "qnx.nto.toolchain.cmake").write_text(CMAKE.lstrip(), encoding="utf-8")
        extra = {
            "autotools": "Generate the upstream configure script first if needed. Adjust HOST_TRIPLE for older config.sub scripts.",
            "cmake": "If upstream uses third-party packages, stage their QNX builds and set the relevant CMake search options.",
            "make": "An upstream Makefile may ignore CC, CXX, PREFIX or DESTDIR. Check it first. Use a clean source checkout per architecture or clean upstream between builds.",
        }[build_system]
        readme = (
            f"# {project} QNX 8.0 port scaffold\n\n"
            "Set the upstream URL and version here, then add and document your patches. "
            "This scaffold has not been built with the QNX SDK.\n\n"
            "Source the licensed QNX SDP 8.0 environment and select a target architecture:\n\n"
            "    source ~/qnx800/qnxsdp-env.sh\n"
            f"    make -C ports/{project}/nto-aarch64-le QNX_PROJECT_ROOT=/absolute/source install\n\n"
            "Swap the target directory to nto-x86_64-o for x86-64; "
            "install output is staged under that architecture directory, not the SDK.\n\n"
            f"{extra}\n"
        )
        (destination / "README.md").write_text(readme, encoding="utf-8")
    except (OSError, KeyError):
        rmtree(destination, ignore_errors=True)
        raise
    return destination
