#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
import re
from typing import List, Dict, Any, Optional
from packaging import version as pkg_version
import urllib.request
import urllib.error

# Package-catalog mapping
CATALOG_MAPPING = {
    "community-operators": {
        "olmv0": {
            "name": "community-operators",
            "namespace": "openshift-marketplace"
        },
        "olmv1": {
            "name": "openshift-community-operators"
        },
        "packages": ["grafana-operator"]
    },
    "redhat-operators": {
        "olmv0": {
            "name": "redhat-operators",
            "namespace": "openshift-marketplace"
        },
        "olmv1": {
            "name": "openshift-redhat-operators"
        },
        "packages": [
            "cluster-logging", "openshift-gitops-operator", "kubernetes-nmstate-operator",
            "local-storage-operator", "mcg-operator", "ocs-operator", "odf-csi-addons-operator",
            "odf-operator", "loki-operator", "openshift-pipelines-operator-rh", "metallb-operator",
            "web-terminal", "mtv-operator", "multicluster-engine", "advanced-cluster-management",
            "rhacs-operator", "compliance-operator", "nfd", "quay-operator",
            "cluster-observability-operator", "ocs-client-operator", "recipe",
            "odf-prometheus-operator", "rook-ceph-operator", "cephcsi-operator",
            "odf-dependencies", "openshift-cert-manager-operator", "redhat-oadp-operator",
            "kubevirt-hyperconverged", "devworkspace-operator", "odf-external-snapshotter-operator"
        ]
    }
}

PLCC_API_URL = "https://access.redhat.com/product-life-cycles/api/v2/products"


def get_catalog_for_package(package_name: str) -> Optional[Dict[str, Any]]:
    """Find the catalog configuration for a given package."""
    for catalog_key, catalog_info in CATALOG_MAPPING.items():
        if package_name in catalog_info["packages"]:
            return {
                "key": catalog_key,
                **catalog_info
            }
    return None


def extract_version_from_bundle_name(bundle_name: str) -> Optional[str]:
    r"""
    Extract version from bundle/CSV name.
    Bundle names don't contain '.' until version info, so split at first '.' and parse from there.
    Expected formats: <package>.v<version> or <package>.<version>
    Version regex: v?[0-9]+\.[0-9]+\.[0-9]+(-[^\+\n ]+)?(\+.+)?
    """
    # Split at first dot to get the version part
    parts = bundle_name.split('.', 1)
    if len(parts) < 2:
        return None

    version_part = parts[1]

    # Match semver pattern with optional 'v' prefix
    # v?[0-9]+\.[0-9]+\.[0-9]+(-[^\+\n ]+)?(\+.+)?
    match = re.match(r'v?([0-9]+\.[0-9]+\.[0-9]+(?:-[^\+\n ]+)?(?:\+.+)?)', version_part)
    if match:
        return match.group(1)

    return None


def parse_openshift_version(ocp_version: str) -> str:
    """
    Parse OpenShift version to <major>.<minor> format.
    """
    parts = ocp_version.split('.')
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return ocp_version


def get_compatible_versions_from_plcc(package_name: str, ocp_version: str, plcc_file: Optional[str] = None) -> Dict[str, Any]:
    """
    Query PLCC API (or local file) to find compatible versions for a package and OpenShift version.

    Returns:
        Dict with 'status' (ok/not_found), 'compatible_versions' (list), 'message' (optional)
    """
    ocp_version_short = parse_openshift_version(ocp_version)

    try:
        if plcc_file:
            # Use local file
            with open(plcc_file, 'r') as f:
                plcc_data = json.load(f)
        else:
            # Query API
            with urllib.request.urlopen(PLCC_API_URL, timeout=30) as response:
                plcc_data = json.load(response)

        # Find package in PLCC data
        package_entry = None
        for entry in plcc_data.get('data', []):
            if entry.get('package') == package_name:
                package_entry = entry
                break

        if not package_entry:
            return {
                "status": "not_found",
                "message": f"no package information declared in PLC API for OCP {ocp_version}"
            }

        # Extract compatible versions
        compatible_versions = []
        for ver in package_entry.get('versions', []):
            ver_name = ver.get('name')
            ocp_compat = ver.get('openshift_compatibility', '')

            # Check if ocp_version_short is in comma-separated list
            compat_list = [v.strip() for v in ocp_compat.split(',')]
            if ocp_version_short in compat_list:
                compatible_versions.append(ver_name)

        if not compatible_versions:
            return {
                "status": "error",
                "message": f"no compatible versions declared in PLC API for OCP {ocp_version}"
            }

        return {
            "status": "ok",
            "compatible_versions": compatible_versions
        }

    except urllib.error.URLError as e:
        return {
            "status": "not_found",
            "message": f"Error accessing PLCC API: {str(e)}"
        }
    except Exception as e:
        return {
            "status": "not_found",
            "message": f"Error querying PLCC: {str(e)}"
        }


