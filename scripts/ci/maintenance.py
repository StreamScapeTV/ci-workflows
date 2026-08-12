#!/usr/bin/env python3
"""CLI adapter for bounded organization maintenance operations."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/"src"))
from ci_workflows.maintenance import GitHubApi, artifacts, branches, conformance, render_result, runner_retry
from ci_workflows.maintenance_contract import MaintenanceError, load_contract

def _bool(value:str)->bool:
    low=value.casefold()
    if low=="true": return True
    if low=="false": return False
    raise argparse.ArgumentTypeError("expected true or false")
def _out(values:dict[str,str])->None:
    path=os.environ.get("GITHUB_OUTPUT","")
    if path:
        with Path(path).open("a",encoding="utf-8") as out:
            for name,value in values.items():
                if "\n" in value or "\r" in value: raise MaintenanceError("output_invalid")
                out.write(f"{name}={value}\n")
def parser()->argparse.ArgumentParser:
    root=argparse.ArgumentParser(); sub=root.add_subparsers(dest="operation",required=True)
    a=sub.add_parser("artifacts"); a.add_argument("--repository-scope",default=""); a.add_argument("--dry-run",type=_bool,required=True); a.add_argument("--request-id",required=True)
    b=sub.add_parser("branches"); b.add_argument("--project-id",required=True); b.add_argument("--pr-number",type=int); b.add_argument("--expected-head-sha",required=True); b.add_argument("--dry-run",type=_bool,required=True); b.add_argument("--request-id",required=True)
    c=sub.add_parser("conformance"); c.add_argument("--repository-scope",default=""); c.add_argument("--dry-run",type=_bool,required=True); c.add_argument("--request-id",required=True)
    r=sub.add_parser("runner-retry"); r.add_argument("--project-id",required=True); r.add_argument("--run-id",type=int,required=True); r.add_argument("--expected-head-sha",required=True); r.add_argument("--dry-run",type=_bool,required=True); r.add_argument("--request-id",required=True)
    return root
def main(argv:list[str]|None=None)->int:
    args=parser().parse_args(argv); token=os.environ.get("MAINTENANCE_GITHUB_TOKEN","")
    if not token: print("maintenance credential is required",file=sys.stderr); return 2
    contract=load_contract(ROOT); api=GitHubApi(token,api_url=os.environ.get("GITHUB_API_URL","https://api.github.com"))
    try:
        if args.operation=="artifacts": result=artifacts(contract,api,root=ROOT,repository_scope=args.repository_scope,dry_run=args.dry_run,request_id=args.request_id)
        elif args.operation=="branches": result=branches(contract,api,project_id=args.project_id,pr_number=args.pr_number,expected_head_sha=args.expected_head_sha,dry_run=args.dry_run,request_id=args.request_id)
        elif args.operation=="conformance": result=conformance(contract,api,root=ROOT,repository_scope=args.repository_scope,dry_run=args.dry_run,request_id=args.request_id)
        else: result=runner_retry(contract,api,root=ROOT,project_id=args.project_id,run_id=args.run_id,expected_head_sha=args.expected_head_sha,dry_run=args.dry_run,request_id=args.request_id)
    except MaintenanceError as error:
        values={"result":"failure","mutation_count":"0","retry_run_id":"","report_issue_url":"","request_id":getattr(args,"request_id",""),"failure_code":error.code}; _out(values); print(json.dumps(values,sort_keys=True)); return 1
    values=render_result(result); values["failure_code"]=""; _out(values); print(json.dumps(values,sort_keys=True)); return 0
if __name__=="__main__": raise SystemExit(main())
