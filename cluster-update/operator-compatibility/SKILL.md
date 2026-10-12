---
name: operator-compatibility
description: Determine if operators need upgrades for OpenShift version compatibility by querying Product Life Cycle API and comparing against OLM v0 packagemanifests or OLM v1 FBC catalogs. Use when planning cluster upgrades to identify which installed operators require updates, or when evaluating operator compatibility with a target OCP version. Automatically filters upgrade candidates to the most relevant versions.
license: Apache-2.0
compatibility: Requires Python 3 with packaging library, oc CLI for live cluster queries, and network access to query https://access.redhat.com/product-life-cycles/api/v2/products
---

# Operator Compatibility Checker

Determine whether operators need upgrades to be compatible with a target OpenShift version by cross-referencing Product Life Cycle (PLCC) data with OLM catalog information.

## CLI Tool

All compatibility checks go through `operator-compatibility/check_operator_compatibility.py` — a Python 3 script requiring only the `packaging` library. Run with `-h` for full usage:

```bash
python3 operator-compatibility/check_operator_compatibility.py -h
```

Install the required dependency:
```bash
pip3 install packaging
```

## Usage

```bash
python3 operator-compatibility/check_operator_compatibility.py <OCP_VERSION> \
  [--operator PACKAGE:VERSION] \
  [--olmv0 PACKAGE:VERSION] \
  [--olmv1 PACKAGE:VERSION] \
  [--mode v0|v1] \
  [--plcc-file PATH] \
  [--fbc-file PATH] \
  [--output json|text]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `OCP_VERSION` | Target OpenShift version (e.g., `4.17`, `4.22`) |
| `--operator PACKAGE:VERSION` | Operator to check (uses default mode) |
| `--olmv0 PACKAGE:VERSION` | Explicitly check as OLM v0 operator |
| `--olmv1 PACKAGE:VERSION` | Explicitly check as OLM v1 operator |
| `--mode v0\|v1` | Default OLM mode when using `--operator` (default: `v0`) |
| `--plcc-file PATH` | Use local PLCC JSON file instead of API |
| `--fbc-file PATH` | Use local FBC file instead of cluster query |
| `--output json\|text` | Output format (default: `json`) |

### Examples

#### Check single OLM v1 operator against OCP 4.22

```bash
python3 operator-compatibility/check_operator_compatibility.py 4.22 \
  --olmv1 cluster-logging:6.3
```

#### Check multiple operators

```bash
python3 operator-compatibility/check_operator_compatibility.py 4.17 \
  --olmv1 cluster-logging:5.8 \
  --olmv1 openshift-gitops-operator:1.9.0 \
  --olmv0 grafana-operator:4.10.0
```

#### Use local files for offline analysis

```bash
python3 operator-compatibility/check_operator_compatibility.py 4.22 \
  --olmv1 cluster-logging:6.3 \
  --plcc-file plcc.json \
  --fbc-file redhat-4.22.json
```

#### Text output for human reading

```bash
python3 operator-compatibility/check_operator_compatibility.py 4.22 \
  --olmv1 cluster-logging:6.3 \
  --output text
