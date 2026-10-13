---
name: cluster-update-advisor
description: Assess OpenShift cluster update (upgrade) readiness and risk, and produce a concrete remediation plan when the upgrade is feasible. Use when evaluating whether a cluster is safe to update, when an update is available, or when the user asks about update risks, prerequisites, blockers, or best practices.
license: Apache-2.0
---

# Cluster Update Advisor

## Purpose

Assess cluster update readiness, produce a structured risk report with
actionable prerequisites, blockers, and recommendations, and — when the
upgrade is feasible — generate a concrete, ordered remediation plan with
executable `ProposedAction[]` entries.

The proposal request includes pre-collected cluster readiness data (JSON)
gathered by the Cluster Version Operator. Analyze this data, classify findings,
and produce a decision with evidence. Do not re-collect cluster data — it is
already in the request.

If the assessment determines the upgrade is not feasible or no viable
remediation path exists, do not produce a remediation plan — explain why in the
diagnosis instead.

## Inputs

The proposal request contains:
- Current and target version metadata
- Channel and update path classification — `Recommended` or `Conditional`
- **Cluster readiness JSON** — cluster health checks with context relevant to preparing for the update

The readiness JSON is embedded in the request between ` ```json ` markers under
the "Cluster Readiness Data" heading. Parse it to begin analysis.

**Readiness JSON structure:**

```json
{
  "current_version": "4.21.5",
  "target_version": "4.21.8",
  "checks": {
    "cluster_conditions":    { "_status": "ok", "summary": {...}, ... },
    "operator_health":       { "_status": "ok", "summary": {...}, ... },
    "api_deprecations":      { "_status": "ok", "summary": {...}, ... },
    "node_capacity":         { "_status": "ok", "summary": {...}, ... },
    "pdb_drain":             { "_status": "ok", "summary": {...}, ... },
    "etcd_health":           { "_status": "ok", "summary": {...}, ... },
    "network":               { "_status": "ok", "summary": {...}, ... },
    "crd_compat":            { "_status": "ok", "summary": {...}, ... },
    "olm_operator_lifecycle": { "_status": "ok", "summary": {...}, "operators": [...] }
  }
}
```

Each check contains `_status` (`ok` or `error`) and check-specific data
with a `summary` section for quick parsing.


## Risk Assessment Phase

### Parse readiness data

Extract the JSON from the proposal request.
Count checks with `_status` `ok` vs `error` for completeness.

### Verify data completeness

Any check with `_status` `error` represents a gap in visibility.
Note incomplete areas — they reduce confidence.

### Evaluate findings in detail

If the system prompt includes organization-specific policy (thresholds, scheduling preferences, risk tolerance), apply those constraints.
Otherwise use sensible defaults.
Walk through each check's summary and detail data:

- Compare numeric thresholds (node headroom, etcd backup age)
- Evaluate conditional update risks against cluster state
- Identify compounding risks (e.g., paused MCP + cert expiry)
- Estimate update duration (~10 min/node)

### Classify findings

Assign each finding a severity per the classification table.

| Check | Blocker if... | Warning if... |
|---|---|---|
| Cluster conditions | Upgradeable=False (non-z-stream) | Update already in progress |
| API deprecations | Workloads use APIs **removed** in target | Workloads use **deprecated** APIs |
| Operator health | Any operator has Upgradeable=False | Any operator is Degraded=True |
| MachineConfigPool | Any MCP paused or degraded | MCP updating or not all machines ready |
| Node capacity | Headroom < 20% | Headroom < 40% |
| PDB config | PDB blocks ALL replicas from draining | PDB has maxUnavailable: 0 |
| etcd health | Any member unhealthy | No recent backup (within 24h) |
| Network plugin | SDN in use and target requires OVN (4.17+) | Using deprecated SDN (< 4.17) |
| CRD compatibility | Stored version not served; operator maxOpenShiftVersion < target | Deprecated versions still served |
| OLM operator lifecycle | Installed operator incompatible with target OCP; operator product EOL | Operator has pending update; operator product in Maintenance Support |

For other checks, treat an issue as a blocker if would cause data loss, a performance regression, or a failed update.
Treat the issue as a warning if would cause temporary disruption or slow updates.

### Investigate with other skills

If additional information or context is needed to classify a finding, these skills may be useful:

- **`openshift-docs`** — Read official OpenShift update docs for version-specific
  procedures and breaking changes.

- **`prometheus`** — Query cluster metrics for trend analysis (etcd latency,
  CPU headroom, firing alerts).

- **`jira`** — Search Red Hat Jira for bugs and known issues affecting the target version.

- **`product-lifecycle`** — Query Red Hat Product Life Cycle API to check
  support status and OCP compatibility for installed operators. Use the operator's
  `package` name from OLM readiness data to look up entries via the `package`
  field (exact match). Flag operators whose product version is End of life or whose
  `openshift_compatibility` does not include the target OCP version.

### Classify overall recommendation

Aggregate finding classification, and make a decision on the overall assessment:

* escalate  — insufficient data for confident assessment.
* block     — findings must be resolved before update.
* warn — findings exist but manageable with prerequisites.
* recommend — all checks pass within acceptable thresholds.

| Blockers | Warnings | Decision |
|---|---|---|
| Unable to assess | any | `escalate` |
| 1+ | any | `block` |
| 0 | 1+ | `warn` |
| 0 | 0 | `recommend` |

### Produce a structured risk report

Produce the risk report with the decision and evidence.

The output schema is enforced by the OlsAgent CR's `outputSchema` field —
the operator handles structured output compliance via the LLM API.

**If the decision is `escalate` or `block` with no viable remediation, stop here.** Do not produce a remediation plan — explain why in the diagnosis.

---

## Remediation Plan Phase

Proceed only when the assessment indicates the upgrade is feasible (decision is `recommend` or `warn`, or `block` with actionable remediation).

### Identify required operator upgrades

Use the OLM operator data at `.checks.olm_operator_lifecycle`. The `operators` array contains one entry per installed OLM-managed operator with fields like `name`, `package`, `namespace`, `channel`, `available_channels`, `installed_version`, and `state`.

Key behaviors to note:
- `install_plan_approval` determines whether an InstallPlan requires manual approval (`Manual`) or proceeds automatically (`Automatic`)
- `installed_csv` and `install_plan_approval` indicate OLMv0 (Subscription-managed) operators

The `summary` section reports aggregate counts: `incompatible_with_target`, `manual_approval`, `pending_upgrades`, `total_operators`.

**Supported operator scope:**

`cluster-logging`, `openshift-gitops-operator`, `kubernetes-nmstate-operator`, `openshift-cert-manager-operator`, `local-storage-operator`, `redhat-oadp-operator`, `mcg-operator`, `ocs-operator`, `odf-csi-addons-operator`, `odf-operator`, `loki-operator`, `kubevirt-hyperconverged-operator`, `openshift-pipelines-operator-rh`, `metallb-operator`, `devworkspace-operator`, `web-terminal`, `mtv-operator`, `multicluster-engine`, `advanced-cluster-management`, `rhacs-operator`, `compliance-operator`, `nfd`, `quay-operator`, `cluster-observability-operator`, `ocs-client-operator`, `recipe`, `odf-prometheus-operator`, `rook-ceph-operator`, `cephcsi-operator`, `odf-dependencies`, `grafana-operator`, `odf-external-snapshotter-operator`

**If the cluster has any installed operator whose `package` is not in this list, do not produce a remediation plan.** Report in the diagnosis that the cluster contains operators outside the supported scope and recommend performing the upgrade manually without AI assistance. List the unsupported operators found.

**Critical operators** (require upgrade before the platform upgrade) are those in the supported list where:
- `summary.incompatible_with_target > 0` — at least one operator is incompatible with the target OCP version
- The operator's current channel does not support the target OCP version (use `available_channels` to identify a compatible channel)
- The operator is in an EOL state flagged by the `product-lifecycle` skill

**Do NOT upgrade operators** that are already compatible with the target OCP version or where the upgrade is optional.

**If a critical operator has no compatible channel in `available_channels`**, the upgrade should NOT proceed. Detail which operators are blocking and why in the Remediation Option.

### Build the plan

Produce a single `RemediationOption` with these sections:

#### Pre-checks

Essential validation before any mutations:

- Verify cluster is not currently upgrading (`Progressing=False`)
- Verify no critical operators are degraded
- Verify target version exists in available or conditional updates

#### OLM Operator Upgrades (if needed)

**IMPORTANT:** Every command in the plan must be concrete and executable — resolve all variable values from the data gathered during the assessment phase. Never emit raw placeholders.

For each critical operator requiring upgrade:

**OLMv0 (Subscription/InstallPlan):**

Resolve from the assessment data: `${SUBSCRIPTION_NAME}` and `${NAMESPACE}` from the operator's subscription resource; `${TARGET_CHANNEL}` from the target version's required channel; `${INSTALLPLAN_NAME}` from the subscription's `.status.installplan.name` after the channel patch.

```bash
# Update subscription channel (triggers new InstallPlan), only if channel is different
# Requires: patch on subscriptions.operators.coreos.com
oc patch subscriptions.operators.coreos.com ${SUBSCRIPTION_NAME} -n ${NAMESPACE} --type=merge -p '{"spec":{"channel":"${TARGET_CHANNEL}"}}'

