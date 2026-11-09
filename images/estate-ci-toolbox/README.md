# estate-ci-toolbox

The estate CI toolchain image (ADR-0004, estate-ci#3). Built FROM the stock ARC
runner image, so it serves both as `runner_image` (label-swap sets whose `run:`
steps execute in the runner container: proxmox-infrastructure, home-dotfiles,
AI_presentation) and as the `job` sidecar image for repositories without their
own CI image. It replaces the fleet `agents-arc-runner-toolbox`
and removes the need for `actions/setup-*` (R2). Secret-free; built in-cluster
by `.github/workflows/build.yml` (Kaniko on `estate-ci-buildcheck` for PRs,
`estate-ci-build` with push when the workflow is dispatched on `main`; there
is no push trigger) and published as
`registry.vallery.net/jvallery/estate-ci/toolbox@sha256:<digest>`.

| Tool | Version | Source and check |
|---|---|---|
| Base: stock ARC runner | actions-runner 2.338.2 (Ubuntu 24.04 noble) amd64 | `mirrors.txt`, same index digest as `runners.yaml` `runner_image`; startup contract unchanged |
| python3 + pip + venv + PyYAML | 3.12 (apt) | noble archive (`python3-yaml`); `PIP_BREAK_SYSTEM_PACKAGES=1`, user installs to `~/.local` |
| uv / uvx | 0.13.5 | GitHub release, sha256 |
| Node.js + npm + corepack (pnpm) | v24.22.1 | nodejs.org, SHASUMS256 |
| ruby | 3.2 (apt `ruby-full`) | noble archive |
| php-cli | 8.3 (apt) | noble archive (wordpress `php -l`) |
| kubectl | v1.35.10 | dl.k8s.io, sha256 (fleet toolbox pin) |
| kustomize | v5.8.3 | GitHub release, sha256 (fleet/Argo CD pin; cortex checks it exactly) |
| helm | v3.22.1 | get.helm.sh, sha256 (cortex renders need v3) |
| kubeconform | v0.8.0 | GitHub release, CHECKSUMS |
| yq (mikefarah) | v4.54.1 | GitHub release, sha256 |
| jq, git, curl, make, zip/unzip, xz, sqlite3, g++, openssh-client, file | apt | noble archive |
| crane | v0.22.1 | GitHub release, sha256 (same pin as `image-build.sh`) |
| gitleaks | 8.30.1 | built from module source with go1.27.3 (go.sum hash pinned; fleet recipe) |
| shellcheck | v0.11.0 | GitHub release, sha256 recorded from the asset |
| actionlint | 1.7.12 | GitHub release, checksums.txt |
| gh | 2.103.3 | GitHub release, checksums.txt. **Exception E-1** (ADR-0011, accepted under D-373): only `gh attestation verify` of upstream artifacts (sentinel frigate-promotion); no token in the image |
| monitoring-stack Static Validation archives (ms#1560) | kustomize v5.4.2, kubeconform v0.6.7, prometheus 3.13.5, alertmanager 0.34.1, tempo 3.0.2, blackbox_exporter 0.29.1 | Upstream release archives kept whole under their original basenames at `/opt/estate-ci-tools/<tool>/<version>/` (off PATH), each sha256-verified against the monitoring-stack pins and the upstream checksum files. Callers check the archive sha256 and use it only on an exact match. Bump together with monitoring-stack |

`versions.sh --assert` runs at build time and fails on any drift; the result is
in the image at `/usr/local/share/estate-ci-toolbox/versions.txt`.

Environment: the runner's own (user `runner`, uid/gid 1001, `HOME=/home/runner`,
runner `PATH`; no ENTRYPOINT/CMD/WORKDIR change) plus `LANG=C.UTF-8` and
`PIP_BREAK_SYSTEM_PACKAGES=1`. Node lives in `/usr/local`. CVE-2026-59873: the
runner-bundled npm with tar < 7.5.19 is removed from `externals`, as in the
fleet toolbox.

Not included, by design: Docker or any container runtime, cloud CLIs,
credentials. `gh` is present only for exception E-1; forge API calls still go
through `forge-api.sh`. Repository-specific stacks (Playwright browsers,
PostgreSQL server, ESPHome) belong in that repository's own CI image.

## Using it

In `runners.yaml`, set the repository's `ci` profile `job_image` to the
published digest and `job_uid: 1001`, then call `checks.yml` with
`in-job-image: true`. Version changes: bump the `ARG`s (version and sha256)
and `versions.sh` together; the PR build proves the image.
