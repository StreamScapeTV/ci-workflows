#!/usr/bin/env python3
"""CLI adapter for trusted Flux reconciliation."""
from __future__ import annotations
import argparse, json, os, shutil, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/"src"))
from ci_workflows.flux_reconcile import plan_summary, reconcile, resolve_request
from ci_workflows.maintenance_contract import MaintenanceError, load_contract

def _bool(value:str)->bool:
    low=value.casefold()
    if low=="true": return True
    if low=="false": return False
    raise argparse.ArgumentTypeError("expected true or false")
def _write(values:dict[str,str])->None:
    path=os.environ.get("GITHUB_OUTPUT","")
    if path:
        with Path(path).open("a",encoding="utf-8") as out:
            for name,value in values.items():
                if "\n" in value or "\r" in value: raise MaintenanceError("output_invalid")
                out.write(f"{name}={value}\n")
    print(json.dumps(values,sort_keys=True))
def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(); p.add_argument("--source-root",type=Path,required=True); p.add_argument("--source-repository",required=True); p.add_argument("--admitted-sha",required=True); p.add_argument("--target-id",required=True); p.add_argument("--product-id",required=True); p.add_argument("--operation",required=True); p.add_argument("--policy-path",required=True); p.add_argument("--allowlist-path",required=True); p.add_argument("--request-id",required=True); p.add_argument("--dry-run",type=_bool,required=True); args=p.parse_args(argv)
    state=Path(os.environ.get("RUNNER_TEMP",str(ROOT/".maintenance-state")))/f"flux-reconcile-{os.environ.get('GITHUB_RUN_ID','local')}-{args.request_id}"; shutil.rmtree(state,ignore_errors=True); contract=load_contract(ROOT)
    try:
        plan=resolve_request(contract,source_root=args.source_root,source_repository=args.source_repository,admitted_sha=args.admitted_sha,target_id=args.target_id,product_id=args.product_id,operation=args.operation,policy_path=args.policy_path,allowlist_path=args.allowlist_path,request_id=args.request_id,state_root=state)
        if not args.dry_run: reconcile(contract,plan,source_root=args.source_root,state_root=state,flux_kubeconfig=os.environ.get("FLUX_KUBECONFIG",""),flux_sops_age_key=os.environ.get("FLUX_SOPS_AGE_KEY",""))
        values=plan_summary(plan,dry_run=args.dry_run); values["reconciliation_state"]="dry-run" if args.dry_run else "applied-and-verified"; values["failure_code"]=""; _write(values); return 0
    except MaintenanceError as error:
        _write({"result":"failure","reconciliation_state":"rejected","request_id":args.request_id,"failure_code":error.code}); return 1
    finally:
        shutil.rmtree(state,ignore_errors=True)
if __name__=="__main__": raise SystemExit(main())
