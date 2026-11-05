"""Explicit developer setup, inventory and UID-scoped cleanup of managed Kubernetes resources."""

from __future__ import annotations

import argparse
import asyncio
import json
import tomllib
from pathlib import Path
from typing import Any

from a13n_harness.providers.environment.kubernetes.configuration import (
    LABEL_PREFIX,
    ClusterProfile,
    KubernetesConnectionConfiguration,
    Resource,
)
from a13n_harness.providers.environment.kubernetes.runtime import KubernetesRuntime, open_runtime

from dev.service.checkout import Checkout, write_private

# ConfigMaps are borrowed; this provider never creates them.
KINDS = ("Pod", "Secret", "NetworkPolicy", "PersistentVolumeClaim")


async def inventory(runtime: KubernetesRuntime) -> list[dict[str, Any]]:
    """Return metadata only, including retained claims; never export Secret bodies."""
    owner = runtime.profile.ownership_id
    records = []
    for kind in KINDS:
        continuation = ""
        while True:
            page = await runtime.call(
                "GET",
                runtime.path(kind),
                query=[("labelSelector", f"{LABEL_PREFIX}owner={owner}"), ("limit", 100), ("continue", continuation)],
            )
            for item in page["items"]:
                metadata = item["metadata"]
                labels = metadata.get("labels", {})
                if labels.get(LABEL_PREFIX + "owner") != owner:
                    continue
                records.append(
                    {
                        "kind": kind,
                        "name": metadata["name"],
                        "uid": metadata["uid"],
                        "instance": labels.get(LABEL_PREFIX + "instance"),
                        "created_at": metadata.get("creationTimestamp"),
                        "deleting_at": metadata.get("deletionTimestamp"),
                        "finalizers": metadata.get("finalizers", []),
                    }
                )
            continuation = page.get("metadata", {}).get("continue", "")
            if not continuation:
                break
    return records


async def cleanup(
    runtime: KubernetesRuntime, resources: list[Resource], *, delete_storage: bool, confirmed: bool
) -> list[dict[str, str]]:
    """Validate the entire explicit selection before dispatch; replacements are protected by API UID preconditions."""
    selected = []
    for resource in resources:
        if resource.kind not in KINDS or resource.uid is None:
            raise ValueError("Cleanup requires an owned Kind/name/uid from inventory")
        if resource.kind == "PersistentVolumeClaim" and not delete_storage:
            raise ValueError("Deleting a PVC requires --delete-storage; its data may be retained")
        item = await runtime.get(resource.kind, resource.name)
        if item is None:
            continue
        metadata = item["metadata"]
        labels = metadata.get("labels", {})
        if (
            metadata["uid"] != resource.uid
            or labels.get(LABEL_PREFIX + "owner") != runtime.profile.ownership_id
            or not labels.get(LABEL_PREFIX + "instance")
        ):
            raise ValueError(f"Ownership or UID changed for {resource.kind}/{resource.name}; inspect again")
        selected.append(resource)
    result = []
    # Stop consumers first. PVC protection/finalizers remain Kubernetes-owned.
    for resource in sorted(selected, key=lambda resource: KINDS.index(resource.kind)):
        if confirmed:
            await runtime.delete(resource)
        result.append(
            {
                "kind": resource.kind,
                "name": resource.name,
                "uid": resource.uid or "",
                "result": "requested" if confirmed else "preview",
            }
        )
    return result


def configure(args: argparse.Namespace) -> None:
    checkout = Checkout.resolve()
    profile = ClusterProfile(
        authentication="kubeconfig",
        kubeconfig=args.kubeconfig.expanduser().resolve(),
        context=args.context,
        namespaces=(args.namespace,),
        ownership_id=f"dev-{checkout.id}",
        sandbox_service_account=args.service_account,
        allow_port_forward=True,
        allow_plaintext_envd=True,
    )
    write_private(checkout.state / "kubernetes.json", profile.model_dump_json(indent=2, exclude_none=True))
    print(f"Saved cluster profile 'local' with owner {profile.ownership_id}. Restart make dev to apply it.")
    print("Create a Kubernetes Provider with config " + json.dumps({"cluster": "local", "namespace": args.namespace}))


async def operate(args: argparse.Namespace) -> None:
    values = tomllib.loads(args.config.read_text())
    profile = ClusterProfile.model_validate(values["environments"]["kubernetes_clusters"][args.cluster])
    connection = KubernetesConnectionConfiguration(cluster=args.cluster, namespace=args.namespace)
    connection._cluster = profile
    runtime = await open_runtime(configuration=connection, credential=None)
    try:
        if args.action == "list":
            result = await inventory(runtime)
        else:
            resources = []
            for value in args.resource:
                kind, name, uid = value.split("/", 2)
                resources.append(Resource.model_validate({"kind": kind, "name": name, "uid": uid}))
            result = await cleanup(runtime, resources, delete_storage=args.delete_storage, confirmed=args.yes)
        print(
            json.dumps(
                {
                    "cluster": args.cluster,
                    "namespace": args.namespace,
                    "owner": profile.ownership_id,
                    "resources": result,
                },
                indent=2,
            )
        )
    finally:
        await runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    setup = commands.add_parser("configure", help="Save this checkout's explicit trusted cluster profile")
    setup.add_argument("--kubeconfig", type=Path, required=True)
    setup.add_argument("--context", required=True)
    setup.add_argument("--namespace", required=True)
    setup.add_argument("--service-account", default="default")
    for name in ("list", "cleanup"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("var/dev/service.toml"))
        command.add_argument("--cluster", default="local")
        command.add_argument("--namespace", required=True)
        if name == "cleanup":
            command.add_argument(
                "--resource", action="append", required=True, help="Kind/name/uid from list; repeat to select more"
            )
            command.add_argument(
                "--delete-storage",
                action="store_true",
                help="Allow deletion of explicitly selected PVCs and their data",
            )
            command.add_argument(
                "--yes", action="store_true", help="Delete the selected resources; otherwise preview only"
            )
    args = parser.parse_args()
    if args.action == "configure":
        configure(args)
    else:
        asyncio.run(operate(args))


if __name__ == "__main__":
    main()
