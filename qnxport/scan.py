import json
import os
import re
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from .report import Finding, Rule

SOURCE_EXT = {".c", ".h", ".cc", ".cpp", ".cxx", ".c++", ".hh", ".hpp", ".hxx", ".ipp"}
BUILD_NAMES = {
    "cmakelists.txt", "makefile", "gnumakefile", "makefile.am", "makefile.in", "meson.build",
    "configure.ac", "configure.in",
}
SKIP_DIRS = {".git", ".venv", "__pycache__", "build", "node_modules", ".tox", ".pytest_cache"}
C_LEX = re.compile(
    r"(?P<comment>//(?:\\\n|[^\n])*|/\*[\s\S]*?(?:\*/|\Z))"
    r'|(?P<raw>(?:u8|u|U|L)?R"(?P<delim>[^()\\\s]{0,16})\([\s\S]*?\)(?P=delim)")'
    r"""|(?P<string>"(?:\\[\s\S]|[^"\\\n])*"|'(?:\\[\s\S]|[^'\\\n])*')"""
)
INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*[<"]([^>"\s]+)[>"]', re.M)
C_WORD = re.compile(r"[A-Za-z_]\w*")
PREPROC = re.compile(r"^[ \t]*#[ \t]*(ifdef|ifndef|if|elif|else|endif)\b([^\n]*)", re.M)
LINUX = re.compile(r"\b__linux__\b")
QNX = re.compile(r"\b(?:__QNX__|__QNXNTO__)\b")
PROC = re.compile(r"/proc/(?:self|thread-self)/(?:exe|fd(?:info)?|maps|smaps|status|cmdline|mountinfo|task|cgroup)(?=[/\"'\s]|\Z)")
IGNORE = re.compile(r"qnxport:\s*ignore\[([A-Z0-9]+(?:\s*,\s*[A-Z0-9]+)*)\]")
BUILD_LEX = re.compile(
    r"(?P<bracket>\#\[(?P<eq>=*)\[[\s\S]*?\](?P=eq)\])"
    r"""|(?P<string>"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*')"""
    r"|(?P<comment>(?<!\\)\#[^\n]*)"
)
AUTOCONF_COMMENT = re.compile(r"\bdnl(?:[ \t][^\n]*)?$", re.M)
LINK_FLAG = re.compile(r"(?<![\w.-])-(?:ldl|lrt|lpthread)(?![\w.+/-])")
LIB_CONTEXT = re.compile(
    r"\b(?:target_link_libraries|link_libraries|find_library|AC_CHECK_LIB)\s*\([^)]*\)"
    r"|\b(?:find_library|dependency)\s*\(\s*['\"][^'\"]+['\"]",
    re.I | re.S,
)
LINK_SETTING = re.compile(
    r"(?:\b(?:set|list|target_link_options|add_link_options|link_options)\s*\("
    r"[^)]*|\b(?:[\w]*LIBS|[\w]*LDFLAGS|[\w]*LINK_FLAGS|link_args)"
    r"\s*(?:\+?[:?]?=|:)\s*[^#]*)$",
    re.I,
)
BARE_LIB = re.compile(r"(?<![\w.-])(?:dl|rt|pthread)(?![\w.-])")


@dataclass(frozen=True)
class RuleIndex:
    by_kind: dict
    header_prefixes: tuple

    def matches(self, kind, word):
        return self.by_kind.get(kind, {}).get(word, ())


@lru_cache(maxsize=1)
def rules():
    return tuple(json.loads(files("qnxport").joinpath("rules.json").read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def rule_index():
    by_kind: dict = {}
    prefixes = []
    for row in rules():
        rule = Rule(**{key: value for key, value in row.items() if key != "match"})
        for kind, values in row["match"].items():
            if kind == "header_prefix":
                prefixes.extend((value, rule) for value in values)
            else:
                index = by_kind.setdefault(kind, {})
                for value in [True] if values is True else values:
                    index.setdefault(value, []).append(rule)
    return RuleIndex(by_kind, tuple(prefixes))


def masked(text, ranges):
    chars = list(text)
    for start, end in ranges:
        chars[start:end] = [c if c == "\n" else " " for c in text[start:end]]
    return "".join(chars)


def source_candidates(text, index, lex):
    comments = [m.span() for m in lex if m.lastgroup == "comment"]
    no_comments = masked(text, comments)
    bare_code = masked(text, [m.span() for m in lex])
    includes = [
        m for m in INCLUDE.finditer(no_comments) if "#" in bare_code[m.start():m.start(1)]
    ]
    code = masked(bare_code, [m.span(1) for m in includes])
    include_starts = {m.start(1) - 1 for m in includes}
    for m in includes:
        header = m[1]
        yield from ((m.start(1), r, header) for r in index.matches("header", header))
        for prefix, rule in index.header_prefixes:
            if header.startswith(prefix):
                yield m.start(1), rule, header
    for literal in lex:
        if literal.lastgroup == "string" and literal.start() not in include_starts:
            m = PROC.search(literal[0])
            if m:
                yield from ((literal.start() + m.start(), r, m[0]) for r in index.matches("proc", True))
    for m in C_WORD.finditer(code):
        word = m[0]
        yield from ((m.start(), rule, word) for rule in index.matches("token", word))
        call_rules = index.matches("call", word)
        if call_rules and re.match(r"\s*\(", code[m.end():m.end() + 150]):
            before = code[max(0, m.start() - 30):m.start()].rstrip()
            if not before.endswith((".", "->")):
                yield from ((m.start(), rule, word) for rule in call_rules)
    for start, word in linux_guards(code):
        yield from ((start, rule, word) for rule in index.matches("guard", True))


def linux_guards(code):
    stack: list[list] = []
    for m in PREPROC.finditer(code):
        keyword, expr = m.groups()
        if keyword in {"if", "ifdef", "ifndef"}:
            stack.append([None, False])
        if keyword in {"if", "ifdef", "ifndef", "elif"} and stack:
            linux, qnx = LINUX.search(expr), QNX.search(expr)
            if linux and stack[-1][0] is None:
                stack[-1][0] = m.start(2) + linux.start()
            if qnx:
                for scope in stack:
                    scope[1] = True
        if keyword == "else" and stack:
            stack[-1][1] = True
        if keyword == "endif" and stack:
            start, handles_qnx = stack.pop()
            if start is not None and not handles_qnx:
                yield start, "__linux__"


def build_candidates(text, index, lex, dnl):
    no_comments = masked(text, [*(m.span() for m in lex if m.lastgroup != "string"), *dnl])
    literals = [m for m in lex if m.lastgroup == "string"]
    literal_starts = [m.start() for m in literals]
    def in_literal(offset):
        previous = bisect_right(literal_starts, offset) - 1
        return literals[previous] if previous >= 0 and offset < literals[previous].end() else None

    contexts = [m for m in LIB_CONTEXT.finditer(no_comments) if not in_literal(m.start())]
    for m in LINK_FLAG.finditer(no_comments):
        if literal := in_literal(m.start()):
            line_start = no_comments.rfind("\n", 0, literal.start()) + 1
            if not (
                LINK_SETTING.search(no_comments[line_start:literal.start()])
                or any(ctx.start() <= m.start() < ctx.end() for ctx in contexts)
            ):
                continue
        yield from ((m.start(), rule, m[0]) for rule in index.matches("link", m[0]))
    for context in contexts:
        for m in BARE_LIB.finditer(context[0]):
            yield from (
                (context.start() + m.start(), rule, m[0]) for rule in index.matches("link", m[0])
            )


def analyze(text, path, source):
    starts = [0, *(m.end() for m in re.finditer("\n", text))]

    def loc(offset):
        line = bisect_right(starts, offset)
        return line, offset - starts[line - 1] + 1

    lex = list((C_LEX if source else BUILD_LEX).finditer(text))
    dnl = []
    if Path(path).name.lower() in {"configure.ac", "configure.in"}:
        dnl = [m.span() for m in AUTOCONF_COMMENT.finditer(
            masked(text, [m.span() for m in lex if m.lastgroup == "string"])
        )]
    comments = [m.span() for m in lex if m.lastgroup in {"bracket", "comment"}] + dnl
    suppression = set()
    for start, end in comments:
        for m in IGNORE.finditer(text, start, end):
            line, _ = loc(m.start())
            targets = {line}
            if not text[starts[loc(start)[0] - 1]:start].strip():
                targets.add(loc(end - 1)[0] + 1)
            suppression.update((target, rule.strip()) for target in targets for rule in m[1].split(","))
    index = rule_index()
    candidates = (
        source_candidates(text, index, lex) if source else build_candidates(text, index, lex, dnl)
    )
    seen = set()
    for offset, rule, match in sorted(candidates, key=lambda item: (item[0], item[1].id)):
        line, column = loc(offset)
        key = (0 if rule.id == "QNX013" else line, rule.id)
        if key not in seen and (line, rule.id) not in suppression:
            seen.add(key)
            yield Finding(path, line, column, rule, match)


def scan(root, base=None):
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    for folder, dirs, names in os.walk(root, onerror=lambda error: _raise(error)):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            path = Path(folder) / name
            source = path.suffix.lower() in SOURCE_EXT
            build = name.lower() in BUILD_NAMES or path.suffix.lower() in {".cmake", ".mk", ".m4"}
            if (source or build) and not path.is_symlink() and path.is_file():
                yield from analyze(
                    path.read_text(encoding="utf-8", errors="replace"),
                    path.relative_to(base or root).as_posix(),
                    source,
                )


def _raise(error):
    raise error