# If InstallPlan approval is Manual, approve the InstallPlan
oc wait subscriptions.operators.coreos.com ${SUBSCRIPTION_NAME} -n ${NAMESPACE} --for=jsonpath='{.status.installplan.name}'
# Requires: patch on installplans.operators.coreos.com
oc patch installplans.operators.coreos.com ${INSTALLPLAN_NAME} -n ${NAMESPACE} --type=merge -p '{"spec":{"approved":true}}'

# Wait for operator upgrade to complete
oc wait installplans.operators.coreos.com ${INSTALLPLAN_NAME} -n ${NAMESPACE} --for='jsonpath={.status.phase}=Complete' --timeout=300s
```

**OLMv1 (ClusterExtension):**

Resolve from the assessment data: `${EXTENSION_NAME}` from the ClusterExtension resource; `${TARGET_CHANNEL}` from the target version's required channel.

```bash
# Update ClusterExtension channel or version
# Requires: patch on clusterextensions.olm.operatorframework.io
oc patch clusterextensions.olm.operatorframework.io ${EXTENSION_NAME} --type=merge -p '{"spec":{"source":{"catalog":{"channels":["${TARGET_CHANNEL}"]}}}}'

# Wait for upgrade to complete
oc wait clusterextensions.olm.operatorframework.io ${EXTENSION_NAME} --for=condition=Installed=True --timeout=300s
```

#### Platform Upgrade

Resolve from the assessment data: `${TARGET_VERSION}` from the chosen update target in the cluster's available updates.

Request a platform update with `oc adm upgrade --to="${TARGET_VERSION}"`, which requires the ability to `patch` the `version` instance of the `clusterversions.config.openshift.io` resource.

```bash
oc adm upgrade --to=${TARGET_VERSION}
```

#### Post-checks

Verify the upgrade has started:

```bash
# Check that ClusterVersion status reflects the new desiredUpdate
oc get -o jsonpath='{.status.desired.version}' clusterversions.config.openshift.io version