```

## How It Works

The script follows a three-step process:

1. **Query PLCC API**: Retrieve compatible operator versions for the target OCP version
2. **Check catalog compatibility**:
   - **OLM v0**: Query packagemanifest via `oc get packagemanifest`
   - **OLM v1**: Parse FBC catalog (from cluster via `oc get clustercatalog` or from local file)
3. **Filter candidates**: Return only the most relevant upgrade versions:
   - Smallest version larger than current
   - Largest version available
   - Smallest version of next minor release
   - Largest patch of current minor release

### Version Matching

PLCC API returns versions as `major.minor` (e.g., `6.3`), while catalog bundles use `major.minor.patch` (e.g., `6.3.0`, `6.3.1`). The script automatically matches bundle versions where the `major.minor` prefix matches any PLCC compatible version.

### Catalog Mapping

The script includes built-in mappings for common operators:

**Red Hat Operators** (`redhat-operators` / `openshift-redhat-operators`):
- cluster-logging, openshift-gitops-operator, odf-operator, loki-operator, etc.

**Community Operators** (`community-operators` / `openshift-community-operators`):
- grafana-operator

## Output Format

All commands output JSON by default. Use `--output text` for human-readable format.

### Success: Version compatible

```json
{
  "package": "cluster-logging",
  "catalog": "redhat-operators",
  "message": "OK"
}
```

### Upgrade needed

```json
{
  "package": "cluster-logging",
  "catalog": "redhat-operators",
  "candidates": [
    {
      "channel": "stable-6.5",
      "bundle": "cluster-logging.v6.5.0",
      "version": "6.5.0"
    },
    {
      "channel": "stable-6.6",
      "bundle": "cluster-logging.v6.6.1",
      "version": "6.6.1"
    }
  ]
}
```

### Non-fatal error (informational)

Package data may not be published yet or catalog not available:

```json
{
  "package": "example-operator",
  "catalog": "redhat-operators",
  "message": "no package information declared in PLC API for OCP 4.22"
}
```

### Fatal error

Package exists but has no compatible versions:

```json
{
  "package": "example-operator",
  "catalog": "redhat-operators",
  "error": "no compatible versions declared in PLC API for OCP 4.22"
}
```

## Output Fields

| Field | Description |
|-------|-------------|
| `package` | OLM package name |
| `catalog` | Catalog source name (`redhat-operators`, `community-operators`) |
| `message` | Informational message — version is OK or data unavailable (non-fatal) |
| `error` | Fatal error — package exists but incompatible with target OCP |
| `candidates` | Array of upgrade options when version needs updating |
| `candidates[].channel` | Channel name containing the bundle |
| `candidates[].bundle` | Full bundle/CSV name |
| `candidates[].version` | Extracted semantic version |

## When to Use

- **Pre-upgrade planning**: Identify which operators need updates before upgrading OCP
- **Compatibility validation**: Verify current operator versions work with target OCP
- **Upgrade path planning**: Discover available upgrade channels and versions
- **Batch operator checks**: Process multiple operators at once for comprehensive analysis
- **Offline analysis**: Use with local PLCC and FBC files for airgapped environments

## Important Notes

### Error vs Message Distinction

- **`message`**: Non-fatal, informational — data may not be published yet or catalog unavailable. Operator could still work, just no lifecycle data available.
- **`error`**: Fatal — package exists in PLCC/catalog but explicitly has no compatible versions for the target OCP. Upgrade required.

### OLM Version Detection

The script determines the appropriate catalog based on the operator mode:

- **OLM v0**: Uses `catalogsource` + `packagemanifest`
- **OLM v1**: Uses `clustercatalog` + FBC endpoint

Ensure you specify the correct mode or use `--olmv0`/`--olmv1` explicitly.

### Version Extraction

Bundle names follow the pattern `<package>.v<version>` or `<package>.<version>`. The script extracts versions using the regex pattern: `v?[0-9]+\.[0-9]+\.[0-9]+(-[^\+\n ]+)?(\+.+)?`

Examples:
- `cluster-logging.v6.5.0` → `6.5.0`
- `rhsso-operator.7.5.1-opr-009` → `7.5.1-opr-009`

### Performance Considerations

FBC catalogs can be 100MB+. The script:
- Streams files line-by-line instead of loading into memory
- Only collects channel objects for the requested package
- Deduplicates candidates by channel+bundle combination

For live cluster queries, FBC is fetched from the clustercatalog's `.status.urls.base` endpoint.

### Catalog Mapping

Operators not in the built-in mapping will return:
```json
{"package": "unknown-operator", "message": "Unknown package (not in catalog mapping)"}
```

To add operators, edit the `CATALOG_MAPPING` dictionary in the script.