def deduplicate_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deduplicate candidates by channel+bundle combination.
    Keep only unique channel+bundle pairs.
    """
    seen = set()
    unique_candidates = []

    for candidate in candidates:
        key = (candidate['channel'], candidate['bundle'])
        if key not in seen:
            seen.add(key)
            unique_candidates.append(candidate)

    return unique_candidates


def check_olmv0_packagemanifest(package_name: str, catalog_name: str, catalog_namespace: str, compatible_versions: List[str]) -> Dict[str, Any]:
    """
    Query OLM v0 packagemanifest to find upgrade candidates.

    Returns:
        Dict with 'status', 'candidates' (optional), 'message' (optional)
    """
    try:
        # Query packagemanifest
        cmd = [
            "oc", "get", "packagemanifest", package_name,
            "-o", "json",
            "-n", catalog_namespace
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return {
                "status": "not_found",
                "message": "No packagemanifest found"
            }

        pm_data = json.loads(result.stdout)

        # Extract channels and entries
        # PLCC versions are major.minor (e.g., "6.3"), but entry versions may be major.minor.patch (e.g., "6.3.0")
        # We need to match versions where major.minor matches any PLCC version
        candidates = []
        channels = pm_data.get('status', {}).get('channels', [])

        for channel in channels:
            channel_name = channel.get('name')
            entries = channel.get('entries', [])

            for entry in entries:
                entry_name = entry.get('name')
                entry_version = entry.get('version')

                if not entry_version:
                    continue

                # Check if this version is compatible
                # Match exact version or major.minor prefix
                is_compatible = False
                if entry_version in compatible_versions:
                    is_compatible = True
                else:
                    # Try matching major.minor
                    entry_parts = entry_version.split('.')
                    if len(entry_parts) >= 2:
                        entry_major_minor = f"{entry_parts[0]}.{entry_parts[1]}"
                        if entry_major_minor in compatible_versions:
                            is_compatible = True

                if is_compatible:
                    candidates.append({
                        "channel": channel_name,
                        "bundle": entry_name,
                        "version": entry_version
                    })

        # Deduplicate by channel+bundle
        candidates = deduplicate_candidates(candidates)

        if not candidates:
            return {
                "status": "error",
                "message": f"no compatible versions with OCP in packagemanifest"
            }

        return {
            "status": "ok",
            "candidates": candidates
        }

    except subprocess.TimeoutExpired:
        return {
            "status": "not_found",
            "message": "Timeout querying packagemanifest"
        }
    except Exception as e:
        return {
            "status": "not_found",
            "message": f"Error querying packagemanifest: {str(e)}"
        }


def check_olmv1_fbc(package_name: str, catalog_name: str, compatible_versions: List[str], fbc_file: Optional[str] = None) -> Dict[str, Any]:
    """
    Query OLM v1 FBC (File-Based Catalog) to find upgrade candidates.

    Returns:
        Dict with 'status', 'candidates' (optional), 'message' (optional)
    """
    try:
        if fbc_file:
            # Use local FBC file
            # Parse JSON objects in streaming fashion to avoid loading 101MB file into memory
            channel_objs = []

            with open(fbc_file, 'r') as f:
                current_obj_lines = []
                brace_count = 0

                for line in f:
                    # Track opening and closing braces
                    brace_count += line.count('{') - line.count('}')
                    current_obj_lines.append(line)

                    # When brace_count returns to 0, we have a complete object
                    if brace_count == 0 and current_obj_lines:
                        try:
                            obj = json.loads(''.join(current_obj_lines))

                            # Only keep channel objects for our package
                            if obj.get('schema') == 'olm.channel' and obj.get('package') == package_name:
                                channel_objs.append(obj)

                        except json.JSONDecodeError:
                            pass  # Skip malformed objects

                        current_obj_lines = []

            fbc_objects = channel_objs
        else:
            # Query clustercatalog to get base URL
            cmd = ["oc", "get", "clustercatalog", catalog_name, "-o", "json"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                return {
                    "status": "not_found",
                    "message": "No clustercatalog found"
                }

            cc_data = json.loads(result.stdout)
            base_url = cc_data.get('status', {}).get('urls', {}).get('base')

            if not base_url:
                return {
                    "status": "not_found",
                    "message": "No base URL in clustercatalog"
                }

            # Fetch FBC from URL
            fbc_url = f"{base_url}/api/v1/all"
            with urllib.request.urlopen(fbc_url, timeout=60) as response:
                content = response.read().decode('utf-8')
                # Only collect channel objects for our package
                channel_objs = []
                decoder = json.JSONDecoder()
                idx = 0
                while idx < len(content):
                    content_from_idx = content[idx:].lstrip()
                    if not content_from_idx:
                        break
                    try:
                        obj, end_idx = decoder.raw_decode(content_from_idx)

                        # Only keep channel objects for our package
                        if obj.get('schema') == 'olm.channel' and obj.get('package') == package_name:
                            channel_objs.append(obj)

                        idx += len(content[idx:]) - len(content_from_idx) + end_idx
                    except json.JSONDecodeError:
                        break

                fbc_objects = channel_objs

        # Process channel objects to find compatible candidates
        # PLCC versions are major.minor (e.g., "6.3"), but bundle versions are major.minor.patch (e.g., "6.3.0")
        # We need to match bundle versions where major.minor matches any PLCC version
        candidates = []
        for channel_obj in fbc_objects:
            channel_name = channel_obj.get('name')
            entries = channel_obj.get('entries', [])

            for entry in entries:
                entry_name = entry.get('name')
                entry_version = extract_version_from_bundle_name(entry_name)

                if not entry_version:
                    continue

                # Check if this version is compatible
                # Match exact version or major.minor prefix
                is_compatible = False
                if entry_version in compatible_versions:
                    is_compatible = True
                else:
                    # Try matching major.minor
                    entry_parts = entry_version.split('.')
                    if len(entry_parts) >= 2:
                        entry_major_minor = f"{entry_parts[0]}.{entry_parts[1]}"
                        if entry_major_minor in compatible_versions:
                            is_compatible = True

                if is_compatible:
                    candidates.append({
                        "channel": channel_name,
                        "bundle": entry_name,
                        "version": entry_version
                    })

        # Deduplicate by channel+bundle
        candidates = deduplicate_candidates(candidates)

        if not candidates:
            # Check if we found any channels for the package
            if not fbc_objects:
                return {
                    "status": "not_found",
                    "message": "No data for package in clustercatalog"
                }
            else:
                return {
                    "status": "error",
                    "message": "no compatible versions with OCP in FBC"
                }

        return {
            "status": "ok",
            "candidates": candidates
        }

    except urllib.error.URLError as e:
        return {
            "status": "not_found",
            "message": f"Error fetching FBC: {str(e)}"
        }
    except Exception as e:
        return {
            "status": "not_found",
            "message": f"Error querying FBC: {str(e)}"
        }


def filter_candidates(candidates: List[Dict[str, Any]], current_version: str) -> List[Dict[str, Any]]:
    """
    Filter candidates to most relevant upgrade versions:
    - Only versions larger than current version
    - Largest semver version in list
    - Smallest semver version larger than current
    - Smallest entry of next minor version
    - Largest patch of current major.minor
    """
    if not current_version:
        return candidates

    try:
        current_ver = pkg_version.parse(current_version)
    except Exception:
        # If current version cannot be parsed, return all candidates
        return candidates

    # Filter to only versions > current
    larger_candidates = []
    for candidate in candidates:
        try:
            cand_ver = pkg_version.parse(candidate['version'])
            if cand_ver > current_ver:
                larger_candidates.append({
                    **candidate,
                    'parsed_version': cand_ver
                })
        except Exception:
            # Skip unparseable versions
            continue

    if not larger_candidates:
        return []

    # Sort by version
    larger_candidates.sort(key=lambda x: x['parsed_version'])

    # Identify key versions
    key_versions = set()

    # 1. Largest version overall
    largest = larger_candidates[-1]
    key_versions.add(largest['version'])

    # 2. Smallest version larger than current
    smallest_larger = larger_candidates[0]
    key_versions.add(smallest_larger['version'])

    # 3. For semver with major.minor, find next minor version's smallest
    if hasattr(current_ver, 'major') and hasattr(current_ver, 'minor'):
        next_minor_versions = [
            c for c in larger_candidates
            if hasattr(c['parsed_version'], 'major') and
               hasattr(c['parsed_version'], 'minor') and
               c['parsed_version'].major == current_ver.major and
               c['parsed_version'].minor == current_ver.minor + 1
        ]
        if next_minor_versions:
            key_versions.add(next_minor_versions[0]['version'])

    # 4. Largest patch of current major.minor
    if hasattr(current_ver, 'major') and hasattr(current_ver, 'minor'):
        same_minor_versions = [
            c for c in larger_candidates
            if hasattr(c['parsed_version'], 'major') and
               hasattr(c['parsed_version'], 'minor') and
               c['parsed_version'].major == current_ver.major and
               c['parsed_version'].minor == current_ver.minor
        ]
        if same_minor_versions:
            key_versions.add(same_minor_versions[-1]['version'])

    # Filter candidates to only key versions, preserving all channels
    filtered = [c for c in candidates if c['version'] in key_versions]

    # Deduplicate again after filtering
    filtered = deduplicate_candidates(filtered)

    return filtered


def check_operator_compatibility(
    package_name: str,
    ocp_version: str,
    current_version: Optional[str] = None,
    olm_mode: str = "v0",
    plcc_file: Optional[str] = None,
    fbc_file: Optional[str] = None
) -> Dict[str, Any]:
    """
    Main function to check operator compatibility and determine upgrade path.
    """
    result = {
        "package": package_name
    }

    # Get catalog for package
    catalog_info = get_catalog_for_package(package_name)
    if not catalog_info:
        result["message"] = "Unknown package (not in catalog mapping)"
        return result

    catalog_display = catalog_info['key']
    result["catalog"] = catalog_display

    # Step 1: Get compatible versions from PLCC
    plcc_result = get_compatible_versions_from_plcc(package_name, ocp_version, plcc_file)

    if plcc_result['status'] == 'not_found':
        # Non-fatal: package data might not be published yet
        result["message"] = plcc_result['message']
        return result
    elif plcc_result['status'] == 'error':
        # Fatal: package exists but no compatible versions
        result["error"] = plcc_result['message']
        return result

    compatible_versions = plcc_result['compatible_versions']

    # Step 2: Check if current version is already compatible
    if current_version and current_version in compatible_versions:
        result["message"] = "OK"
        return result

    # Step 3: Find upgrade candidates
    if olm_mode == "v0":
        catalog_name = catalog_info['olmv0']['name']
        catalog_namespace = catalog_info['olmv0']['namespace']
        upgrade_result = check_olmv0_packagemanifest(
            package_name, catalog_name, catalog_namespace, compatible_versions
        )
    else:  # v1
        catalog_name = catalog_info['olmv1']['name']
        upgrade_result = check_olmv1_fbc(
            package_name, catalog_name, compatible_versions, fbc_file
        )

    if upgrade_result['status'] == 'not_found':
        # Non-fatal: catalog data might not be available
        result["message"] = upgrade_result['message']
        return result
    elif upgrade_result['status'] == 'error':
        # Fatal: catalog exists but no compatible versions
        result["error"] = upgrade_result['message']
        return result

    # Step 4: Filter candidates
    candidates = upgrade_result['candidates']
    if current_version:
        candidates = filter_candidates(candidates, current_version)

    # Remove parsed_version from output
    for c in candidates:
        c.pop('parsed_version', None)

    if candidates:
        result["candidates"] = candidates
    else:
        result["error"] = f"no compatible versions with OCP {ocp_version}"

    return result


def main():
    parser = argparse.ArgumentParser(
        description='Check operator compatibility with OpenShift version and determine upgrade path'
    )
    parser.add_argument(
        'ocp_version',
        help='OpenShift version (e.g., 4.16, 4.17)'
    )
    parser.add_argument(
        '--operator',
        action='append',
        dest='operators',
        metavar='PACKAGE:VERSION',
        help='Operator in format package:version (can be specified multiple times)'
    )
    parser.add_argument(
        '--olmv0',
        action='append',
        dest='olmv0_operators',
        metavar='PACKAGE:VERSION',
        help='OLM v0 operator in format package:version (can be specified multiple times)'
    )
    parser.add_argument(
        '--olmv1',
        action='append',
        dest='olmv1_operators',
        metavar='PACKAGE:VERSION',
        help='OLM v1 operator in format package:version (can be specified multiple times)'
    )
    parser.add_argument(
        '--mode',
        choices=['v0', 'v1'],
        default='v0',
        help='Default OLM mode (v0 or v1) when using --operator flag'
    )
    parser.add_argument(
        '--plcc-file',
        help='Path to local PLCC JSON file (instead of querying API)'
    )
    parser.add_argument(
        '--fbc-file',
        help='Path to local FBC file for OLM v1 (instead of querying cluster)'
    )
    parser.add_argument(
        '--output',
        choices=['json', 'text'],
        default='json',
        help='Output format'
    )

    args = parser.parse_args()

    # Collect all operators
    operators = []

    if args.operators:
        for op in args.operators:
            parts = op.split(':', 1)
            if len(parts) != 2:
                print(f"Error: Invalid operator format '{op}', expected PACKAGE:VERSION", file=sys.stderr)
                sys.exit(1)
            operators.append({
                'package': parts[0],
                'version': parts[1],
                'mode': args.mode
            })

    if args.olmv0_operators:
        for op in args.olmv0_operators:
            parts = op.split(':', 1)
            if len(parts) != 2:
                print(f"Error: Invalid operator format '{op}', expected PACKAGE:VERSION", file=sys.stderr)
                sys.exit(1)
            operators.append({
                'package': parts[0],
                'version': parts[1],
                'mode': 'v0'
            })

    if args.olmv1_operators:
        for op in args.olmv1_operators:
            parts = op.split(':', 1)
            if len(parts) != 2:
                print(f"Error: Invalid operator format '{op}', expected PACKAGE:VERSION", file=sys.stderr)
                sys.exit(1)
            operators.append({
                'package': parts[0],
                'version': parts[1],
                'mode': 'v1'
            })

    if not operators:
        print("Error: No operators specified. Use --operator, --olmv0, or --olmv1 flags", file=sys.stderr)
        parser.print_help()
        sys.exit(1)

    # Check each operator
    results = []
    for op in operators:
        result = check_operator_compatibility(
            package_name=op['package'],
            ocp_version=args.ocp_version,
            current_version=op['version'],
            olm_mode=op['mode'],
            plcc_file=args.plcc_file,
            fbc_file=args.fbc_file
        )
        results.append(result)

    # Output results
    if args.output == 'json':
        print(json.dumps(results, indent=2))
    else:
        for result in results:
            print(f"Package: {result['package']}")
            print(f"  Catalog: {result.get('catalog', 'N/A')}")
            if 'message' in result:
                print(f"  Status: {result['message']}")
            elif 'error' in result:
                print(f"  Error: {result['error']}")
            elif 'candidates' in result:
                print(f"  Upgrade needed:")
                for candidate in result['candidates']:
                    print(f"    - Channel: {candidate['channel']}, Bundle: {candidate['bundle']}, Version: {candidate['version']}")
            print()


if __name__ == '__main__':
    main()
