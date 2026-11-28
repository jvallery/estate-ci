# estate-ci-toolbox (ADR-0004, estate-ci#3): the estate CI toolchain image. It
# replaces the fleet agents-arc-runner-toolbox and serves two roles:
#   - runner_image of label-swap sets (proxmox-infrastructure, home-dotfiles,
#     AI_presentation): their existing `run:` steps execute in it directly;
#   - job sidecar image (job_uid 1001) for repos without their own CI image.
# So it is built FROM the stock ARC runner image (estate-ci's mirror, digest
# pinned) and keeps the runner's startup contract untouched: no ENTRYPOINT, CMD,
# WORKDIR, HOME or PATH change; it only adds binaries and ends as USER runner
# (uid 1001). Same rule as the fleet toolbox (a changed startup contract broke
# the ARC JIT handshake, agents#3088 probe 3).
#
# Secret-free. Every downloaded release binary or archive is pinned by version and
# sha256 (upstream checksum files, except shellcheck, which publishes none: its
# digest was recorded from the release asset). The eight Go tools of the go-build
# stage are pinned by module version and h1 module sum (from sum.golang.org,
# asserted with `go version -m`), downloaded through proxy.golang.org and verified
# against sum.golang.org at build time. Ubuntu packages come from the signed noble
# archive. versions.sh --assert fails the build on any drift. amd64 only.

# Go tools rebuilt from their module source with go1.27.4 (newest Go; CVE-2026-97054 and the
# other stdlib rows are fixed in 1.27.4/1.26.11). Upstream
# release binaries of helm, kustomize, kubeconform, yq, crane and actionlint are linked with older
# Go (go1.26.1 to go1.27.3) and none has a newer release (read 2026-11-22). Same recipe as
# the gitleaks build before it, for all eight (cosign added for estate-ci#125): the exact module version, its h1 module sum
# asserted from `go version -m` (values from sum.golang.org, listed in the README), the version
# ldflags upstream sets, and go1.27.4 asserted per binary. Module dependencies stay as each
# release pins them (go install never bumps them), so x/net, x/crypto and x/text rows remain
# until upstream releases. kubectl and gh stay upstream release binaries with sha256 pins.
FROM registry.vallery.net/jvallery/estate-ci/mirror/docker.io/library/golang@sha256:fb7cceb528b47141eccd9b60bf3cbd27d5ef5f8cf5b7d338bec866da495b50dc AS go-build
ARG GO_VERSION=go1.27.4
ARG GITLEAKS_VERSION=8.30.1
ARG GITLEAKS_MODULE_SUM=h1:PmEvCfVI7ti9dV3s5aMZUY7sS2GxRvG3yzih7E+cS3w=
ARG HELM_VERSION=v3.22.1
ARG HELM_MODULE_SUM=h1:uo2SouzAxMkKZTNzZ1lLP/c/Bu7xfUJKYDaYMxMHvk8=
ARG KUSTOMIZE_VERSION=v5.8.3
ARG KUSTOMIZE_MODULE_SUM=h1:SJXKgCEb4Ru+8PrhwQ+ORAASc2rPWBneHjh4fKTXPWY=
ARG KUBECONFORM_VERSION=v0.8.0
ARG KUBECONFORM_MODULE_SUM=h1:loDYd3a3spjIFrauqW67CDKF55Oo9R6uvC8AvpVd4ug=
ARG YQ_VERSION=v4.54.3
ARG YQ_MODULE_SUM=h1:1SnmdehRmL1Z56SuPH8IQUW3D73Q74F3mpGWW0jAb3E=
ARG CRANE_VERSION=v0.22.1
ARG CRANE_MODULE_SUM=h1:RZuuSYhTvlDvtsK+NkutoCZ//C0X2ebLK8X8l3ULs84=
ARG ACTIONLINT_VERSION=1.7.12
ARG ACTIONLINT_MODULE_SUM=h1:vQ4GeJN86C0QH+gTUQcs8McmK62OLT3kmakPMtEWYnY=
# cosign v3.1.6 (estate-ci#125, ADR-0012): the sign sidecar of build sets signs with it, and
# verify-image.sh verifies. Module sum from sum.golang.org; version ldflag as upstream's Makefile.
ARG COSIGN_VERSION=v3.1.6
ARG COSIGN_MODULE_SUM=h1:8RiFff6JGIysokSck6pZo7uuuiYoYPZq31CRCVBOUDc=
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOBIN=/out
RUN set -eu; \
    test "$(go env GOVERSION)" = "${GO_VERSION}"; \
    build() { \
      bin="$1"; pkg="$2"; mainmod="$3"; ver="$4"; sum="$5"; ldx="$6"; \
      for attempt in 1 2 3; do \
        if go install -trimpath -ldflags "-s -w ${ldx}" "${pkg}@${ver}"; then break; fi; \
        if [ "$attempt" = 3 ]; then exit 1; fi; \
        sleep 15; \
      done; \
      test "$(go version -m "/out/${bin}" | sed -n '1s/.*: //p')" = "${GO_VERSION}"; \
      test "$(go version -m "/out/${bin}" | awk -v m="${mainmod}" '$1 == "mod" && $2 == m {print $3 " " $4}')" = "${ver} ${sum}"; \
    }; \
    build gitleaks github.com/zricethezav/gitleaks/v8 github.com/zricethezav/gitleaks/v8 "v${GITLEAKS_VERSION}" "${GITLEAKS_MODULE_SUM}" \
      "-X github.com/zricethezav/gitleaks/v8/version.Version=${GITLEAKS_VERSION}"; \
    build helm helm.sh/helm/v3/cmd/helm helm.sh/helm/v3 "${HELM_VERSION}" "${HELM_MODULE_SUM}" \
      "-X helm.sh/helm/v3/internal/version.version=${HELM_VERSION} -X helm.sh/helm/v3/internal/version.gitTreeState=clean \
       -X helm.sh/helm/v3/pkg/lint/rules.k8sVersionMajor=1 -X helm.sh/helm/v3/pkg/lint/rules.k8sVersionMinor=37 \
       -X helm.sh/helm/v3/pkg/chartutil.k8sVersionMajor=1 -X helm.sh/helm/v3/pkg/chartutil.k8sVersionMinor=37"; \
    build kustomize sigs.k8s.io/kustomize/kustomize/v5 sigs.k8s.io/kustomize/kustomize/v5 "${KUSTOMIZE_VERSION}" "${KUSTOMIZE_MODULE_SUM}" ""; \
    build kubeconform github.com/yannh/kubeconform/cmd/kubeconform github.com/yannh/kubeconform "${KUBECONFORM_VERSION}" "${KUBECONFORM_MODULE_SUM}" \
      "-X main.version=${KUBECONFORM_VERSION}"; \
    build yq github.com/mikefarah/yq/v4 github.com/mikefarah/yq/v4 "${YQ_VERSION}" "${YQ_MODULE_SUM}" ""; \
    build crane github.com/google/go-containerregistry/cmd/crane github.com/google/go-containerregistry "${CRANE_VERSION}" "${CRANE_MODULE_SUM}" \
      "-X github.com/google/go-containerregistry/cmd/crane/cmd.Version=${CRANE_VERSION#v}"; \
    build actionlint github.com/rhysd/actionlint/cmd/actionlint github.com/rhysd/actionlint "v${ACTIONLINT_VERSION}" "${ACTIONLINT_MODULE_SUM}" \
      "-X github.com/rhysd/actionlint.version=${ACTIONLINT_VERSION}"; \
    build cosign github.com/sigstore/cosign/v3/cmd/cosign github.com/sigstore/cosign/v3 "${COSIGN_VERSION}" "${COSIGN_MODULE_SUM}" \
      "-X sigs.k8s.io/release-utils/version.gitVersion=${COSIGN_VERSION}"; \
    ls -l /out

