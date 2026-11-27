# Ownership

This repository owns a CI system and everything it declares:

- the reusable workflows and this repository's own workflows in `.github/workflows/`;
- the runner scripts in `deploy/foundation/bin/` (ConfigMap `estate-ci-scripts`);
- namespaces `ci-runners` and `arc-systems` as declared in `deploy/foundation/` and `deploy/arc-systems/`;
- the ARC controller and the runner sets generated from `runners.yaml` into `deploy/runners/apps/`;
- the job images in `images/`, the image mirror list `mirrors.txt` and the Renovate policy in `renovate/`.

[AGENTS.md](AGENTS.md) and [README.md](README.md) describe each one. The estate keeps the full item list in its ownership record, which is not public.