# Verify upgrade is progressing (initial check only)
oc get -o jsonpath='{.status.conditions[?(@.type=="Progressing")].status}' clusterversions.config.openshift.io version
```

### Structure as RemediationOption

The plan must conform to the `RemediationOption` schema expected by the lightspeed-agentic-operator.

Each `ProposedAction` has:

| Field | Description |
|---|---|
| `type` | One of: `pre-check`, `mutation`, `wait`, `post-check` |
| `command` | The exact `oc` or `kubectl` command to execute |
| `description` | Human-readable explanation of what this action does |

Mark the action with:

```yaml
risk: "Medium" or "High"  # based on conditional risks and findings
reversible: "Irreversible"
rollbackPlan:
  description: "Platform upgrade cannot be rolled back once started."
```

**Derive RBAC for every planned action.** For each `oc` command in the plan, use the fully qualified resource name to identify the Kubernetes PolicyRule it requires (resource, apiGroup, verbs, resourceNames where applicable). Include the full list of required RBAC permissions in the remediation option output.

## Action Type Classification

| Action | Type | Example |
|---|---|---|
| Check cluster conditions | `pre-check` | `oc get clusterversions.config.openshift.io version -o jsonpath=...` |
| Check operator health | `pre-check` | `oc get clusteroperators.config.openshift.io -o json` |
| Update Subscription channel | `mutation` | `oc patch subscriptions.operators.coreos.com ... --type=merge -p '{...}'` |
| Approve InstallPlan | `mutation` | `oc patch installplans.operators.coreos.com ... --type=merge -p '{...}'` |
| Update ClusterExtension | `mutation` | `oc patch clusterextensions.olm.operatorframework.io ... --type=merge -p '{...}'` |
| Wait for operator install | `wait` | `oc wait clusterextensions.olm.operatorframework.io ... --for=condition=Installed` |
| Trigger platform upgrade | `mutation` | `oc adm upgrade --to=...` |
| Verify upgrade started | `post-check` | `oc get clusterversions.config.openshift.io version -o jsonpath=...` |

## Failure Modes — What NOT to Do

1. **Never recommend updating without analyzing the readiness data.** The JSON
   in the request is the source of truth.

2. **Never dismiss conditional update risks.** If the update path is conditional,
   evaluate each risk against the cluster.

3. **Never skip the API deprecation check.** Workloads using removed APIs will
   break after the update.

4. **Never assume etcd is healthy.** Always check member health in the readiness data.

5. **Never fabricate Jira issue keys, KB article IDs, or CVE numbers.** Use the
   `redhat-support` skill to get real data.

6. **Never recommend skipping an update version** unless the readiness data shows
   that path exists.

7. **Never recommend force-updating.** If the standard path is blocked, report it.

8. **Never upgrade all operators blindly.** Only upgrade operators that are incompatible with the target OCP version or whose `maxOpenShiftVersion` blocks the upgrade.

9. **Never produce generic or placeholder commands.** Every command must be specific, with actual operator names, namespaces, channels, and versions filled in.

10. **Never skip the pre-check phase.** Verify cluster readiness before any mutations.

11. **Never assume OLM API type.** The readiness data operator entries contain fields that indicate their management type — `installed_csv` and `install_plan_approval` indicate OLMv0 (Subscription-managed). Use the correct command templates.

12. **Never dry-run with --dry-run=client.** Always use `--dry-run=server` to validate against the actual cluster API.

13. **Detail reasons for failure.** If no valid remediation plans can be generated, detail the root cause.

## Example Output Structure

```yaml
remediationOptions:
  - name: "OpenShift 4.21.5 → 4.21.8 upgrade with operator alignment"
    description: "Upgrade cluster-logging operator to target-compatible channel, then upgrade platform to 4.21.8"
    plan:
      risk: "Medium"
      reversible: "Irreversible"
      rollbackPlan:
        description: "Platform upgrade cannot be rolled back once started."
      actions:
        # Pre-checks
        - type: pre-check
          command: "oc get -o jsonpath='{.status.conditions[?(@.type==\"Progressing\")].status}' clusterversions.config.openshift.io version"
          description: "Verify cluster is not currently progressing (expected: False)"

        - type: pre-check
          command: "oc get clusteroperators.config.openshift.io -o json | jq -r '.items[] | select(.status.conditions[] | select(.type==\"Degraded\" and .status==\"True\")) | .metadata.name'"
          description: "Check for degraded cluster operators (expected: no output)"

        # OLM operator upgrades
        - type: mutation
          command: "oc patch subscriptions.operators.coreos.com cluster-logging -n openshift-logging --type=merge -p '{\"spec\":{\"channel\":\"stable-5.9\"}}'"
          description: "Update cluster-logging operator to stable-5.9 channel (compatible with OCP 4.21.8)"

        - type: wait
          command: "oc wait subscriptions.operators.coreos.com cluster-logging -n openshift-logging --for=jsonpath='{.status.state}'=AtLatestKnown --timeout=300s"
          description: "Wait for cluster-logging operator upgrade to complete"

        # Platform upgrade
        - type: mutation
          command: "oc adm upgrade --to=4.21.8"
          description: "Trigger platform upgrade to 4.21.8 (patches clusterversions.config.openshift.io/version)"

        # Post-checks
        - type: post-check
          command: "oc get -o jsonpath='{.status.desired.version}' clusterversions.config.openshift.io version"
          description: "Verify desiredUpdate has been set (expected: 4.21.8)"

        - type: post-check
          command: "oc get -o jsonpath='{.status.conditions[?(@.type==\"Progressing\")].status}' clusterversions.config.openshift.io version"
          description: "Verify upgrade has started progressing (expected: True)"
```

## Integration with Other Skills

This skill is typically used in conjunction with:

- **`product-lifecycle`** — Used to verify operator support status and OCP compatibility before planning upgrades

