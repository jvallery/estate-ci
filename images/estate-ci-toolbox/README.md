# estate-ci-toolbox

The default `job` sidecar image for repositories without their own CI image
(ADR-0004, estate-ci#3). It replaces the fleet `agents-arc-runner-toolbox`
and removes the need for `actions/setup-*` (R2). Secret-free; built in-cluster
by `.github/workflows/build.yml` (Kaniko on `estate-ci-buildcheck` for PRs,
`estate-ci-build` with push on `main`) and published as
`registry.vallery.net/jvallery/estate-ci/toolbox@sha256:<digest>`.

| Tool | Version | Source and check |
|---|---|---|
| Ubuntu base | 24.04 (noble) amd64 | `mirrors.txt`, digest-pinned |
| python3 + pip + venv + PyYAML | 3.12 (apt) | noble archive (`python3-yaml`); `PIP_BREAK_SYSTEM_PACKAGES=1`, user installs to `~/.local` |
| uv / uvx | 0.13.5 | GitHub release, sha256 |
| Node.js + npm + corepack (pnpm) | v24.22.1 | nodejs.org, SHASUMS256 |
| ruby | 3.2 (apt `ruby-full`) | noble archive |
| php-cli | 8.3 (apt) | noble archive (wordpress `php -l`) |
| kubectl | v1.35.10 | dl.k8s.io, sha256 (fleet toolbox pin) |
| kustomize | v5.8.3 | GitHub release, sha256 (fleet/Argo CD pin; cortex checks it exactly) |
| helm | v3.16.3 | get.helm.sh, sha256 (cortex renders need v3) |
| kubeconform | v0.8.0 | GitHub release, CHECKSUMS |
| yq (mikefarah) | v4.54.1 | GitHub release, sha256 |
| jq, git, curl, make, zip/unzip, xz, sqlite3, g++, openssh-client, file | apt | noble archive |
| crane | v0.22.1 | GitHub release, sha256 (same pin as `image-build.sh`) |
| gitleaks | 8.30.1 | built from module source with go1.27.3 (go.sum hash pinned; fleet recipe) |
| shellcheck | v0.11.0 | GitHub release, sha256 recorded from the asset |
| actionlint | 1.7.12 | GitHub release, checksums.txt |
| gh | 2.103.3 | GitHub release, checksums.txt. **ADR-0009 exception E-1**: only `gh attestation verify` of upstream artifacts (sentinel frigate-promotion); no token in the image |

`versions.sh --assert` runs at build time and fails on any drift; the result is
in the image at `/usr/local/share/estate-ci-toolbox/versions.txt`.

Environment: `HOME=/home/ci` (uid/gid 1001, the `job_uid` runner sets use),
`LANG=C.UTF-8`, `PATH` includes `~/.local/bin` and `/opt/node/bin`. The image
sets no entrypoint; the runner Pod runs `sidecar-agent.sh` in it.

Not included, by design: Docker or any container runtime, cloud CLIs,
credentials. `gh` is present only for exception E-1; forge API calls still go
through `forge-api.sh`. Repository-specific stacks (Playwright browsers,
PostgreSQL server, ESPHome) belong in that repository's own CI image.

## Using it

In `runners.yaml`, set the repository's `ci` profile `job_image` to the
published digest and `job_uid: 1001`, then call `checks.yml` with
`in-job-image: true`. Version changes: bump the `ARG`s (version and sha256)
and `versions.sh` together; the PR build proves the image.
