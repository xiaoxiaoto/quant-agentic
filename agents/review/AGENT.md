# Review Agent Guide

Use this guide for code review and risk assessment.

## Review stance

Lead with findings. Prioritize bugs, behavioral regressions, architecture violations, security risks, and missing tests. Keep summaries secondary.

## Checklist

- Verify the change matches the requested behavior and does not add unrelated scope.
- Check layer direction and imports against `agents/architecture/AGENT.md`.
- Check test coverage against `agents/testing/AGENT.md`.
- Flag unclear assumptions, migrations, compatibility breaks, and operational risks.
- Include file and line references for each finding when possible.

If no issues are found, say so clearly and name any residual risk or test gap.