# Stock ARC runner 2.338.2 (Ubuntu 24.04 noble: python 3.12, ruby 3.2, php 8.3);
# the upstream 2026-11-11 re-push (index 6d0b2d80, amd64 4037925f; AMC-SEC-A2-10),
# from estate-ci's mirror; runners.yaml moves to the same digest in the same delivery.
FROM registry.vallery.net/jvallery/estate-ci/mirror/ghcr.io/actions/actions-runner@sha256:6d0b2d80d80f143f77dcb676e50920100bbb547e39e883454d57f45ab8c31300

USER root
ARG DEBIAN_FRONTEND=noninteractive
# Upgrade the inherited APT and Brotli packages before installing other tools.
# Disable download retries until APT meets the CVE-2026-109518 security floor.
# The Brotli floor covers CVE-2026-111497. Then a full upgrade (AMC-SEC-A2-10):
# linux-libc-dev must reach 6.8.0-151.151 or the build fails.
RUN set -eu; \
    apt-get -o Acquire::Retries=0 update; \
    apt-get -o Acquire::Retries=0 install -y --only-upgrade --no-install-recommends \
      apt libapt-pkg6.0t64 libbrotli1; \
    dpkg --compare-versions "$(dpkg-query -W -f='${Version}' apt)" ge 2.8.4ubuntu0.1; \
    dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libapt-pkg6.0t64)" ge 2.8.4ubuntu0.1; \
    dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libbrotli1)" ge 1.1.0-2ubuntu0.1; \
    apt-get -o Acquire::Retries=0 upgrade -y --no-install-recommends; \
    apt-get install -y --no-install-recommends \
      ca-certificates curl file g++ git gnupg jq make openssh-client \
      php-cli python3 python3-pip python3-venv python3-yaml ruby-full \
      sqlite3 tar unzip xz-utils zip zsh; \
    if dpkg-query -W linux-libc-dev >/dev/null 2>&1; then \
      dpkg --compare-versions "$(dpkg-query -W -f='${Version}' linux-libc-dev)" ge 6.8.0-151.151; \
    fi; \
    rm -rf /var/lib/apt/lists/*

# CVE-2026-59873: drop every runner-bundled npm whose tar is older than 7.5.19
# (node20's npm carries tar 6.2.1), as the fleet toolbox does (#5812); the
# runner runs JS actions with `node` only.
RUN set -eu; \
    for pkg in $(find /home/runner/externals -path '*/node_modules/npm/node_modules/tar/package.json'); do \
      version="$(jq -r .version "$pkg")"; \
      if [ "$(printf '%s\n%s\n' 7.5.19 "$version" | sort -V | head -n1)" != 7.5.19 ]; then \
        npm_dir="${pkg%/node_modules/tar/package.json}"; \
        node_root="${npm_dir%/lib/node_modules/npm}"; \
        rm -rf "$npm_dir" "$node_root/bin/npm" "$node_root/bin/npx"; \
      fi; \
    done; \
    for pkg in $(find /home/runner/externals -path '*/node_modules/tar/package.json'); do \
      version="$(jq -r .version "$pkg")"; \
      test "$(printf '%s\n%s\n' 7.5.19 "$version" | sort -V | head -n1)" = 7.5.19; \
    done

