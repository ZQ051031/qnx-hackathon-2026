import json

import pytest

from qnxport.report import render, threshold_failed
from qnxport.scan import analyze, rules


def scan_source(text):
    return list(analyze(text, "dir/My Source#.cpp", True))


def ids(findings):
    return [f.rule.id for f in findings]


def test_c_lexer_comments_strings_and_real_calls():
    c = '''/* #include <linux/ignored.h>
    epoll_wait();
    */
    auto payload = R"data(
#include <linux/raw.h>
    epoll_wait(); "embedded string"
    )data";
    const char* status = "eventfd()";
    #include "sys/epoll.h"
    struct epoll_event event;
    epoll_wait (fd, &event, 1, -1);
    callbacks.eventfd();
    const char* location = "/proc/self/exe";
    '''
    hits = scan_source(c)
    assert [(f.line, f.match) for f in hits] == [
        (9, "sys/epoll.h"), (11, "epoll_wait"), (13, "/proc/self/exe"),
    ]
    assert hits[0].column == 15


def test_suppression_must_be_a_comment():
    hits = scan_source('''
// qnxport: ignore[QNX001,QNX002]
epoll_wait(fd); eventfd(0, 0);
eventfd(0, 0); // qnxport: ignore[QNX002]
epoll_wait(fd); const char *s = "qnxport: ignore[QNX001]";
epoll_wait(fd); // qnxport: ignore[QNX002]
''')
    assert [(f.line, f.rule.id) for f in hits] == [(5, "QNX001"), (6, "QNX001")]


def test_review_rules_are_notes_and_cite_docs():
    sample = """
#include <sys/inotify.h>
int n = inotify_init1(0);
int t = gettid();
int x = SO_REUSEPORT;
struct mallinfo mi = mallinfo();
"""
    hits = scan_source(sample)
    assert ids(hits) == ["QNX005", "QNX005", "QNX016", "QNX018", "QNX020"]
    assert all(f.rule.severity == "note" for f in hits)
    assert "libfsnotify" in hits[0].rule.hint
    assert all(row["url"].startswith("https://www.qnx.com/developers/docs/8.0/") for row in rules())


def test_platform_guard_pairs_with_visible_qnx_branch():
    result = scan_source("""
#ifdef __linux__
int linux_backend;
#elif defined(__QNXNTO__)
int qnx_backend;
#endif
#if defined(__linux__)
int other_backend;
#endif
""")
    assert [(f.line, f.rule.id) for f in result] == [(7, "QNX014")]


def test_platform_notes_group_elif_checks_and_skip_generic_fallback():
    result = scan_source("""
#if defined(__linux__)
#elif defined(__linux__) && defined(__mips__)
#endif
#if defined(__linux__)
#else
#endif
""")
    assert [(f.line, f.rule.id) for f in result] == [(2, "QNX014")]


@pytest.mark.parametrize(("name", "source", "expected"), [
    ("CMakeLists.txt", '# target_link_libraries(x dl)\n#[=[-ldl]=]\n'
                       'target_link_libraries(x PRIVATE dl rt pthread socket)', ["QNX010", "QNX011", "QNX012"]),
    ("configure.ac", "dnl AC_CHECK_LIB(dl, dlopen)\nAC_CHECK_LIB(rt, timer_create)", ["QNX011"]),
    ("Makefile", "LDLIBS += -ldl -lpthread # -lrt", ["QNX010", "QNX012"]),
    ("meson.build", "cc.find_library('dl', required: false)", ["QNX010"]),
])
def test_build_inputs(name, source, expected):
    assert ids(analyze(source, name, False)) == expected


def test_build_messages_and_autoconf_probes_are_not_hardcoded_linkage():
    cmake = 'message("target_link_libraries(x dl), -lrt")\nset(LDFLAGS "-lpthread")\n'
    assert ids(analyze(cmake, "CMakeLists.txt", False)) == ["QNX012"]
    assert not list(analyze("AC_SEARCH_LIBS([dlopen], [dl])", "configure.ac", False))


def test_reports_and_severity_thresholds():
    hits = scan_source("eventfd(0, 0);")
    assert threshold_failed(hits, "error")
    assert not threshold_failed(hits, "none")
    assert not threshold_failed(scan_source("gettid();"), "warning")
    assert json.loads(render(hits, "json"))[0]["rule"]["id"] == "QNX002"
    assert "| Location | Severity | Rule |" in render(hits, "markdown")
    report = json.loads(render(hits, "sarif"))
    assert report["version"] == "2.1.0"
    run = report["runs"][0]
    assert run["tool"]["driver"]["rules"][0]["id"] == "QNX002"
    location = run["results"][0]["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "dir/My%20Source%23.cpp"
    assert location["region"] == {"startLine": 1, "startColumn": 1}
