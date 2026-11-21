import argparse
import json
import sys
from pathlib import Path

from . import __version__, buildfile, doctor, scaffold
from .report import SEVERITIES, render, threshold_failed
from .scan import scan

FORMATS = ("text", "json", "sarif", "markdown")


def reporting(parser):
    parser.add_argument("--format", choices=FORMATS, default="text")
    parser.add_argument("--fail-on", choices=SEVERITIES, default="error",
                        help="exit 1 at this severity or higher (default: error)")


def make_parser():
    parser = argparse.ArgumentParser(prog="qnxport", description="Linux host tools for QNX 8.0.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    scanner = sub.add_parser("scan", help="scan C/C++ sources and build files")
    scanner.add_argument("directory", type=Path)
    scanner.add_argument("--relative-to", type=Path, metavar="DIRECTORY",
                         help="report paths relative to this ancestor (default: scanned directory)")
    reporting(scanner)

    build = sub.add_parser("buildfile", help="QNX mkifs buildfile tools")
    build_commands = build.add_subparsers(dest="action", required=True)
    lint = build_commands.add_parser("lint", help="check an mkifs buildfile")
    lint.add_argument("file", type=Path)
    lint.add_argument("--nonbootable", action="store_true", help="skip boot and loader checks")
    reporting(lint)

    port = sub.add_parser("scaffold", help="generate a QNX port starter directory")
    port.add_argument("project")
    port.add_argument("--build-system", choices=scaffold.BUILD_SYSTEMS, required=True)

    inspect = sub.add_parser("doctor", help="inspect the host QNX SDP environment")
    inspect.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv=None):
    args = make_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            data = doctor.inspect()
            print(json.dumps(data, indent=2) if args.format == "json" else doctor.describe(data))
            return 0
        if args.command == "scaffold":
            destination = scaffold.scaffold(args.project, args.build_system, Path("."))
            print(f"Created {destination} ({args.build_system}, QNX 8.0).")
            return 0
        if args.command == "scan":
            directory = args.directory.resolve()
            base = args.relative_to.resolve() if args.relative_to else directory
            if not directory.is_relative_to(base):
                raise ValueError(f"--relative-to must be an ancestor of {directory}")
            findings = list(scan(directory, base))
        else:
            findings = buildfile.lint(
                args.file.read_text(encoding="utf-8"), args.file.as_posix(),
                nonbootable=args.nonbootable,
            )
    except (OSError, ValueError) as error:
        print(f"qnxport: {error}", file=sys.stderr)
        return 2
    print(render(findings, args.format))
    return int(threshold_failed(findings, args.fail_on))
