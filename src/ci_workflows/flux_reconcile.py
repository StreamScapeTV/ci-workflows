"""Trusted Flux reconciliation wrapper around exact Flux-owned policy source."""
from __future__ import annotations

import hashlib, json, os, re, shutil, subprocess, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from .maintenance_contract import MaintenanceContract, MaintenanceError

_DNS = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PRODUCT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_SOURCE_KINDS = {"oci", "git", "helm", "bucket"}
_WORKLOAD_KINDS = {"deployment", "statefulset", "daemonset"}

@dataclass(frozen=True)
class ResourceRef:
    name: str; namespace: str; kind: str = ""
@dataclass(frozen=True)
class WorkloadRef:
    kind: str; name: str; namespace: str
@dataclass(frozen=True)
class FluxPlan:
    admitted_sha: str; target_id: str; product_id: str; operation: str; request_id: str
    policy_sha256: str; allowlist_sha256: str; executor_sha256: str
    flux_source: ResourceRef | None; kustomization: ResourceRef | None; helm_release: ResourceRef | None
    workloads: tuple[WorkloadRef, ...]

def _bounded_path(root: Path, relative: str) -> Path:
    base = root.resolve(); current = base
    for part in relative.split("/"):
        current /= part
        if current.is_symlink(): raise MaintenanceError("flux_source_path_rejected")
    resolved = current.resolve(strict=False)
    if resolved == base or base not in resolved.parents: raise MaintenanceError("flux_source_path_rejected")
    return resolved

def _regular(root: Path, relative: str) -> Path:
    path = _bounded_path(root, relative)
    if not path.is_file() or path.is_symlink(): raise MaintenanceError("flux_source_path_rejected")
    return path

def _sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def _git(source: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=source, check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=30)
    if done.returncode: raise MaintenanceError("flux_source_identity_invalid")
    return done.stdout.strip()
def _clean(source: Path) -> bool: return not _git(source, "status", "--porcelain", "--untracked-files=all")
def _dns(value: Any, field: str) -> str:
    if not isinstance(value, str) or _DNS.fullmatch(value) is None: raise MaintenanceError(f"flux_plan_{field}_invalid")
    return value

def _resource(raw: Any, field: str, source: bool = False) -> ResourceRef | None:
    if raw is None or raw == "": return None
    if not isinstance(raw, Mapping): raise MaintenanceError("flux_policy_plan_invalid")
    expected = {"name", "namespace", "kind"} if source else {"name", "namespace"}
    if set(raw) != expected: raise MaintenanceError("flux_policy_plan_invalid")
    kind = str(raw.get("kind", ""))
    if source and kind not in _SOURCE_KINDS: raise MaintenanceError("flux_policy_plan_invalid")
    return ResourceRef(_dns(raw.get("name"), field + "_name"), _dns(raw.get("namespace"), field + "_namespace"), kind)
def _workloads(raw: Any) -> tuple[WorkloadRef, ...]:
    if raw is None or raw == "": return ()
    if not isinstance(raw, list) or len(raw) > 20: raise MaintenanceError("flux_policy_plan_invalid")
    result=[]; seen=set()
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {"kind","name","namespace"} or item.get("kind") not in _WORKLOAD_KINDS: raise MaintenanceError("flux_policy_plan_invalid")
        value=WorkloadRef(str(item["kind"]), _dns(item.get("name"),"workload_name"), _dns(item.get("namespace"),"workload_namespace"))
        key=(value.kind,value.namespace,value.name)
        if key in seen: raise MaintenanceError("flux_policy_plan_invalid")
        seen.add(key); result.append(value)
    return tuple(result)
def _read_plan(path: Path, *, admitted_sha: str, target_id: str, product_id: str, operation: str, request_id: str, policy_sha256: str, allowlist_sha256: str, executor_sha256: str) -> FluxPlan:
    try: raw=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as error: raise MaintenanceError("flux_policy_plan_invalid") from error
    expected={"schema_version","target_id","product_id","operation","flux_source","kustomization","helm_release","workloads"}
    if not isinstance(raw,Mapping) or set(raw)!=expected or raw.get("schema_version")!=1 or raw.get("target_id")!=target_id or raw.get("product_id")!=product_id or raw.get("operation")!=operation: raise MaintenanceError("flux_policy_plan_invalid")
    source=_resource(raw.get("flux_source"),"flux_source",True); kust=_resource(raw.get("kustomization"),"kustomization"); helm=_resource(raw.get("helm_release"),"helm_release"); workloads=_workloads(raw.get("workloads"))
    if operation=="deploy" and not any((source,kust,helm)): raise MaintenanceError("flux_policy_plan_invalid")
    if operation=="restart" and not workloads: raise MaintenanceError("flux_policy_plan_invalid")
    return FluxPlan(admitted_sha,target_id,product_id,operation,request_id,policy_sha256,allowlist_sha256,executor_sha256,source,kust,helm,workloads)

