import posixpath
import re
import shlex
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .report import Finding, Rule

DOC = "https://www.qnx.com/developers/docs/8.0/com.qnx.doc.neutrino.utilities/topic/m/mkifs.html"
RULES = {
    f"IFS{n:03}": Rule(f"IFS{n:03}", level, title, hint, DOC)
    for n, level, title, hint in [
        (1, "error", "Malformed buildfile syntax", "Check brackets, braces, quotes and file specifications."),
        (2, "error", "No virtual boot image header", "Use a virtual= attribute for a QNX 8.0 bootable image."),
        (3, "error", "No procnto in boot block", "Include the BSP's procnto variant in the bootstrap block."),
        (4, "warning", "Duplicate image target", "Use unique targets or mark intentional duplicates with +dupignore."),
        (5, "error", "Invalid owner or permission attribute", "Use decimal uid/gid or *, and octal, symbolic or * permissions."),
        (6, "warning", "No libc.so in the image listing", "If the image needs dynamic executables, provide QNX libc.so."),
        (7, "warning", "No QNX runtime linker in the image listing", "Provide /usr/lib/ldqnx-64.so.2 in the boot environment."),
        (8, "warning", "Runtime linker path not established", "Add an /usr/lib/ldqnx-64.so.2 image entry or a procmgr_symlink to it."),
    ]
}
ATTR = re.compile(r"\??(?:[+-][\w.-]+|[\w.-]+=.*)\Z", re.S)
MODE = re.compile(r"(?:[0-7]{1,4}|\*|[ugoa]*[-+=][rwxsgt]+(?:,[ugoa]*[-+=][rwxsgt]+)*)\Z")
ATTR_LIST = re.compile(r'^\[((?:[^"\'\]\\]|\\.|"[^"]*"|\'[^\']*\')*)\]')
OPEN = re.compile(r"=\s*\{\s*$")
CLOSE = re.compile(r"^\s*}\s*(?:#.*)?$")
UNESCAPED_CLOSE = re.compile(r"(?:^|[^\\])(?:\\\\)*}")
PROC_NTO = re.compile(r"^procnto(?:[-_][\w.-]+)?$")
VAR = re.compile(r"\$(?:\{[^}]+\}|[A-Za-z_]\w*)")
SYSTEM_BINARIES = {"esh", "sh", "ksh", "bash", "devc-con", "io-sock", "fsevmgr", "slogger2"}
SCRIPT_BUILTINS = {"display_msg", "waitfor", "reopen", "procmgr_symlink", "setconf", "unset", "export"}


@dataclass
class Entry:
    line: int
    target: str
    host: str | None
    attrs: dict
    kind: str = "file"
    body: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class Buildfile:
    entries: list[Entry] = field(default_factory=list)
    headers: list[int] = field(default_factory=list)
    issues: list[tuple[int, str, str]] = field(default_factory=list)


def attributes(raw, defaults=None):
    values = {}
    if not raw.startswith("["):
        return values, raw
    match = ATTR_LIST.match(raw)
    if not match:
        raise ValueError("unclosed attribute list")
    for item in shlex.split(match[1]):
        if not ATTR.fullmatch(item):
            raise ValueError(f"invalid attribute {item!r}; do not put spaces around '='")
        token = item.removeprefix("?")
        if token.startswith(("+", "-")):
            key, value = token[1:], token[0] == "+"
        else:
            key, value = token.split("=", 1)
        if not item.startswith("?") or (key not in values and key not in (defaults or {})):
            values[key] = value
    rest = raw[match.end():].strip()
    if rest.startswith("["):
        raise ValueError("combine attributes in one bracket pair")
    return values, rest


def clean_line(raw):
    escaped = False
    quote = ""
    for i, char in enumerate(raw):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "#":
            return raw[:i].strip()
    return raw.strip()


def file_spec(raw):
    lexer = shlex.shlex(raw, posix=True, punctuation_chars="=")
    lexer.whitespace_split = True
    lexer.commenters = ""
    parts = list(lexer)
    if len(parts) == 1 and parts[0] not in {"", "=", "{", "}"}:
        return parts[0], None
    if len(parts) == 3 and parts[1] == "=" and parts[0] and parts[2] not in {"", "{", "}"}:
        return parts[0], parts[2]
    raise ValueError(f"expected host filename or target=host, got {raw!r}")


def parse(text):
    document = Buildfile()
    defaults = {}
    block = None
    lines = iter(enumerate(text.splitlines(), 1))
    for line, raw in lines:
        if block is not None:
            if CLOSE.fullmatch(raw):
                block = None
            else:
                block.body.append((line, raw))
                if UNESCAPED_CLOSE.search(raw):
                    document.issues.append((line, "IFS001", "escape literal closing braces in inline content"))
            continue
        while (len(raw) - len(raw.rstrip("\\"))) % 2:
            continuation = next(lines, None)
            if continuation is None:
                break
            raw = raw[:-1] + continuation[1]
        raw = clean_line(raw)
        if not raw:
            continue
        try:
            attrs, spec = attributes(raw, defaults)
            for key in ("uid", "gid", "perms", "dperms"):
                if key not in attrs:
                    continue
                val = str(attrs[key])
                pattern = MODE if key.endswith("perms") else re.compile(r"(?:[0-9]+|\*)\Z")
                if not VAR.search(val) and not pattern.fullmatch(val):
                    document.issues.append((line, "IFS005", f"{key}={val}"))
            if "physical" in attrs:
                document.issues.append((line, "IFS001", "physical= is not implemented; use virtual="))
            header = "virtual" if "virtual" in attrs else None
            if header:
                value = attrs[header]
                if not isinstance(value, str) or not value or value.endswith((",", "/")):
                    document.issues.append((line, "IFS001", f"invalid {header} bootfile value"))
                else:
                    document.headers.append(line)
            merged = defaults | attrs
            if not spec:
                defaults = merged
            elif OPEN.search(spec):
                target, _ = file_spec(OPEN.sub("=QNXPORT_INLINE", spec))
                kind = (
                    "boot" if header or target == ".bootstrap" else
                    "script" if merged.get("script") else "inline"
                )
                block = Entry(line, target, None, merged, kind)
                document.entries.append(block)
            else:
                if re.search(r"(?:^|\s)[{}](?:$|\s)", spec):
                    raise ValueError("unexpected brace; put inline contents on separate lines")
                target, host = file_spec(spec)
                if host is None:
                    host = target
                    target = posixpath.basename(target)
                kind = ("boot" if header or target == ".bootstrap" else
                        "script" if merged.get("script") else "file")
                document.entries.append(Entry(line, target, host, merged, kind))
        except ValueError as error:
            document.issues.append((line, "IFS001", str(error)))
    if block is not None:
        document.issues.append((block.line, "IFS001", f"unclosed block for {block.target}"))
    return document