# fetch <url> <sha256> <file>: download and verify, or fail the build.
RUN printf '%s\n' '#!/bin/sh' 'set -eu' \
      'curl -fsSL --retry 5 --retry-delay 10 --retry-all-errors -o "$3" "$1"' \
      'echo "$2  $3" | sha256sum -c -' > /usr/local/bin/fetch-verified \
    && chmod 0755 /usr/local/bin/fetch-verified && mkdir -p /tmp/tools

# kubectl v1.35.10 (server minor 1.35; go1.26.10, upstream binary). kustomize v5.8.3 (the fleet
# toolbox and Argo CD tools pin, which cortex-ai-compute checks exactly) comes from go-build.
ARG KUBECTL_VERSION=v1.35.10
ARG KUBECTL_SHA256=c49a28f1f4efc4d475ed05f47f2ce8e563bf7bef69ed77fe1b924ea6d83b6327
RUN cd /tmp/tools && fetch-verified "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" "${KUBECTL_SHA256}" kubectl \
    && install -m 0755 kubectl /usr/local/bin/kubectl \
    && rm -f /tmp/tools/*

# helm v3.22.1 (cortex-ai-compute renders need exactly v3; v4 fails), kubeconform v0.8.0, yq v4.54.3
# and crane v0.22.1 (same crane pin as estate-ci image-build.sh / mirror.sh) come from go-build.

# shellcheck v0.11.0 (Haskell, upstream binary); actionlint v1.7.12 comes from go-build (the
# versions estate-ci's self-test uses).
ARG SHELLCHECK_VERSION=v0.11.0
ARG SHELLCHECK_SHA256=8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198
RUN cd /tmp/tools && fetch-verified "https://github.com/koalaman/shellcheck/releases/download/${SHELLCHECK_VERSION}/shellcheck-${SHELLCHECK_VERSION}.linux.x86_64.tar.xz" "${SHELLCHECK_SHA256}" shellcheck.txz \
    && tar -xJf shellcheck.txz "shellcheck-${SHELLCHECK_VERSION}/shellcheck" \
    && install -m 0755 "shellcheck-${SHELLCHECK_VERSION}/shellcheck" /usr/local/bin/shellcheck \
    && rm -rf /tmp/tools/*

# uv 0.13.9 and Node.js 24.22.3 (newest releases, 2026-11-20 refresh) with npm and corepack (pnpm),
# installed under /usr/local so PATH stays the runner's.
ARG UV_VERSION=0.13.9
ARG UV_SHA256=a7fbaff2b9a271d85c1bdb540941090fd1ce011797b4abeb48e4dcd39c2471d5
ARG NODE_VERSION=v24.22.3
ARG NODE_SHA256=4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb
RUN cd /tmp/tools && fetch-verified "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" "${UV_SHA256}" uv.tgz \
    && tar -xzf uv.tgz && install -m 0755 uv-x86_64-unknown-linux-gnu/uv uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/ \
    && fetch-verified "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz" "${NODE_SHA256}" node.txz \
    && tar -xJf node.txz -C /usr/local --strip-components=1 \
    && rm -f /usr/local/CHANGELOG.md /usr/local/README.md /usr/local/LICENSE \
    && rm -rf /tmp/tools/*

# gh v2.105.0: ADR-0009 exception E-1 (upstream supply-chain attestation
# verification). Only for `gh attestation verify` of upstream release artifacts
# (sentinel frigate-promotion); never for forge API calls, which go through the
# forge wrapper (forge-api.sh). The image carries no token.
ARG GH_VERSION=2.105.0
ARG GH_SHA256=79e7ab5db738c48777b696e374f8b428f0647debfb424f44d2f426370407fe2f
RUN cd /tmp/tools && fetch-verified "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" "${GH_SHA256}" gh.tgz \
    && tar -xzf gh.tgz "gh_${GH_VERSION}_linux_amd64/bin/gh" \
    && install -m 0755 "gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh \
    && rm -rf /tmp/tools/*

# monitoring-stack Static Validation archives (ms ci/static-validation lib.sh and rules.sh;
# consumer ms#1560): the UPSTREAM RELEASE ARCHIVES under their original basenames, not
# unpacked binaries, because the callers sha256-check the archive and use this copy only
# on an exact match, falling back to the download. Path:
# /opt/estate-ci-tools/<tool>/<version>/<basename>. Off PATH, so kustomize v5.8.3 and
# kubeconform v0.8.0 above stay authoritative. sha256s equal the ms pins (prometheus,
# alertmanager, blackbox_exporter, tempo) and the upstream checksum files (all six;
# ms pins only the kustomize and kubeconform versions). amd64 only, like this image.
# Bump together with monitoring-stack's pins; versions.sh asserts every archive.
RUN set -eu; \
    archive() { \
      dir="/opt/estate-ci-tools/$1/$2"; mkdir -p "$dir"; \
      fetch-verified "$3" "$4" "$dir/$(basename "$3")"; \
      chmod 0444 "$dir/$(basename "$3")"; \
    }; \
    archive kustomize v5.4.2 "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv5.4.2/kustomize_v5.4.2_linux_amd64.tar.gz" 881c6e9007c7ea2b9ecc214d13f4cdd1f837635dcf4db49ce4479898f7d911a3; \
    archive kubeconform v0.6.7 "https://github.com/yannh/kubeconform/releases/download/v0.6.7/kubeconform-linux-amd64.tar.gz" 95f14e87aa28c09d5941f11bd024c1d02fdc0303ccaa23f61cef67bc92619d73; \
    archive prometheus 3.13.5 "https://github.com/prometheus/prometheus/releases/download/v3.13.5/prometheus-3.13.5.linux-amd64.tar.gz" 0388822ace519c5a3a8ef556a541ce1b536e21d872f82deded465bd27a5a0699; \
    archive alertmanager 0.34.1 "https://github.com/prometheus/alertmanager/releases/download/v0.34.1/alertmanager-0.34.1.linux-amd64.tar.gz" 265b9d1e55ef0d5306a436018af6d2b686c2ce051f03d968f7464ecb1372a7e8; \
    archive tempo 3.0.2 "https://github.com/grafana/tempo/releases/download/v3.0.2/tempo_3.0.2_linux_amd64.tar.gz" d2b95c01774c81c4494c4162e2f3511699a66b46506c96028745f5d4f0a78086; \
    archive blackbox_exporter 0.29.1 "https://github.com/prometheus/blackbox_exporter/releases/download/v0.29.1/blackbox_exporter-0.29.1.linux-amd64.tar.gz" b82b81839e6317e84fdd367f46d7ddd3b773fa09cbe2e3d5633a59171d741e76

COPY --from=go-build /out/gitleaks /out/helm /out/kustomize /out/kubeconform /out/yq /out/crane /out/actionlint /out/cosign /usr/local/bin/

# Hosted-runner parity (as the fleet toolbox): UTF-8 locale and pip installs
# into the PEP 668 system python. No other env change.
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PIP_BREAK_SYSTEM_PACKAGES=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

# Assert every version and write the manifest the README documents.
COPY versions.sh /usr/local/share/estate-ci-toolbox/versions.sh
RUN rm -rf /tmp/tools \
    && sh /usr/local/share/estate-ci-toolbox/versions.sh --assert > /usr/local/share/estate-ci-toolbox/versions.txt \
    && cat /usr/local/share/estate-ci-toolbox/versions.txt
# Restore the runner's user (startup contract above).
USER runner
