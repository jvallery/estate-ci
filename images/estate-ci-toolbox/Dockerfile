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
# Secret-free. Every downloaded tool is pinned by version and sha256 (upstream
# checksum files, except shellcheck, which publishes none: its digest was
# recorded from the release asset). Ubuntu packages come from the signed noble
# archive. versions.sh --assert fails the build on any drift. amd64 only.

# gitleaks 8.30.1 rebuilt from its module source with go1.27.3 (upstream's release
# binary is go1.24.11; CVE-2025-68121 in the Go stdlib). Module version and go.sum
# hash are fixed; the Go checksum database verifies the download. Same recipe as
# the fleet toolbox, so the version string repos check stays "8.30.1".
FROM registry.vallery.net/jvallery/estate-ci/mirror/docker.io/library/golang@sha256:fea12ba16096adec0f699ce1c4da2154d320450e56c1f63671f95d2a3551c77b AS gitleaks-build
ARG GITLEAKS_VERSION=8.30.1
ARG GITLEAKS_MODULE_SUM=h1:PmEvCfVI7ti9dV3s5aMZUY7sS2GxRvG3yzih7E+cS3w=
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOBIN=/out
RUN set -eu; \
    for attempt in 1 2 3; do \
      if go install -trimpath \
          -ldflags "-s -w -X github.com/zricethezav/gitleaks/v8/version.Version=${GITLEAKS_VERSION}" \
          "github.com/zricethezav/gitleaks/v8@v${GITLEAKS_VERSION}"; then break; fi; \
      if [ "$attempt" = 3 ]; then exit 1; fi; \
      sleep 15; \
    done; \
    /out/gitleaks version | grep -qx "${GITLEAKS_VERSION}"; \
    test "$(go version -m /out/gitleaks | sed -n '1s/.*: //p')" = go1.27.3; \
    test "$(go version -m /out/gitleaks | awk '$1 == "mod" && $2 == "github.com/zricethezav/gitleaks/v8" {print $3 " " $4}')" = "v${GITLEAKS_VERSION} ${GITLEAKS_MODULE_SUM}"

# Stock ARC runner 2.338.2 (Ubuntu 24.04 noble: python 3.12, ruby 3.2, php 8.3);
# the same index digest runners.yaml pins, from estate-ci's mirror.
FROM registry.vallery.net/jvallery/estate-ci/mirror/ghcr.io/actions/actions-runner@sha256:759a1d94a15b05941aba5196974ff43fe15b4cbba29b694b35cbc32a1166b232

