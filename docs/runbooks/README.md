# Runbooks

The current procedures for this repository. Each one lives where it is linked; this page only indexes them.

| Procedure | Use it when | Where |
|---|---|---|
| Onboarding a repository | A jvallery repository needs runner sets, the estate workflows, required checks or an image build and pin flow; each step has its rollback | [docs/MIGRATION-RUNBOOK.md](../MIGRATION-RUNBOOK.md) |
| After a cve-gate block | `image-build.yml` refused to push or pin a digest because of a CVE finding | "After a cve-gate block" in [README.md](../../README.md) |
| Moving a mirror to `crane copy` | An image that is only `FROM <upstream>@<digest>` is still built by Kaniko | "Image mirrors" in [README.md](../../README.md) |
| Image tags and retention | Choosing a tag for a new image, or finding out why a digest was removed from the registry | [docs/image-tags-and-retention.md](../image-tags-and-retention.md) |
| A runner-set change or a broken foundation script | Rolling out or undoing a `runners.yaml` change, a controller chart move, or a script that stops every job | "Hazards" in [AGENTS.md](../../AGENTS.md) |
| Re-running jobs that lost their runner | Jobs stay queued after a listener restart | `gh run rerun <run-id> --failed`, or [`orphan-redispatch.yml`](../../.github/workflows/orphan-redispatch.yml) |

The procedures for the CI alerts and for checks against the running cluster are kept with the estate's private operating documentation.
