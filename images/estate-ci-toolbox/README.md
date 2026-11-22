# estate-ci-toolbox

The estate CI toolchain image (ADR-0004, estate-ci#3). Built FROM the stock ARC
runner image, so it serves both as `runner_image` (label-swap sets whose `run:`
steps execute in the runner container: proxmox-infrastructure, home-dotfiles,
AI_presentation) and as the `job` sidecar image for repositories without their
own CI image. It replaces the fleet `agents-arc-runner-toolbox`
and removes the need for `actions/setup-*` (R2). Secret-free; built in-cluster
by `.github/workflows/build.yml` (Kaniko on `estate-ci-buildcheck` for PRs,
`estate-ci-build` with push on a push to `main` that changes this directory, or
on dispatch; its `pin` job then opens the digest pin PR) and published as
`registry.vallery.net/jvallery/estate-ci/toolbox@sha256:<digest>`.

| Tool | Version | Source and check |
|---|---|---|
| Base: stock ARC runner | actions-runner 2.338.2 (Ubuntu 24.04 noble) amd64 | `mirrors.txt`, same index digest as `runners.yaml` `runner_image`; startup contract unchanged |
| python3 + pip + venv + PyYAML | 3.12 (apt) | noble archive (`python3-yaml`); `PIP_BREAK_SYSTEM_PACKAGES=1`, user installs to `~/.local` |
| uv / uvx | 0.13.9 | GitHub release, sha256 |
| Node.js + npm + corepack (pnpm) | v24.22.3 | nodejs.org, SHASUMS256 |
| ruby | 3.2 (apt `ruby-full`) | noble archive |
| php-cli | 8.3 (apt) | noble archive (wordpress `php -l`) |
| kubectl | v1.35.10 | dl.k8s.io, sha256 (fleet toolbox pin) |
| kustomize | v5.8.3 | built from module source with go1.27.4, module sum `h1:SJXKgCEb4Ru+8PrhwQ+ORAASc2rPWBneHjh4fKTXPWY=` asserted (`go version -m`) (fleet/Argo CD pin; cortex checks it exactly) |
| helm | v3.22.1 | built from module source with go1.27.4, module sum `h1:uo2SouzAxMkKZTNzZ1lLP/c/Bu7xfUJKYDaYMxMHvk8=` asserted (`go version -m`); upstream version and k8s 1.37 lint ldflags (cortex renders need v3) |
| kubeconform | v0.8.0 | built from module source with go1.27.4, module sum `h1:loDYd3a3spjIFrauqW67CDKF55Oo9R6uvC8AvpVd4ug=` asserted (`go version -m`) |
| yq (mikefarah) | v4.54.3 | built from module source with go1.27.4, module sum `h1:1SnmdehRmL1Z56SuPH8IQUW3D73Q74F3mpGWW0jAb3E=` asserted (`go version -m`) |
| jq, git, curl, make, zip/unzip, xz, sqlite3, g++, openssh-client, file | apt | noble archive |
| zsh | 5.9 (apt) | noble archive (`zsh -f -n` syntax checks and zsh self-tests; home-dotfiles#104 retires the workflow's inline package download) |
| crane | v0.22.1 | built from module source with go1.27.4, module sum `h1:RZuuSYhTvlDvtsK+NkutoCZ//C0X2ebLK8X8l3ULs84=` asserted (`go version -m`) (same pin as `image-build.sh`) |
| gitleaks | 8.30.1 | built from module source with go1.27.4, module sum `h1:PmEvCfVI7ti9dV3s5aMZUY7sS2GxRvG3yzih7E+cS3w=` asserted (`go version -m`) (fleet recipe) |
| shellcheck | v0.11.0 | GitHub release, sha256 recorded from the asset |
| actionlint | 1.7.12 | built from module source with go1.27.4, module sum `h1:vQ4GeJN86C0QH+gTUQcs8McmK62OLT3kmakPMtEWYnY=` asserted (`go version -m`) |
| gh | 2.105.0 | GitHub release, checksums.txt. **Exception E-1** (ADR-0011, accepted under D-373): only `gh attestation verify` of upstream artifacts (sentinel frigate-promotion); no token in the image |
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