USER root
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates curl file g++ git gnupg jq make openssh-client \
      php-cli python3 python3-pip python3-venv python3-yaml ruby-full \
      sqlite3 tar unzip xz-utils zip \
    && rm -rf /var/lib/apt/lists/*

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

# kubectl v1.35.10 (server minor 1.35; go1.26.10) and kustomize v5.8.3: the fleet
# toolbox and Argo CD tools pins, which cortex-ai-compute checks exactly.
ARG KUBECTL_VERSION=v1.35.10
ARG KUBECTL_SHA256=c49a28f1f4efc4d475ed05f47f2ce8e563bf7bef69ed77fe1b924ea6d83b6327
ARG KUSTOMIZE_VERSION=v5.8.3
ARG KUSTOMIZE_SHA256=cb9e31198d3f63b44848bd0afc4c8efc56e6cfef9c93a4a39a211940e6decd61
RUN cd /tmp/tools && fetch-verified "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" "${KUBECTL_SHA256}" kubectl \
    && install -m 0755 kubectl /usr/local/bin/kubectl \
    && fetch-verified "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2F${KUSTOMIZE_VERSION}/kustomize_${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" "${KUSTOMIZE_SHA256}" kustomize.tgz \
    && tar -xzf kustomize.tgz kustomize && install -m 0755 kustomize /usr/local/bin/kustomize \
    && rm -f /tmp/tools/*

# helm v3.22.1 (cortex-ai-compute renders need exactly v3; v4 fails; go1.26.9, no grpc in the
# binary: clears CVE-2025-68121 and CVE-2026-33186 of v3.16.3), kubeconform v0.8.0.
ARG HELM_VERSION=v3.22.1
ARG HELM_SHA256=896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea
ARG KUBECONFORM_VERSION=v0.8.0
ARG KUBECONFORM_SHA256=9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
RUN cd /tmp/tools && fetch-verified "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" "${HELM_SHA256}" helm.tgz \
    && tar -xzf helm.tgz linux-amd64/helm && install -m 0755 linux-amd64/helm /usr/local/bin/helm \
    && fetch-verified "https://github.com/yannh/kubeconform/releases/download/${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" "${KUBECONFORM_SHA256}" kubeconform.tgz \
    && tar -xzf kubeconform.tgz kubeconform && install -m 0755 kubeconform /usr/local/bin/kubeconform \
    && rm -rf /tmp/tools/*

# yq v4.54.1, crane v0.22.1 (same pin as estate-ci image-build.sh / mirror.sh).
ARG YQ_VERSION=v4.54.1
ARG YQ_SHA256=8e34fc298390875de416e6a4afcb8cabeceb25d9aa8506c1a2f9353cf702ea5f
ARG CRANE_VERSION=v0.22.1
ARG CRANE_SHA256=0ab7a1d6932a213aed964ce97666c3077fe691c8606413674a8b3e0b9ec4cda0
RUN cd /tmp/tools && fetch-verified "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64" "${YQ_SHA256}" yq \
    && install -m 0755 yq /usr/local/bin/yq \
    && fetch-verified "https://github.com/google/go-containerregistry/releases/download/${CRANE_VERSION}/go-containerregistry_Linux_x86_64.tar.gz" "${CRANE_SHA256}" crane.tgz \
    && tar -xzf crane.tgz crane && install -m 0755 crane /usr/local/bin/crane \
    && rm -f /tmp/tools/*

# shellcheck v0.11.0, actionlint v1.7.12 (the versions estate-ci's self-test uses).
ARG SHELLCHECK_VERSION=v0.11.0
ARG SHELLCHECK_SHA256=8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198
ARG ACTIONLINT_VERSION=1.7.12
ARG ACTIONLINT_SHA256=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8
RUN cd /tmp/tools && fetch-verified "https://github.com/koalaman/shellcheck/releases/download/${SHELLCHECK_VERSION}/shellcheck-${SHELLCHECK_VERSION}.linux.x86_64.tar.xz" "${SHELLCHECK_SHA256}" shellcheck.txz \
    && tar -xJf shellcheck.txz "shellcheck-${SHELLCHECK_VERSION}/shellcheck" \
    && install -m 0755 "shellcheck-${SHELLCHECK_VERSION}/shellcheck" /usr/local/bin/shellcheck \
    && fetch-verified "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz" "${ACTIONLINT_SHA256}" actionlint.tgz \
    && tar -xzf actionlint.tgz actionlint && install -m 0755 actionlint /usr/local/bin/actionlint \
    && rm -rf /tmp/tools/*

# uv 0.13.5 (the weather CI pin) and Node.js 24.22.1 with npm and corepack (pnpm),
# installed under /usr/local so PATH stays the runner's.
ARG UV_VERSION=0.13.5
ARG UV_SHA256=f68d291c75783122dad58ab77def0e13f3233f358731db5d6d7e8715603b928f
ARG NODE_VERSION=v24.22.1
ARG NODE_SHA256=6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6
RUN cd /tmp/tools && fetch-verified "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" "${UV_SHA256}" uv.tgz \
    && tar -xzf uv.tgz && install -m 0755 uv-x86_64-unknown-linux-gnu/uv uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/ \
    && fetch-verified "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz" "${NODE_SHA256}" node.txz \
    && tar -xJf node.txz -C /usr/local --strip-components=1 \
    && rm -f /usr/local/CHANGELOG.md /usr/local/README.md /usr/local/LICENSE \
    && rm -rf /tmp/tools/*

# gh v2.103.3: ADR-0009 exception E-1 (upstream supply-chain attestation
# verification). Only for `gh attestation verify` of upstream release artifacts
# (sentinel frigate-promotion); never for forge API calls, which go through the
# forge wrapper (forge-api.sh). The image carries no token.
ARG GH_VERSION=2.103.3
ARG GH_SHA256=4c806978ffce1cec70853302a56d70ac6da4f19bae7adca4e1a8ab00633dd91d
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

COPY --from=gitleaks-build /out/gitleaks /usr/local/bin/gitleaks

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
