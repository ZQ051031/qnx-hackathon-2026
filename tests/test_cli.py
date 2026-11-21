import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def invoke(*args, cwd=ROOT, env=None):
    environ = os.environ | {"PYTHONPATH": str(ROOT)} | (env or {})
    return subprocess.run(
        [sys.executable, "-m", "qnxport", *map(str, args)], cwd=cwd,
        env=environ, capture_output=True, text=True, check=False,
    )


def test_scan_sample_is_end_to_end():
    args = ("scan", "examples/linux-only", "--relative-to", ".", "--format", "json")
    result = invoke(*args)
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert {item["rule"]["id"] for item in report} == {
        "QNX001", "QNX010", "QNX011", "QNX012", "QNX013",
    }
    assert {item["path"] for item in report} == {
        "examples/linux-only/CMakeLists.txt", "examples/linux-only/main.c",
    }
    assert invoke(*args, "--fail-on", "none").returncode == 0
    assert invoke("scan", "missing").returncode == 2
    assert invoke("scan", "examples", "--relative-to", "tests").returncode == 2


def test_buildfile_lint_sample():
    result = invoke("buildfile", "lint", "examples/demo.build")
    assert result.returncode == 0
    assert result.stdout == "0 finding(s).\n"
    assert invoke("buildfile", "lint", "examples/missing").returncode == 2


@pytest.mark.parametrize("system", ["cmake", "autotools", "make"])
def test_scaffold_generates_both_architectures_without_overwriting(tmp_path, system):
    result = invoke("scaffold", "libexample", "--build-system", system, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    port = tmp_path / "ports" / "libexample"
    assert (port / "Makefile").is_file()
    assert "QNX_PROJECT_ROOT" in (port / "common.mk").read_text()
    assert "-Vgcc_nto" in (port / "common.mk").read_text()
    assert "QNX_ARCH := aarch64le" in (port / "nto-aarch64-le" / "Makefile").read_text()
    assert "QNX_ARCH := x86_64" in (port / "nto-x86_64-o" / "Makefile").read_text()
    assert (port / "qnx.nto.toolchain.cmake").exists() == (system == "cmake")
    assert (port / "README.md").is_file()
    assert invoke("scaffold", "libexample", "--build-system", system, cwd=tmp_path).returncode == 2
    assert invoke("scaffold", "../outside", "--build-system", system, cwd=tmp_path).returncode == 2


def test_doctor_absent_or_present_without_running_qcc(tmp_path):
    empty = {"QNX_HOST": "", "QNX_TARGET": "", "PATH": ""}
    result = invoke("doctor", env=empty)
    assert result.returncode == 0
    assert "QNX SDP environment not detected" in result.stdout
    executable = tmp_path / "qcc"
    executable.write_text("#!/bin/sh\nexit 1\n")
    executable.chmod(0o755)
    result = invoke("doctor", "--format", "json", env={
        "QNX_HOST": str(tmp_path), "QNX_TARGET": str(tmp_path), "PATH": str(tmp_path),
    })
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["environment_detected"]
    assert report["qcc"] == str(executable)
