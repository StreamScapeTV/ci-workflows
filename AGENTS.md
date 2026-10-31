# ci-workflows: agent instructions

Integration branch: `main`. Agent State project key: `ci-workflows`.

Use the installed `$iptv-workflow` and `$iptv-platform` skills before engineering
work. The platform skill selects this repository's architecture, testing and
release/deployment instructions. Keep current repository feature specifications
and tracked implementation contracts authoritative within their scopes.

| Scenario | Skill |
| --- | --- |
| Assignment, claims or operational state | `$iptv-agent-state` |
| Implementation, source publication or handoff | `$iptv-workflow` |
| Candidate review or integration review | `$iptv-review` |
| Local validation, required CI or release evidence | `$iptv-ci` |
| Repository architecture, migrations or deployment contract | `$iptv-platform` |

Use local Git, `gh` and the approved Supabase CLI. Resolve skills from the installed
skill list, `.agents/skills/<skill>/SKILL.md`, `~/.agents/skills/<skill>/SKILL.md`,
or `$IPTV_SKILLS_ROOT/<skill>/SKILL.md`. If missing, install the existing canonical
checkout with `python3 <codex-skills>/iptv-skills/scripts/install.py --project <repository-path> --apply`
(or `--user --apply` for user installation), then read the selected skill.

These routes replace the shared web bootstrap. Do not require a browser, Drive
archive or separate shared-policy checkout for local engineering. Skill
installation does not invent an actor, assign work, change live services or
replace an explicit required test/review/release gate.