def commands(entries):
    for entry in entries:
        for line, raw in entry.body:
            try:
                _, command = attributes(clean_line(raw))
                words = shlex.split(command)
            except ValueError:
                continue
            while words and re.match(r"^[A-Za-z_]\w*=", words[0]):
                words.pop(0)
            if words:
                yield line, words


def image_path(entry, nonbootable):
    prefix = str(entry.attrs.get("prefix", "" if nonbootable else "proc/boot"))
    mount = str(entry.attrs.get("mount", "/"))
    target = entry.target if entry.target.startswith("/") else posixpath.join(prefix, entry.target)
    return posixpath.normpath(posixpath.join("/", mount.lstrip("/"), target.lstrip("/")))


def has_elf_interpreter(path):
    if not path.is_file():
        return False
    try:
        with path.open("rb") as binary:
            header = binary.read(64)
            if len(header) != 64 or header[:4] != b"\x7fELF" or header[4] not in (1, 2):
                return False
            byte_order = {1: "<", 2: ">"}.get(header[5])
            if not byte_order:
                return False
            wide = header[4] == 2
            offset, = struct.unpack_from(byte_order + ("Q" if wide else "I"), header, 32 if wide else 28)
            size, count = struct.unpack_from(byte_order + "HH", header, 54 if wide else 42)
            if size < 4 or not offset or count == 0xFFFF:
                return False
            for index in range(count):
                binary.seek(offset + index * size)
                p_type = binary.read(4)
                if len(p_type) < 4:
                    break
                if struct.unpack(byte_order + "I", p_type)[0] == 3:  # PT_INTERP
                    return True
    except (OSError, ValueError):
        pass
    return False


def local_dynamic_entry(entry, buildfile_dir):
    if not entry.host or entry.attrs.get("type") == "link" or VAR.search(entry.host):
        return False
    host = Path(entry.host)
    if host.is_absolute():
        return has_elf_interpreter(host)
    search = str(entry.attrs.get("search", ""))
    paths = [Path.cwd(), buildfile_dir]
    paths.extend(Path(part) for part in search.split(":") if part and not VAR.search(part))
    return any(has_elf_interpreter(directory / host) for directory in paths)


def lint(text, path, *, nonbootable=False):
    document = parse(text)
    issues = document.issues[:]
    entries = document.entries
    if not nonbootable:
        if not document.headers:
            issues.append((1, "IFS002", "use --nonbootable for an image fragment"))
        boot = [entry for entry in entries if entry.kind == "boot"]
        external_boot = any(entry.host for entry in boot)
        if not external_boot and not any(
            PROC_NTO.fullmatch(posixpath.basename(words[0])) for _, words in commands(boot)
        ):
            issues.append((boot[0].line if boot else 1, "IFS003", "bootstrap"))

    targets = {}
    scripts = set()
    files = [entry for entry in entries if entry.kind in {"file", "inline"}]
    for entry in entries:
        if entry.kind == "script":
            if entry.target in scripts:
                issues.append((entry.line, "IFS004", entry.target))
            scripts.add(entry.target)
        elif entry.kind != "boot":
            key = image_path(entry, nonbootable)
            if key in targets and not entry.attrs.get("dupignore"):
                issues.append((entry.line, "IFS004", f"{key}, first listed on line {targets[key]}"))
            targets.setdefault(key, entry.line)

    known = {posixpath.basename(entry.target) for entry in files if entry.host}
    startup = list(commands(entry for entry in entries if entry.kind == "script"))
    dynamic = sorted({
        posixpath.basename(words[0]) for _, words in startup
        if posixpath.basename(words[0]) in known - SCRIPT_BUILTINS
    } | (known & SYSTEM_BINARIES) | {
        posixpath.basename(entry.target) for entry in files
        if not nonbootable and local_dynamic_entry(entry, Path(path).parent)
    })
    runtime_path = "/usr/lib/ldqnx-64.so.2"
    if dynamic and not nonbootable:
        detail = f"check linkage for {', '.join(dynamic)}"
        names = {posixpath.basename(key) for key in targets}
        if not {"libc.so", "libc.so.5"} & names:
            issues.append((1, "IFS006", detail))
        symlink = any(
            len(words) >= 3 and words[0] == "procmgr_symlink" and words[2] == runtime_path
            for _, words in startup
        )
        if "ldqnx-64.so.2" not in names and not symlink:
            issues.append((1, "IFS007", detail))
        elif runtime_path not in targets and not symlink:
            issues.append((1, "IFS008", runtime_path))
    return [
        Finding(path, line, 1, RULES[rule], message)
        for line, rule, message in sorted(issues)
    ]