def resolve_request(contract: MaintenanceContract, *, source_root: Path, source_repository: str, admitted_sha: str, target_id: str, product_id: str, operation: str, policy_path: str, allowlist_path: str, request_id: str, state_root: Path) -> FluxPlan:
    """Run only the exact Flux-owned policy adapter and validate its structured plan."""
    contract.validate_request_id(request_id); contract.validate_sha(admitted_sha); policy=contract.operation("flux_reconcile")
    if source_repository!=policy["repository"]: raise MaintenanceError("flux_repository_rejected")
    if operation not in policy["allowed_operations"]: raise MaintenanceError("flux_operation_rejected")
    if _DNS.fullmatch(target_id) is None or _PRODUCT.fullmatch(product_id) is None: raise MaintenanceError("flux_identifier_rejected")
    if policy_path!=policy["policy_path"] or allowlist_path!=policy["allowlist_path"]: raise MaintenanceError("flux_policy_path_rejected")
    if _git(source_root,"rev-parse","HEAD")!=admitted_sha or not _clean(source_root): raise MaintenanceError("flux_source_identity_invalid")
    policy_file=_regular(source_root,policy_path); allowlist=_regular(source_root,allowlist_path); executor=_regular(source_root,str(policy["executor_path"]))
    state=state_root.resolve(); state.mkdir(parents=True,exist_ok=True)
    if state.is_symlink(): raise MaintenanceError("flux_state_invalid")
    request_file=state/"request.json"; plan_file=state/"plan.json"
    request_file.write_text(json.dumps({"schema_version":1,"target_id":target_id,"product_id":product_id,"operation":operation},sort_keys=True,separators=(",",":"))+"\n",encoding="utf-8")
    done=subprocess.run([sys.executable,str(policy_file),"--central-interface",str(policy["policy_interface"]),"--central-request",str(request_file),"--allowlist",str(allowlist),"--output-plan",str(plan_file)],cwd=source_root,check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,env={"PATH":os.environ.get("PATH",""),"PYTHONDONTWRITEBYTECODE":"1"})
    if done.returncode: raise MaintenanceError("flux_policy_rejected")
    plan=_read_plan(plan_file,admitted_sha=admitted_sha,target_id=target_id,product_id=product_id,operation=operation,request_id=request_id,policy_sha256=_sha256(policy_file),allowlist_sha256=_sha256(allowlist),executor_sha256=_sha256(executor))
    if _git(source_root,"rev-parse","HEAD")!=admitted_sha or not _clean(source_root): raise MaintenanceError("flux_source_mutated_by_policy")
    return plan

def _args(plan: FluxPlan) -> list[str]:
    source=plan.flux_source or ResourceRef("", "", ""); kust=plan.kustomization or ResourceRef("",""); helm=plan.helm_release or ResourceRef("","")
    workloads=json.dumps([{"kind":x.kind,"name":x.name,"namespace":x.namespace} for x in plan.workloads],sort_keys=True,separators=(",",":"))
    return ["--operation",plan.operation,"--flux-source-kind",source.kind,"--flux-source-name",source.name,"--flux-source-namespace",source.namespace,"--kustomization-name",kust.name,"--kustomization-namespace",kust.namespace,"--helm-release-name",helm.name,"--helm-release-namespace",helm.namespace,"--workloads-json",workloads]
def _revalidate(contract: MaintenanceContract, plan: FluxPlan, source: Path) -> Path:
    policy=contract.operation("flux_reconcile")
    if _git(source,"rev-parse","HEAD")!=plan.admitted_sha or not _clean(source): raise MaintenanceError("flux_source_changed_before_apply")
    paths=[_regular(source,str(policy[x])) for x in ("policy_path","allowlist_path","executor_path")]
    if [_sha256(x) for x in paths] != [plan.policy_sha256,plan.allowlist_sha256,plan.executor_sha256]: raise MaintenanceError("flux_source_changed_before_apply")
    return paths[2]
def reconcile(contract: MaintenanceContract, plan: FluxPlan, *, source_root: Path, state_root: Path, flux_kubeconfig: str, flux_sops_age_key: str) -> None:
    executor=_revalidate(contract,plan,source_root)
    if not flux_kubeconfig.strip() or not flux_sops_age_key.strip(): raise MaintenanceError("flux_credentials_missing")
    if shutil.which("flux") is None or shutil.which("kubectl") is None: raise MaintenanceError("flux_tooling_missing")
    state=state_root.resolve(); state.mkdir(parents=True,exist_ok=True); kube=state/"kubeconfig"; age=state/"sops-age-key"
    kube.write_text(flux_kubeconfig,encoding="utf-8"); age.write_text(flux_sops_age_key,encoding="utf-8"); kube.chmod(0o600); age.chmod(0o600)
    env={"PATH":os.environ.get("PATH",""),"PYTHONDONTWRITEBYTECODE":"1","KUBECONFIG":str(kube),"SOPS_AGE_KEY_FILE":str(age)}
    try:
        done=subprocess.run([sys.executable,str(executor),*_args(plan)],cwd=source_root,check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3600,env=env)
        if done.returncode: raise MaintenanceError("flux_reconciliation_failed")
        verify_health(plan)
        if _git(source_root,"rev-parse","HEAD")!=plan.admitted_sha or not _clean(source_root): raise MaintenanceError("flux_source_mutated_during_apply")
    finally:
        kube.unlink(missing_ok=True); age.unlink(missing_ok=True)
    if kube.exists() or age.exists(): raise MaintenanceError("flux_credential_cleanup_failed")
def verify_health(plan: FluxPlan) -> None:
    if plan.operation=="deploy" and not any((plan.flux_source,plan.kustomization,plan.helm_release)): raise MaintenanceError("flux_health_contract_invalid")
    if plan.operation=="restart" and not plan.workloads: raise MaintenanceError("flux_health_contract_invalid")
def plan_summary(plan: FluxPlan, *, dry_run: bool) -> dict[str,str]:
    return {"result":"success","reconciliation_state":"dry-run" if dry_run else "authorized","request_id":plan.request_id}
