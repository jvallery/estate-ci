# estate-ci-toolbox: the default `job` image for repositories without their own CI
# image (ADR-0004, estate-ci#3). It replaces the fleet agents-arc-runner-toolbox.
#
# Secret-free. Bases come from estate-ci's own mirrors (mirrors.txt) in
# registry.vallery.net, pinned by digest. Every downloaded tool is pinned by
# version and sha256 (upstream checksum files, except shellcheck, which publishes
# none: its digest was recorded from the release asset). Ubuntu packages come
# from the signed noble archive. The final RUN asserts every version, so a drift
# fails the build. Tool list and versions: README.md next to this file.
#
# Runs as the job sidecar (uid 1001, no capabilities, read-only nothing assumed):
# the image only adds tools; the Pod sets the command (sidecar-agent.sh).
# amd64 only, like every runner node.

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

# Ubuntu 24.04 (noble), linux/amd64: python 3.12, ruby 3.2, php 8.3.
FROM registry.vallery.net/jvallery/estate-ci/mirror/docker.io/library/ubuntu@sha256:78d4744ead1f5b19b04d0df558ab182a5300903900755e515b2ca4d10eb2044d

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash ca-certificates curl file g++ git gnupg jq make openssh-client \
      php-cli python3 python3-pip python3-venv python3-yaml ruby-full \
      sqlite3 tar unzip xz-utils zip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/tools
# fetch <url> <sha256> <file>: download and verify, or fail the build.
RUN printf '%s\n' '#!/bin/sh' 'set -eu' \
      'curl -fsSL --retry 3 -o "$3" "$1"' \
      'echo "$2  $3" | sha256sum -c -' > /usr/local/bin/fetch-verified \
    && chmod 0755 /usr/local/bin/fetch-verified

