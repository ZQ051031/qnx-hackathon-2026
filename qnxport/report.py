import json
from dataclasses import asdict, dataclass
from urllib.parse import quote

from . import __version__

SEVERITIES = {"none": 0, "note": 1, "warning": 2, "error": 3}
SARIF_SCHEMA = "https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/schemas/sarif-schema-2.1.0.json"


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    title: str
    hint: str
    url: str


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    column: int
    rule: Rule
    match: str = ""

    @property
    def message(self):
        return f"{self.rule.title}: {self.match}" if self.match else self.rule.title


def render(findings, format_name):
    if format_name == "json":
        return json.dumps([asdict(item) for item in findings], indent=2)
    if format_name == "sarif":
        rules = {f.rule.id: f.rule for f in findings}
        results = [
            {
                "ruleId": f.rule.id,
                "level": f.rule.severity,
                "message": {"text": f"{f.message}. {f.rule.hint}"},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": quote(f.path, safe="/")},
                        "region": {"startLine": f.line, "startColumn": f.column},
                    },
                }],
            }
            for f in findings
        ]
        return json.dumps({
            "$schema": SARIF_SCHEMA, "version": "2.1.0",
            "runs": [{"tool": {"driver": {
                "name": "qnxport", "version": __version__,
                "rules": [{
                    "id": r.id, "shortDescription": {"text": r.title},
                    "help": {"text": r.hint}, "helpUri": r.url,
                    "defaultConfiguration": {"level": r.severity},
                } for r in rules.values()],
            }}, "results": results}],
        }, indent=2)
    if format_name == "markdown":
        def cell(value):
            return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")

        lines = [
            "# qnxport report", "", f"{len(findings)} finding(s).", "",
            "| Location | Severity | Rule | Finding | Fix hint |",
            "| --- | --- | --- | --- | --- |",
        ]
        lines.extend(
            "| " + " | ".join(map(cell, (
                f"{f.path}:{f.line}:{f.column}", f.rule.severity,
                f"[{f.rule.id}]({f.rule.url})", f.message, f.rule.hint,
            ))) + " |"
            for f in findings
        )
        return "\n".join(lines)
    return "\n".join([
        *(f"{f.path}:{f.line}:{f.column}: {f.rule.severity} [{f.rule.id}] {f.message}\n"
          f"  {f.rule.hint} ({f.rule.url})" for f in findings),
        f"{len(findings)} finding(s).",
    ])


def threshold_failed(findings, threshold):
    return threshold != "none" and any(
        SEVERITIES[f.rule.severity] >= SEVERITIES[threshold] for f in findings
    )
