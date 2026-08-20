# Project intent and zero-touch onboarding

AI Collab can register any owned Git root without adding files to it. This is
the Tier-0 default and is enough for a single repository or for repositories
already present as immediate children of the selected root.

Teams that need a stable multi-repository contract can commit
`.aicollab/project.yaml`. This is human-reviewable team intent. It does not pin
the installed AI Collab version, adapter implementation, machine paths, local
availability, or Scenario state.

```yaml
schema_version: 1
min_reader: 0.1.7
project_key: example
repos:
  - repo_key: example
    classification: required
    placement: project_root
    path: .
    remote: git@github.com:example/example.git
    base_branch: main
    provision_order: 0
    provision_after: []
    acceptance_layer: base
    smoke_policy: required
gates:
  profile: builtin.standard-v1
collaboration:
  profile: builtin.standard-v1
```

`min_reader` prevents an older App from guessing at future intent semantics.
Additive top-level fields are ignored with a visible warning when `min_reader`
allows the installed reader; missing or malformed required semantics fail with
a typed error.
`gates` and `collaboration` may select a shipped profile or a project-owned
registry using `{registry: relative/path}`. Custom registries are maintained
and reviewed by the project; builtin profiles require no additional files.

The owner-only command `ai-collab harness project bootstrap <git-root>` returns
a deterministic proposal, including YAML and its digest. It never writes the
selected checkout. A maintainer may review and commit that proposal; ordinary
employees only register the project and use the App.

At registration the Host builds a deterministic, machine-path-free resolved
render and stores it in owner-private Host state. That render adds immutable
builtin-profile digests and the current runtime/adapter bindings to team
intent. Every new Scenario receives the complete render snapshot; resume never
depends on a mutable registry history or re-rendering old intent.

Reconciliation runs at App startup, project selection, after Workspace
preparation, and on manual refresh. It classifies declared repositories as
present, missing, or drifted and local discoveries as undeclared. Availability
changes are refreshed without changing the semantic binding. A changed intent
or discovered Tier-0 topology becomes a pending update and is applied only
after the user selects **Apply project update**. Tool-owned compatibility pin
updates are applied automatically and affect only Scenarios created afterward;
existing Scenarios keep recovering from their own pinned snapshot. Historical
bindings cannot be selected for a newly created Scenario.

For repositories already present locally, arbitrary branches, detached HEAD,
and ahead/behind state are valid: planning pins the current local HEAD and WIP.
`base_branch` is used only when a declared repository must be cloned remotely.
Shallow and partial clones cannot provide the complete object evidence needed
for isolated provisioning; AI Collab reports a typed remediation instead of a
generic adapter failure.

`project_descriptor.yaml` and `repo_manifest.yaml` remain read-only legacy
inputs for every preview release that produced them. AI Collab does not
regenerate, rewrite, or require teams to delete those files during upgrade.
