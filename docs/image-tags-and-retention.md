# Image tag contract and retention classes

The tag contract for images built on estate-ci runners, and the retention
classes the registry applies to them. The decisions are in
[ADR-0003](adr/0003-builds-registry-and-caching.md) (builds and registry) and
[ADR-0006](adr/0006-pins-and-delivery.md) (digest pins); this page states the
contract in one place.

The tag table and the retention table came from `jvallery/agents`
`docs/container-registry-promotion-policy.md`, which is superseded
(jvallery/estate-platform#60). That document was written for GHCR. The rows
below are the ones that still hold for `registry.vallery.net`, restated for the
one tag this repository pushes.

## Tag contract

`deploy/foundation/bin/image-build.sh` is the only place a tag is formed.

| Source | Tag | Pushed | Use |
|---|---|---|---|
| Commit on the default branch (push or dispatch on a `<repo>-build` set) | `gha-<12-char-sha>` | Yes | Build identity. The build reads the digest back and outputs `registry.vallery.net/<image>@sha256:...`. |
| Pull request (`build-check` set) | none | No | Build-only check with `--no-push`. PR code never runs where the push credential is mounted (ADR-0005). |
| Upstream mirror (a `mirrors.txt` row, dispatched on main) | none of ours | Copied | A byte-for-byte copy to `jvallery/estate-ci/mirror/<upstream path>`; it is pinned as `registry.vallery.net/<path>@<the upstream digest>`. |

Rules that carry over unchanged:

- A tag is for discovery and inspection. Nothing deploys a tag: Argo CD deploys
  only a digest committed in Git by the pin job (ADR-0006, STD-CI-13).
- No `latest`, and no environment alias such as `dev`, `staging` or `prod`.
  Promotion is a change of the pinned digest in the repository that owns the
  manifest.

Tag forms from the superseded policy that this repository does not emit:
`sha-<12-char-sha>` (native CI's form, replaced by `gha-`), `branch-<name>`,
`pr-<number>`, `tag-<git-tag>`, `release-<version>` and caller-defined extra
tags. Do not add one without an ADR change.

## Retention classes

`jvallery/storage-infra` owns registry retention and runs it: the daily
`registry-origin/zot-retention` job under
`deploy/registry-origin/retention/policy.json`, described in its
`docs/runbooks/zot-content-prune.md`. The counts, caps and schedule live there
and are not repeated here. This table says what a build may rely on, and how
each class of the superseded policy maps to it.

| Image class | Superseded policy (GHCR) | In force for `registry.vallery.net` |
|---|---|---|
| A digest that Git desired state references | Never deleted by cleanup | Protected: every Argo source at its pinned revision and default branch, every live workload template and its revision history, running pods and Tier-0 node images hold their digests. |
| A production or release digest that was replaced | Kept until explicitly retired | Kept while a live revision history, a pinned Argo source or the reviewed protected baseline still names it. A rollback target outside those needs a row in storage-infra's protected baseline. |
| Current staging digest | Kept while deployed and 90 days after | No separate class: protected while referenced, as above. |
| Default-branch builds that were never pinned | Latest 30 builds or 90 days | The newest builds per repository are kept, the rest are reclaimed after a minimum age; the numbers are in `policy.json`. |
| PR tags and branch tags | Deleted on close or after 30 days | Not pushed, so there is nothing to retain. |
| Layer cache paths (`cache/<repo>`) | Not covered | 7 days, where a repository enabled Kaniko caching (ADR-0003). |
| Failed or orphaned test images | Deleted after investigation | Unchanged. |

The rule that survives intact: automated cleanup never deletes a digest that
Git desired state references. A build that needs a digest kept for any other
reason asks storage-infra for a protected-baseline row; it does not re-tag. A
change to a retention window ships with a capacity projection (STD-OPS-05) in
storage-infra.
