import os
import shutil
from pathlib import Path


def inspect(environ=None):
    env = os.environ if environ is None else environ
    data = {}
    for name in ("QNX_HOST", "QNX_TARGET"):
        value = env.get(name) or None
        data[name] = {"value": value, "directory_exists": bool(value and Path(value).is_dir())}
    data["qcc"] = shutil.which("qcc", path=env.get("PATH", ""))
    data["q++"] = shutil.which("q++", path=env.get("PATH", ""))
    data["environment_detected"] = bool(
        data["qcc"] and data["QNX_HOST"]["directory_exists"] and data["QNX_TARGET"]["directory_exists"]
    )
    return data


def describe(data):
    lines = []
    for name in ("QNX_HOST", "QNX_TARGET"):
        value = data[name]["value"]
        status = "" if not value or data[name]["directory_exists"] else " (not a directory)"
        lines.append(f"{name}: {value or 'unset'}{status}")
    lines.extend(f"{name}: {data[name] or 'not on PATH'}" for name in ("qcc", "q++"))
    if data["environment_detected"]:
        lines.append("SDP environment detected; SDK version, activation and compilation not verified.")
    else:
        lines.append("QNX SDP environment not detected. Scan, lint and scaffold work without it.")
        lines.append("To cross-compile, obtain a myQNX license and source the SDP's qnxsdp-env.sh.")
    return "\n".join(lines)
