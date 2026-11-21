import struct
from pathlib import Path

from qnxport.buildfile import lint, parse

BOOT = """[virtual=aarch64le,elf] .bootstrap = {
    startup-qemu-virt
    procnto-smp-instr
}
"""


def ids(text, **options):
    return {f.rule.id for f in lint(text, "image.build", **options)}


def test_documented_bootstrap_and_runtime_path():
    example = Path(__file__).resolve().parents[1] / "examples" / "demo.build"
    text = example.read_text(encoding="utf-8")
    assert ids(text) == set()
    doc = parse(text)
    assert doc.headers == [3]
    assert doc.entries[0].attrs["search"] == ".:$MKIFS_PATH"
    assert [e.kind for e in doc.entries[:2]] == ["boot", "script"]
    assert doc.entries[-1].attrs["perms"] == "0644"


def test_braces_duplicates_and_owner_diagnostics():
    text = BOOT + """
[uid=alice gid=0 perms=0688] /etc/issue=first
/etc/issue=second
[+dupignore] /etc/issue=third
/etc/config={
still open
"""
    findings = lint(text, "image.build")
    assert [(f.line, f.rule.id) for f in findings] == [
        (6, "IFS005"), (6, "IFS005"), (7, "IFS004"), (9, "IFS001"),
    ]


def test_inline_file_is_opaque_and_wildcard_and_symbolic_modes_are_legal():
    text = BOOT + r"""
[uid=* gid=* perms=u+rx,g-w] /etc/config={
literal opener {
\}
# This is data, not a buildfile comment
}
[dperms=*] "/etc/file with spaces"="/host/my file"
"""
    assert ids(text) == set()
    assert parse(text).entries[-2].body[-1][1].startswith("# This is data")


def test_proc_is_checked_inside_bootstrap_and_fragments_are_supported():
    assert ids("esh\nprocnto-smp-instr") == {"IFS002", "IFS003", "IFS006", "IFS007"}
    assert ids("esh\n", nonbootable=True) == set()
    assert ids(BOOT.replace("procnto-smp-instr", "other")) == {"IFS003"}
    assert ids("[virtual=elf] boot=external.bootstrap") == set()
    assert ids(BOOT.replace("virtual=", "physical=")) == {"IFS001", "IFS002"}


def test_missing_loader_wrong_path_and_boot_script_symlink():
    listing = """
[+script] .script = {
my_daemon &
}
/usr/bin/my_daemon=./daemon
"""
    assert ids(BOOT + listing) == {"IFS006", "IFS007"}
    assert ids(BOOT + listing + "libc.so\nldqnx-64.so.2\n") == {"IFS008"}
    with_link = listing.replace(
        "my_daemon &",
        "procmgr_symlink /proc/boot/ldqnx-64.so.2 /usr/lib/ldqnx-64.so.2\nmy_daemon &",
    )
    assert ids(BOOT + with_link + "libc.so\nldqnx-64.so.2\n") == set()


def test_search_path_can_find_local_dynamic_elf(tmp_path):
    binary = bytearray(120)
    binary[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<Q", binary, 32, 64)
    struct.pack_into("<HH", binary, 54, 56, 1)
    struct.pack_into("<I", binary, 64, 3)
    (tmp_path / "daemon.elf").write_bytes(binary)
    listed = BOOT + f"[search={tmp_path}]\n/usr/bin/app=daemon.elf\n"
    assert ids(listed) == {"IFS006", "IFS007"}
    assert ids(listed.replace("daemon.elf", "not-present.elf")) == set()


def test_conditional_defaults_and_unescaped_inline_close():
    data = BOOT + "[uid=1]\n[?uid=2] a=alpha\nfile={\nnot escaped }\n}\n"
    assert parse(data).entries[1].attrs["uid"] == "1"
    assert ids(data) == {"IFS001"}
