# ci-tools

The small validation toolbox the estate-ci runner sets reference (formerly
`registry.vallery.net/jvallery/agents-ci-tools`, built from jvallery/agents
`control-plane/agent-fleet-control/runtime/ci-tools/`). Moved here by
estate-platform#48 so no running pod uses an image built from jvallery/agents.

- Image: `registry.vallery.net/jvallery/estate-ci/ci-tools`, pushed by
  `.github/workflows/build.yml` (dispatch on main, principal `ci-estate-ci`,
  which already writes `jvallery/estate-ci/**`). PRs build it without push.
- The Dockerfile is the agents one with only the `verdify_campaign_ci.py`
  COPY path changed (that file is copied here byte for byte).
- Consumers re-pin by digest in a reviewed PR (runners.yaml `job_image` rows
  that name `agents-ci-tools`), then re-render.
