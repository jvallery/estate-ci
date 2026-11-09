# estate-ci ci-tools (was agents-ci-tools) — the default image for in-cluster repository validation
# (`repo-validate`, #4859). Built here since estate-platform#48: registry.vallery.net/jvallery/estate-ci/ci-tools.
#
# WHY: repo-validate's `default_image` was the coding-pod runtime (`agents-agent-dev-runtime`), and
# bump-runtime-digest.sh moved it with every runtime pin. The research runtime (#4828) and the
# coding-fleet source cleanup (#4839) replace or delete that image, so owner CI needs its own small,
# independently pinned toolbox first. This image carries only what the registered `checks:` steps
# that omit an `image` actually run.
#
# TOOL INVENTORY — derived 2026-10-13 from every manifests/repo-ci-specs/generated/*.yaml step that
# falls back to the default image, plus the repository scripts those steps call:
#   sh/bash, coreutils, findutils,    every repo; also the run-check wrapper itself (base64, mkfifo,
#   diffutils, grep, sed, gzip, awk   tee, tail, tr); sentinel `diff`, wordpress `cmp`, gravity
#                                     `gunzip` — all Debian essential/required, already in the base
#   git                               every repo (git diff --check / git grep / git ls-files)
#   python3 3.11 + PyYAML,            homeassistant (static, automation-policy, haos-contract,
#   jsonschema, pytest                dashboard), agents (ci-submission-contract), frigate, backups,
#                                     sunshine_club gitops-contract; py_compile everywhere
#   tzdata                            homeassistant check_irrigation_reliability (zoneinfo)
#   ruby 3.1 (+ Psych)                `ruby -c` / YAML checks: homeassistant, sentinel, orbit, immich,
#                                     memoryos, wordpress, network-infra, proxmox, monitoring
#   node 24 + npm                     verdify-www, sunshine_club, memoryos, vallery-family,
#                                     homeassistant dashboard (`node --test`)
#   g++ + make                        verdify-platform firmware-logic (`g++ -std=c++17`); node-gyp
#   uv                                sentinel, verdify-platform, sunshine_club, orbit, monitoring,
#                                     network-infra, storage-infra, crm, AI_presentation
#   kubectl (`kubectl kustomize`)     sentinel, verdify-www, sunshine_club, network-infra, memoryos,
#                                     wordpress, ImagineProposals
#   kubeconform                       verdify-www
#   yq (mikefarah v4)                 VerdifyConsultancy/.github, wordpress (`yq eval`)
#   apt-get + dpkg-deb                verdify-campaign-ci's private PostgreSQL 16 fallback (base)
#   verdify-campaign-ci               verdify-platform campaign-qualification (baked helper, below)
#   jq, yamllint                      named by the #4859 spec; no current step needs a system copy
#                                     (network-infra installs its own yamllint through uv)
# Deliberately NOT carried: agent CLIs and harnesses, VS Code server, docker, gcloud, helm, sops,
# argocd, Playwright browsers, shellcheck, actionlint and pip. A check that needs anything else
# declares its own `image` (as the homeassistant and vallery.net checks already do).
#
# BASE: the official Node 24 slim image on Debian 12 (bookworm), digest-pinned (index digest; the
# linux/amd64 manifest is sha256:51b1100cc2a8…). Same Node major and Debian release as the coding
# runtime it replaces (node:24-bookworm), so system python3 stays 3.11 and ruby 3.1 — the
# interpreters every current default-image check passes on — and NODE_VERSION equals the native CI
# toolchain lock (runtime/native-ci-source/toolchain-lock.json). amd64-only, like every fleet node.
#
# PULL PATH: check pods pull this image with the `argo-ci-workflow` ServiceAccount's
# `zot-origin-cluster-pull` secret, exactly as they pull the runtime today; no zot ACL change.
FROM docker.io/library/node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20

# Debian-signed packages from the base image's bookworm archive. The slim base purges
# ca-certificates and curl after installing Node, so both come back here. Non-interactive so the
# tzdata debconf question can never wait on a builder without a TTY.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      g++ \
      git \
      jq \
      make \
      python3 \
      python3-jsonschema \
      python3-pytest \
      python3-yaml \
      ruby \
      tzdata \
      yamllint \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -c 'import jsonschema, pytest, yaml, zoneinfo; zoneinfo.ZoneInfo("America/Denver")' \
    && ruby -ryaml -e 'abort "psych" unless YAML.safe_load("a: 1") == {"a" => 1}' \
    && git --version && jq --version && yamllint --version && g++ --version | head -n 1

