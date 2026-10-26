"""Render trusted currency separately from model-authored findings."""

from reviewer.infra.safety import display, inline


def marker(head_sha, version, force_id=None):
    suffix = f":{force_id}" if force_id else ""
    return f"<!-- bandwidth-infra-reviewer:{head_sha}:{version}{suffix} -->"


def render_finding(finding):
    body = (
        f"**[infra · {finding.severity}] {display(finding.title, 250)}**\n\n{display(finding.body)}"
    )
    if finding.assumptions:
        body += "\n\nAssumptions: " + "; ".join(
            display(item, 500) for item in finding.assumptions[:5]
        )
    return body


def render_review(context, outcome, run_marker):
    result, stats = outcome.result, outcome.stats
    comments, summary_findings = [], []
    for finding in result.findings:
        body = render_finding(finding)
        if finding.line is not None:
            comments.append(
                {"path": finding.file, "line": finding.line, "side": "RIGHT", "body": body}
            )
        else:
            summary_findings.append(f"- `{inline(finding.file)}`: {body}")
    lines = [
        "### Infrastructure reviewer",
        "",
        f"Found **{len(result.findings)}** supported cost concern(s); {len(comments)} posted inline.",
    ]
    if not result.findings:
        lines += [
            "No high-confidence cost concerns passed validation in the supplied context. This is not a full repository or billing audit."
        ]
    lines += [*summary_findings, "", "**Coverage**", ""]
    for entry in result.coverage:
        detail = "; ".join(display(item, 600) for item in entry.limitations)
        lines.append(f"- {entry.category}: {entry.status}" + (f" — {detail}" if detail else ""))
    lines += ["", "**Pricing evidence**", ""]
    if not context.pricing or not context.pricing.records:
        lines.append("Dollar estimation unavailable; no trusted pricing results were supplied.")
    else:
        for record in context.pricing.records:
            if not record.usable:
                lines.append(
                    f"- `{inline(record.resource)}`: estimate unavailable or assumptions are not comparable; never treated as zero."
                )
                continue
            alert = (
                " — exceeds configured monthly increase threshold"
                if record.delta >= context.policy.cost_threshold_usd_month
                else ""
            )
            lines.append(
                f"- `{inline(record.resource)}`: ${record.monthly_base:,.2f} → ${record.monthly_head:,.2f}/month (delta ${record.delta:+,.2f}){alert}."
            )
            lines.append(
                f"  Assumptions: {inline('; '.join(record.assumptions))}. Source: {inline(record.source)} {inline(record.source_version)}; generated {record.generated_at.isoformat()}."
            )
            if record.unsupported_resources:
                lines.append(
                    f"  Unsupported resources: {inline(', '.join(record.unsupported_resources))}."
                )
    if context.omissions:
        lines += ["", "**Skipped or incomplete context**", ""]
        lines.extend(f"- {display(item, 400)}" for item in context.omissions[:40])
        if len(context.omissions) > 40:
            lines.append(
                f"- {len(context.omissions) - 40} additional omissions are recorded in the run artifact."
            )
    if outcome.dropped:
        lines += [
            "",
            f"Filtered {len(outcome.dropped)} finding(s) for confidence, duplicates, or the comment cap.",
        ]
    if stats["simulated"]:
        lines += ["", "**SIMULATED OFFLINE OUTPUT — no semantic model review was performed.**"]
    footer = [
        "",
        f"Reviewed `{context.head_sha}` (baseline `{context.base_sha[:12]}`) with `{inline(stats['model'])}`; model time {stats['seconds']:.1f}s; {stats['usage'].get('inputTokens', 0)} input / {stats['usage'].get('outputTokens', 0)} output tokens; {stats['calls']} call(s).",
        f"Reviewer version `{inline(run_marker.split(':')[2].split(' ')[0])}`. React 👍/👎 on findings to help evaluate them.",
        "",
        run_marker,
    ]
    suffix = "\n".join(footer)
    body = "\n".join(lines)
    allowance = 60_000 - len(suffix) - 200
    if len(body) > allowance:
        body = (
            body[:allowance]
            + "\n\nSummary truncated to GitHub's size limit; the structured run artifact retains the complete result.\n"
        )
    return body + "\n" + suffix, comments