# kubectl v1.35.10 (server minor 1.35; go1.26.10) and kustomize v5.8.3: the fleet
# toolbox and Argo CD tools pins, which cortex-ai-compute checks exactly.
ARG KUBECTL_VERSION=v1.35.10
ARG KUBECTL_SHA256=c49a28f1f4efc4d475ed05f47f2ce8e563bf7bef69ed77fe1b924ea6d83b6327
ARG KUSTOMIZE_VERSION=v5.8.3
ARG KUSTOMIZE_SHA256=cb9e31198d3f63b44848bd0afc4c8efc56e6cfef9c93a4a39a211940e6decd61
RUN fetch-verified "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" "${KUBECTL_SHA256}" kubectl \
    && install -m 0755 kubectl /usr/local/bin/kubectl \
    && fetch-verified "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2F${KUSTOMIZE_VERSION}/kustomize_${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" "${KUSTOMIZE_SHA256}" kustomize.tgz \
    && tar -xzf kustomize.tgz kustomize && install -m 0755 kustomize /usr/local/bin/kustomize \
    && rm -f /tmp/tools/*

# helm v3.16.3 (cortex-ai-compute renders need exactly v3; v4 fails), kubeconform v0.8.0.
ARG HELM_VERSION=v3.16.3
ARG HELM_SHA256=f5355c79190951eed23c5432a3b920e071f4c00a64f75e077de0dd4cb7b294ea
ARG KUBECONFORM_VERSION=v0.8.0
ARG KUBECONFORM_SHA256=9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
RUN fetch-verified "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" "${HELM_SHA256}" helm.tgz \
    && tar -xzf helm.tgz linux-amd64/helm && install -m 0755 linux-amd64/helm /usr/local/bin/helm \
    && fetch-verified "https://github.com/yannh/kubeconform/releases/download/${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" "${KUBECONFORM_SHA256}" kubeconform.tgz \
    && tar -xzf kubeconform.tgz kubeconform && install -m 0755 kubeconform /usr/local/bin/kubeconform \
    && rm -rf /tmp/tools/*

# yq v4.54.1, crane v0.22.1 (same pin as estate-ci image-build.sh / mirror.sh).
ARG YQ_VERSION=v4.54.1
ARG YQ_SHA256=8e34fc298390875de416e6a4afcb8cabeceb25d9aa8506c1a2f9353cf702ea5f
ARG CRANE_VERSION=v0.22.1
ARG CRANE_SHA256=0ab7a1d6932a213aed964ce97666c3077fe691c8606413674a8b3e0b9ec4cda0
RUN fetch-verified "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64" "${YQ_SHA256}" yq \
    && install -m 0755 yq /usr/local/bin/yq \
    && fetch-verified "https://github.com/google/go-containerregistry/releases/download/${CRANE_VERSION}/go-containerregistry_Linux_x86_64.tar.gz" "${CRANE_SHA256}" crane.tgz \
    && tar -xzf crane.tgz crane && install -m 0755 crane /usr/local/bin/crane \
    && rm -f /tmp/tools/*

# shellcheck v0.11.0, actionlint v1.7.12 (the versions estate-ci's self-test uses).
ARG SHELLCHECK_VERSION=v0.11.0
ARG SHELLCHECK_SHA256=8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198
ARG ACTIONLINT_VERSION=1.7.12
ARG ACTIONLINT_SHA256=8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8
RUN fetch-verified "https://github.com/koalaman/shellcheck/releases/download/${SHELLCHECK_VERSION}/shellcheck-${SHELLCHECK_VERSION}.linux.x86_64.tar.xz" "${SHELLCHECK_SHA256}" shellcheck.txz \
    && tar -xJf shellcheck.txz "shellcheck-${SHELLCHECK_VERSION}/shellcheck" \
    && install -m 0755 "shellcheck-${SHELLCHECK_VERSION}/shellcheck" /usr/local/bin/shellcheck \
    && fetch-verified "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz" "${ACTIONLINT_SHA256}" actionlint.tgz \
    && tar -xzf actionlint.tgz actionlint && install -m 0755 actionlint /usr/local/bin/actionlint \
    && rm -rf /tmp/tools/*

# uv 0.13.5 (the weather CI pin) and Node.js 24.22.1 with npm and corepack (pnpm).
ARG UV_VERSION=0.13.5
ARG UV_SHA256=f68d291c75783122dad58ab77def0e13f3233f358731db5d6d7e8715603b928f
ARG NODE_VERSION=v24.22.1
ARG NODE_SHA256=6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6
RUN fetch-verified "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz" "${UV_SHA256}" uv.tgz \
    && tar -xzf uv.tgz && install -m 0755 uv-x86_64-unknown-linux-gnu/uv uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/ \
    && fetch-verified "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz" "${NODE_SHA256}" node.txz \
    && mkdir -p /opt/node && tar -xJf node.txz -C /opt/node --strip-components=1 \
    && rm -rf /tmp/tools/*

COPY --from=gitleaks-build /out/gitleaks /usr/local/bin/gitleaks

# The job sidecar runs as uid/gid 1001 (runners.yaml job_uid). Give it a home.
RUN groupadd -g 1001 ci && useradd -u 1001 -g 1001 -m -d /home/ci -s /bin/bash ci
ENV HOME=/home/ci \
    PATH=/home/ci/.local/bin:/opt/node/bin:/usr/local/bin:/usr/bin:/bin \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 \
    PIP_BREAK_SYSTEM_PACKAGES=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PYTHON_PREFERENCE=system \
    NPM_CONFIG_UPDATE_NOTIFIER=false
WORKDIR /home/ci

# Assert every version and write the manifest the README documents.
COPY versions.sh /usr/local/share/estate-ci-toolbox/versions.sh
RUN sh /usr/local/share/estate-ci-toolbox/versions.sh --assert > /usr/local/share/estate-ci-toolbox/versions.txt \
    && cat /usr/local/share/estate-ci-toolbox/versions.txt
USER 1001