# kubectl (and its embedded `kubectl kustomize`): the repo's existing qualified pin — the same
# version and official dl.k8s.io linux/amd64 checksum as runtime/arc-runner-toolbox.
ARG KUBECTL_VERSION=v1.33.0
ARG KUBECTL_SHA256=9efe8d3facb23e1618cba36fb1c4e15ac9dc3ed5a2c2e18109e4a66b2bac12dc
RUN curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
      -o /usr/local/bin/kubectl \
    && echo "${KUBECTL_SHA256}  /usr/local/bin/kubectl" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/kubectl \
    && kubectl version --client

# kubeconform and yq: the versions the coding runtime base resolved as `latest` at its last build
# (2026-08-11), now pinned with the upstream release checksums (kubeconform CHECKSUMS, yq
# checksums-bsd).
ARG KUBECONFORM_VERSION=v0.8.0
ARG KUBECONFORM_SHA256=9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
RUN cd /tmp \
    && curl -fsSL -o kubeconform.tar.gz \
      "https://github.com/yannh/kubeconform/releases/download/${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" \
    && echo "${KUBECONFORM_SHA256}  kubeconform.tar.gz" | sha256sum -c - \
    && tar -xzf kubeconform.tar.gz kubeconform \
    && install -m 0755 kubeconform /usr/local/bin/kubeconform \
    && rm -f kubeconform.tar.gz kubeconform \
    && kubeconform -v
ARG YQ_VERSION=v4.53.3
ARG YQ_SHA256=fa52a4e758c63d38299163fbdd1edfb4c4963247918bf9c1c5d31d84789eded4
RUN curl -fsSL -o /usr/local/bin/yq \
      "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_amd64" \
    && echo "${YQ_SHA256}  /usr/local/bin/yq" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/yq \
    && yq --version

# uv: the exact wheel the native CI toolchain already qualifies (runtime/native-ci-source/
# toolchain-lock.json: PyPI URL + sha256). The wheel is py3-none, so its two static binaries are
# extracted directly and no pip is installed.
ARG UV_VERSION=0.12.13
ARG UV_WHEEL_URL=https://files.pythonhosted.org/packages/0c/31/f774e18ea9282f63fb3a4f83db8b818b860fb10efda1d295e4547e993bf8/uv-0.12.13-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
ARG UV_WHEEL_SHA256=5312891392eb5b72eaca9b880e85fe3e706a2b11342e59f7a5cf0304d14d5cc5
RUN curl -fsSL -o /tmp/uv.whl "${UV_WHEEL_URL}" \
    && echo "${UV_WHEEL_SHA256}  /tmp/uv.whl" | sha256sum -c - \
    && python3 -m zipfile -e /tmp/uv.whl /tmp/uv \
    && install -m 0755 "/tmp/uv/uv-${UV_VERSION}.data/scripts/uv" \
      "/tmp/uv/uv-${UV_VERSION}.data/scripts/uvx" /usr/local/bin/ \
    && rm -rf /tmp/uv /tmp/uv.whl \
    && uv --version

# verdify-platform's `campaign-qualification` step runs `python3 /usr/local/bin/verdify-campaign-ci`.
# estate-platform#48: copied byte for byte from jvallery/agents
# control-plane/agent-fleet-control/runtime/agent-dev/verdify_campaign_ci.py (f9d92454), so this
# image no longer builds from jvallery/agents. The mode is set explicitly for the non-root user.
COPY verdify_campaign_ci.py /usr/local/bin/verdify-campaign-ci
RUN chmod 0755 /usr/local/bin/verdify-campaign-ci

# repo-validate runs every check as UID:GID 1001:1001 with HOME=/workspace/home (#3200). Give that
# identity a passwd entry, as the coding runtime's `agent` user did, so getpwuid()-based tools (git
# ident, getpass) resolve it. No sudo. LANG keeps Ruby off US-ASCII for UTF-8 sources.
RUN groupadd --gid 1001 ci \
    && useradd --uid 1001 --gid 1001 --no-create-home --home-dir /workspace/home \
      --shell /bin/bash ci
ENV LANG=C.UTF-8
USER 1001:1001
# Prove every tool executes as the non-root check identity. The real HOME (/workspace/home) only
# exists at run time (repo-validate's emptyDir), so this build-time smoke uses /tmp.
RUN export HOME=/tmp \
    && node --version && npm --version && python3 --version && ruby --version && uv --version \
    && kubectl version --client && kubeconform -v && yq --version \
    && test -x /usr/local/bin/verdify-campaign-ci
ENTRYPOINT []
CMD ["bash"]
