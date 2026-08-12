"""Domain-neutral organization maintenance planning and bounded mutation."""
from __future__ import annotations

import base64, io, json, re, time, urllib.error, urllib.parse, urllib.request, zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from .maintenance_contract import MaintenanceContract, MaintenanceError, ProjectPolicy

_API_VERSION="2022-11-28"; _RETRYABLE={429,500,502,503,504}; _FAILED={"failure","cancelled","startup_failure"}; _USER_FAILED={"failure","timed_out","action_required"}
_SHARED_REF=re.compile(r"StreamScapeTV/ci-workflows/\.github/workflows/[^@\s\"']+@([A-Za-z0-9._/-]+)")

@dataclass
class OperationResult:
    result: str; request_id: str; mutation_count: int=0; retry_run_id: str=""; report_issue_url: str=""; decisions: list[dict[str,Any]]=field(default_factory=list); errors: list[str]=field(default_factory=list)
    @property
    def succeeded(self)->bool: return self.result=="success" and not self.errors

class MaintenanceApi(Protocol):
    def list_artifacts(self, repository:str)->list[Mapping[str,Any]]: ...
    def get_artifact(self, repository:str, artifact_id:int)->Mapping[str,Any]|None: ...
    def delete_artifact(self, repository:str, artifact_id:int)->None: ...
    def get_run(self, repository:str, run_id:int)->Mapping[str,Any]|None: ...
    def get_pull(self, repository:str, number:int)->Mapping[str,Any]|None: ...
    def list_closed_pulls(self, repository:str, base:str)->list[Mapping[str,Any]]: ...
    def get_branch(self, repository:str, branch:str)->Mapping[str,Any]|None: ...
    def delete_branch(self, repository:str, branch:str)->None: ...
    def list_workflow_files(self, repository:str)->list[str]: ...
    def get_file_text(self, repository:str, path:str, ref:str)->str|None: ...
    def list_open_issues(self, repository:str)->list[Mapping[str,Any]]: ...
    def create_issue(self, repository:str, title:str, body:str)->Mapping[str,Any]: ...
    def update_issue(self, repository:str, number:int, title:str, body:str)->Mapping[str,Any]: ...
    def list_attempt_jobs(self, repository:str, run_id:int, attempt:int)->list[Mapping[str,Any]]: ...
    def download_job_logs(self, repository:str, job_id:int, maximum_bytes:int)->str: ...
    def rerun_failed_jobs(self, repository:str, run_id:int)->None: ...

class GitHubApi:
    """Small REST client with bounded retry/pagination and no shell transport."""
    def __init__(self, token:str, *, api_url:str="https://api.github.com", max_attempts:int=3, opener:Any|None=None, sleep:Any=time.sleep)->None:
        if not token: raise MaintenanceError("credential_missing")
        if not api_url.startswith("https://"): raise MaintenanceError("unsafe_api_url")
        self.token=token; self.api=api_url.rstrip("/"); self.max_attempts=max(1,min(max_attempts,5)); self.opener=opener or urllib.request.urlopen; self.sleep=sleep
    def _url(self,path:str)->str:
        if path.startswith("https://"): return path
        if path.startswith("http://"): raise MaintenanceError("unsafe_api_url")
        return self.api+"/"+path.lstrip("/")
    def request(self, method:str, path:str, *, payload:Mapping[str,Any]|None=None, expected:Sequence[int]=(200,), allow_404:bool=False, raw:bool=False)->tuple[Any,Message]:
        url=self._url(path); data=None if payload is None else json.dumps(payload,separators=(",",":")).encode(); req=urllib.request.Request(url,data=data,method=method)
        for key,value in (("Accept","application/vnd.github+json"),("Authorization",f"Bearer {self.token}"),("X-GitHub-Api-Version",_API_VERSION),("User-Agent","StreamScapeTV-ci-workflows-maintenance")): req.add_header(key,value)
        if data is not None: req.add_header("Content-Type","application/json")
        for attempt in range(1,self.max_attempts+1):
            try:
                with self.opener(req,timeout=30) as response:
                    status=int(getattr(response,"status",response.getcode()))
                    if status not in expected: raise MaintenanceError("github_unexpected_status")
                    body=response.read(); value=body if raw else (None if not body else json.loads(body.decode()))
                    return value,response.headers
            except urllib.error.HTTPError as error:
                if allow_404 and error.code==404: return None,Message()
                if error.code in _RETRYABLE and attempt<self.max_attempts:
                    retry=error.headers.get("Retry-After","") if error.headers else ""; delay=float(retry) if str(retry).isdigit() else float(2**(attempt-1)); self.sleep(min(delay,30)); continue
                raise MaintenanceError("github_api_failed") from error
            except urllib.error.URLError as error:
                if attempt<self.max_attempts: self.sleep(float(2**(attempt-1))); continue
                raise MaintenanceError("github_api_failed") from error
            except (UnicodeDecodeError,json.JSONDecodeError) as error: raise MaintenanceError("github_response_invalid") from error
        raise MaintenanceError("github_api_failed")
    @staticmethod
    def _next(headers:Message)->str|None:
        for part in headers.get("Link","").split(","):
            part=part.strip()
            if 'rel="next"' in part:
                if not part.startswith("<") or ">" not in part: raise MaintenanceError("pagination_invalid")
                return part[1:part.index(">")]
        return None
    def paginate(self,path:str,*,collection_key:str|None=None,maximum_pages:int=20)->list[Mapping[str,Any]]:
        out=[]; next_path: str|None=path; pages=0
        while next_path:
            pages+=1
            if pages>maximum_pages: raise MaintenanceError("pagination_bound_exceeded")
            payload,headers=self.request("GET",next_path); values=payload if collection_key is None else payload.get(collection_key) if isinstance(payload,Mapping) else None
            if not isinstance(values,list) or any(not isinstance(x,Mapping) for x in values): raise MaintenanceError("github_response_invalid")
            out.extend(values); next_path=self._next(headers)
        return out
    @staticmethod
    def _repo(repository:str)->str: return "/".join(urllib.parse.quote(p,safe="") for p in repository.split("/",1))
    def list_artifacts(self,r): return self.paginate(f"/repos/{self._repo(r)}/actions/artifacts?per_page=100",collection_key="artifacts")
    def get_artifact(self,r,i):
        p,_=self.request("GET",f"/repos/{self._repo(r)}/actions/artifacts/{i}",allow_404=True); return p if isinstance(p,Mapping) else None
    def delete_artifact(self,r,i): self.request("DELETE",f"/repos/{self._repo(r)}/actions/artifacts/{i}",expected=(204,))
    def get_run(self,r,i):
        p,_=self.request("GET",f"/repos/{self._repo(r)}/actions/runs/{i}",allow_404=True); return p if isinstance(p,Mapping) else None
    def get_pull(self,r,i):
        p,_=self.request("GET",f"/repos/{self._repo(r)}/pulls/{i}",allow_404=True); return p if isinstance(p,Mapping) else None
    def list_closed_pulls(self,r,base): return self.paginate(f"/repos/{self._repo(r)}/pulls?"+urllib.parse.urlencode({"state":"closed","base":base,"per_page":100}),maximum_pages=5)
    def get_branch(self,r,b):
        p,_=self.request("GET",f"/repos/{self._repo(r)}/branches/{urllib.parse.quote(b,safe='')}",allow_404=True); return p if isinstance(p,Mapping) else None
    def delete_branch(self,r,b): self.request("DELETE",f"/repos/{self._repo(r)}/git/refs/heads/{urllib.parse.quote(b,safe='')}",expected=(204,))
    def list_workflow_files(self,r):
        p,_=self.request("GET",f"/repos/{self._repo(r)}/contents/.github/workflows",allow_404=True)
        if p is None: return []
        if not isinstance(p,list): raise MaintenanceError("workflow_inventory_unreadable")
        return sorted(str(x["path"]) for x in p if isinstance(x,Mapping) and isinstance(x.get("path"),str) and str(x["path"]).endswith((".yml",".yaml")))
    def get_file_text(self,r,path,ref):
        q="/".join(urllib.parse.quote(p,safe="") for p in path.split("/")); p,_=self.request("GET",f"/repos/{self._repo(r)}/contents/{q}?"+urllib.parse.urlencode({"ref":ref}),allow_404=True)
        if p is None:return None
        if not isinstance(p,Mapping) or p.get("type")!="file" or p.get("encoding")!="base64" or not isinstance(p.get("content"),str): raise MaintenanceError("workflow_inventory_unreadable")
        try:return base64.b64decode(p["content"],validate=False).decode()
        except (ValueError,UnicodeDecodeError) as error: raise MaintenanceError("workflow_inventory_unreadable") from error
    def list_open_issues(self,r): return self.paginate(f"/repos/{self._repo(r)}/issues?state=open&per_page=100",maximum_pages=10)
    def create_issue(self,r,title,body):
        p,_=self.request("POST",f"/repos/{self._repo(r)}/issues",payload={"title":title,"body":body},expected=(201,)); return p
    def update_issue(self,r,n,title,body):
        p,_=self.request("PATCH",f"/repos/{self._repo(r)}/issues/{n}",payload={"title":title,"body":body}); return p
    def list_attempt_jobs(self,r,run,attempt): return self.paginate(f"/repos/{self._repo(r)}/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100",collection_key="jobs",maximum_pages=2)
    def download_job_logs(self,r,job,maximum_bytes):
        raw,_=self.request("GET",f"/repos/{self._repo(r)}/actions/jobs/{job}/logs",raw=True); data=bytes(raw)
        if len(data)>maximum_bytes: raise MaintenanceError("job_logs_too_large")
        if data[:2]==b"PK":
            try:
                chunks=[]; total=0
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for name in sorted(archive.namelist()):
                        chunk=archive.read(name); total+=len(chunk)
                        if total>maximum_bytes: raise MaintenanceError("job_logs_too_large")
                        chunks.append(chunk)
                data=b"\n".join(chunks)
            except zipfile.BadZipFile as error: raise MaintenanceError("job_logs_invalid") from error
        return data.decode(errors="replace")
    def rerun_failed_jobs(self,r,run): self.request("POST",f"/repos/{self._repo(r)}/actions/runs/{run}/rerun-failed-jobs",expected=(201,))

def _nested(v:Any,*keys:str)->Any:
    for key in keys:
        if not isinstance(v,Mapping):return None
        v=v.get(key)
    return v
def _positive(v:Any,code="github_response_invalid")->int:
    if isinstance(v,bool) or not isinstance(v,int) or v<=0: raise MaintenanceError(code)
    return v
def _timestamp(v:Any)->datetime:
    if not isinstance(v,str) or not v: raise MaintenanceError("github_timestamp_invalid")
    try:t=datetime.fromisoformat(v[:-1]+"+00:00" if v.endswith("Z") else v)
    except ValueError as error: raise MaintenanceError("github_timestamp_invalid") from error
    if t.tzinfo is None: raise MaintenanceError("github_timestamp_invalid")
    return t.astimezone(timezone.utc)
def load_json_file(root:Path,relative:str)->Mapping[str,Any]:
    try:p=json.loads((root/relative).read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as error: raise MaintenanceError("supporting_contract_invalid") from error
    if not isinstance(p,Mapping): raise MaintenanceError("supporting_contract_invalid")
    return p

def _artifact_snapshot(x): return (x.get("id"),x.get("name"),x.get("size_in_bytes"),x.get("created_at"),_nested(x,"workflow_run","id"))
def _retained(x,exceptions,now):
    name=x.get("name"); created=_timestamp(x.get("created_at")); rows=exceptions.get("exceptions")
    if not isinstance(name,str) or not isinstance(rows,list): raise MaintenanceError("artifact_exception_contract_invalid")
    for row in rows:
        if not isinstance(row,Mapping): raise MaintenanceError("artifact_exception_contract_invalid")
        names=row.get("allowed_names"); days=row.get("maximum_retention_days")
        if isinstance(names,list) and name in names and isinstance(days,int) and days>0 and now-created<=timedelta(days=days): return True
    return False

def artifacts(contract:MaintenanceContract,api:MaintenanceApi,*,root:Path,repository_scope:str,dry_run:bool,request_id:str,now:datetime|None=None)->OperationResult:
    contract.validate_request_id(request_id); policy=contract.operation("artifacts"); exceptions=load_json_file(root,contract.artifact_exceptions_path)
    if exceptions.get("schema_version")!=1: raise MaintenanceError("artifact_exception_contract_invalid")
    current=(now or datetime.now(timezone.utc)).astimezone(timezone.utc); cutoff=current-timedelta(hours=int(policy["minimum_age_hours"])); candidates=[]; result=OperationResult("success",request_id)
    for project in contract.selected_projects(repository_scope):
        for item in api.list_artifacts(project.repository):
            aid=_positive(item.get("id"),"artifact_invalid")
            if _timestamp(item.get("created_at"))>cutoff: continue
            if _retained(item,exceptions,current): result.decisions.append({"repository":project.repository,"artifact_id":aid,"action":"preserve","reason":"retained_artifact_exception"}); continue
            rid=_nested(item,"workflow_run","id")
            if rid is not None:
                run=api.get_run(project.repository,_positive(rid))
                if run is not None and run.get("status")!="completed": result.decisions.append({"repository":project.repository,"artifact_id":aid,"action":"preserve","reason":"workflow_run_not_completed"}); continue
            candidates.append((project.repository,item))
    if len(candidates)>int(policy["maximum_deletions"]): raise MaintenanceError("artifact_deletion_bound_exceeded")
    for repo,old in candidates:
        aid=int(old["id"]); fresh=api.get_artifact(repo,aid)
        if fresh is None: result.decisions.append({"repository":repo,"artifact_id":aid,"action":"none","reason":"artifact_already_absent"}); continue
        if _artifact_snapshot(fresh)!=_artifact_snapshot(old): raise MaintenanceError("artifact_changed_before_delete")
        action="would_delete" if dry_run else "delete"
        if not dry_run: api.delete_artifact(repo,aid); result.mutation_count+=1
        result.decisions.append({"repository":repo,"artifact_id":aid,"action":action,"reason":"expired_completed_run_artifact"})
    return result

def _pull_snapshot(p): return (p.get("number"),p.get("state"),p.get("merged_at"),_nested(p,"head","sha"),_nested(p,"head","ref"),_nested(p,"head","repo","full_name"),_nested(p,"base","ref"),_nested(p,"base","repo","full_name"))
def _select_pull(api,project,pr_number,sha,prefix):
    pulls=[]
    if pr_number is not None:
        p=api.get_pull(project.repository,pr_number); pulls=[] if p is None else [p]
    else: pulls=api.list_closed_pulls(project.repository,project.integration_branch)
    matches=[p for p in pulls if p.get("state")=="closed" and p.get("merged_at") and _nested(p,"head","sha")==sha and _nested(p,"head","repo","full_name")==project.repository and _nested(p,"base","ref")==project.integration_branch and _nested(p,"base","repo","full_name")==project.repository and isinstance(_nested(p,"head","ref"),str) and str(_nested(p,"head","ref")).startswith(prefix)]
    if len(matches)!=1: raise MaintenanceError("exact_merged_pull_not_found")
    return matches[0]
def branches(contract,api,*,project_id,pr_number,expected_head_sha,dry_run,request_id):
    contract.validate_request_id(request_id); contract.validate_sha(expected_head_sha); project=contract.project(project_id); pull=_select_pull(api,project,pr_number,expected_head_sha,str(contract.operation("branches")["branch_prefix"])); branch=str(_nested(pull,"head","ref")); result=OperationResult("success",request_id)
    current=api.get_branch(project.repository,branch)
    if current is None: result.decisions.append({"repository":project.repository,"branch":branch,"action":"none","reason":"branch_already_absent"}); return result
    if branch==project.integration_branch or current.get("protected") is True: raise MaintenanceError("protected_branch_rejected")
    if _nested(current,"commit","sha")!=expected_head_sha: raise MaintenanceError("branch_changed_before_delete")
    if dry_run: result.decisions.append({"repository":project.repository,"branch":branch,"action":"would_delete","reason":"exact_tip_merged_by_pull_request"}); return result
    rp=api.get_pull(project.repository,int(pull["number"])); rb=api.get_branch(project.repository,branch)
    if rp is None or _pull_snapshot(rp)!=_pull_snapshot(pull) or rb is None or rb.get("protected") is True or _nested(rb,"commit","sha")!=expected_head_sha: raise MaintenanceError("branch_changed_before_delete")
    api.delete_branch(project.repository,branch); result.mutation_count=1; result.decisions.append({"repository":project.repository,"branch":branch,"action":"delete","reason":"exact_tip_merged_by_pull_request"}); return result

def _inventory_repo(inv,repo):
    rows=inv.get("repositories")
    if not isinstance(rows,list): raise MaintenanceError("workflow_inventory_invalid")
    for row in rows:
        if isinstance(row,Mapping) and row.get("repository")==repo:return row
    raise MaintenanceError("workflow_inventory_missing_repository")
def _workflow_rows(repo):
    rows=repo.get("workflows")
    if not isinstance(rows,list): raise MaintenanceError("workflow_inventory_invalid")
    out={}
    for row in rows:
        if not isinstance(row,list) or len(row)!=7 or not isinstance(row[0],str): raise MaintenanceError("workflow_inventory_invalid")
        out[row[0]]=tuple(row)
    return out

def conformance(contract,api,*,root,repository_scope,dry_run,request_id):
    contract.validate_request_id(request_id); policy=contract.operation("conformance"); inv=load_json_file(root,contract.workflow_inventory_path); findings=[]
    if inv.get("schema_version")!=2: raise MaintenanceError("workflow_inventory_invalid")
    for project in contract.selected_projects(repository_scope):
        expected=_workflow_rows(_inventory_repo(inv,project.repository)); live=set(api.list_workflow_files(project.repository))
        for path in sorted(set(expected)-live): findings.append({"repository":project.repository,"path":path,"kind":"missing_inventory_workflow"})
        for path in sorted(live-set(expected)): findings.append({"repository":project.repository,"path":path,"kind":"unregistered_live_workflow"})
        for path in sorted(live & set(expected)):
            row=expected[path]
            if row[3]=="retire": findings.append({"repository":project.repository,"path":path,"kind":"retired_agent_state_transport_present" if row[5]=="legacy-agent-state" else "retired_workflow_present"})
            text=api.get_file_text(project.repository,path,project.integration_branch)
            if text is None: findings.append({"repository":project.repository,"path":path,"kind":"workflow_disappeared_during_scan"}); continue
            for ref in sorted(set(_SHARED_REF.findall(text))): findings.append({"repository":project.repository,"path":path,"kind":"shared_workflow_reference","reference":ref,"immutable":bool(re.fullmatch(r"[0-9a-f]{40}",ref))})
        if len(findings)>int(policy["maximum_findings"]): raise MaintenanceError("conformance_finding_bound_exceeded")
    findings.sort(key=lambda x:(str(x.get("repository")),str(x.get("path")),str(x.get("kind")),str(x.get("reference","")))); result=OperationResult("success",request_id,decisions=findings)
    if dry_run:return result
    repo=str(policy["report_repository"]); scope=repository_scope.strip() or "all"; title=f"{policy['report_title_prefix']}: {scope}"; body=f"<!-- ci-workflows-maintenance:{scope} -->\n# Organization conformance report\n\n- Request: `{request_id}`\n- Scope: `{scope}`\n- Findings: **{len(findings)}**\n\n```json\n{json.dumps(findings,indent=2,sort_keys=True)}\n```\n"
    if len(body.encode())>60000: raise MaintenanceError("conformance_report_too_large")
    existing=[x for x in api.list_open_issues(repo) if x.get("title")==title and "pull_request" not in x]
    if len(existing)>1: raise MaintenanceError("conformance_report_ambiguous")
    issue=api.update_issue(repo,_positive(existing[0].get("number")),title,body) if existing else api.create_issue(repo,title,body); result.mutation_count=1; url=issue.get("html_url"); result.report_issue_url=url if isinstance(url,str) else ""; return result

def _run_path(run):
    value=run.get("path"); return value.split("@",1)[0] if isinstance(value,str) else ""
def _retry_allowed(inv,project,run,trust):
    row=_workflow_rows(_inventory_repo(inv,project.repository)).get(_run_path(run)); return bool(row and row[3]!="retire" and row[5] in trust)
def _user_step_failed(job):
    steps=job.get("steps"); return isinstance(steps,list) and any(isinstance(s,Mapping) and str(s.get("name","")).strip().casefold()!="set up job" and s.get("conclusion") in _USER_FAILED for s in steps)
def _classify(job,logs,policy):
    _positive(job.get("id"),"job_invalid"); labels=job.get("labels")
    if job.get("conclusion") not in _FAILED:return False,"job_conclusion_not_retryable"
    if not isinstance(labels,list) or "self-hosted" not in {str(x).casefold() for x in labels}:return False,"job_not_self_hosted"
    if _user_step_failed(job):return False,"user_step_failed"
    low=logs.casefold()
    if any(str(x).casefold() in low for x in policy["product_failure_signatures"]):return False,"deterministic_product_failure"
    if any(str(x).casefold() in low for x in policy["infrastructure_signatures"]):return True,"runner_infrastructure_failure"
    return False,"no_proven_runner_infrastructure_signature"
def _run_snapshot(run): return (run.get("id"),run.get("workflow_id"),_run_path(run),run.get("event"),run.get("status"),run.get("conclusion"),run.get("run_attempt"),run.get("head_branch"),run.get("head_sha"),_nested(run,"head_repository","full_name"),run.get("pull_requests"))
def _target_current(api,project,run):
    if run.get("event")=="pull_request":
        pulls=run.get("pull_requests")
        if not isinstance(pulls,list):return False
        matches=[p for p in pulls if isinstance(p,Mapping) and _nested(p,"head","sha")==run.get("head_sha") and _nested(p,"head","repo","full_name")==project.repository and _nested(p,"base","ref")==project.integration_branch]
        if len(matches)!=1 or not isinstance(matches[0].get("number"),int):return False
        current=api.get_pull(project.repository,int(matches[0]["number"])); return bool(current and current.get("state")=="open" and _nested(current,"head","sha")==run.get("head_sha") and _nested(current,"head","repo","full_name")==project.repository and _nested(current,"base","ref")==project.integration_branch)
    if run.get("head_branch")!=project.integration_branch:return False
    current=api.get_branch(project.repository,project.integration_branch); return bool(current and _nested(current,"commit","sha")==run.get("head_sha"))
def runner_retry(contract,api,*,root,project_id,run_id,expected_head_sha,dry_run,request_id):
    contract.validate_request_id(request_id); contract.validate_sha(expected_head_sha); project=contract.project(project_id); policy=contract.operation("runner_retry"); inv=load_json_file(root,contract.workflow_inventory_path); run=api.get_run(project.repository,run_id)
    if run is None: raise MaintenanceError("run_not_found")
    if not _retry_allowed(inv,project,run,set(policy["allowed_inventory_trust"])): raise MaintenanceError("workflow_not_allowlisted")
    if run.get("status")!="completed" or run.get("conclusion") not in {"failure","cancelled"} or run.get("run_attempt")!=1 or run.get("event") not in policy["allowed_events"] or run.get("head_sha")!=expected_head_sha or _nested(run,"head_repository","full_name")!=project.repository: raise MaintenanceError("run_not_retryable")
    if not _target_current(api,project,run): raise MaintenanceError("run_target_no_longer_current")
    failed=[j for j in api.list_attempt_jobs(project.repository,run_id,1) if j.get("conclusion") in _FAILED]
    if not failed: raise MaintenanceError("no_failed_jobs")
    if len(failed)>int(policy["maximum_failed_jobs"]): raise MaintenanceError("failed_job_bound_exceeded")
    decisions=[]
    for job in failed:
        jid=_positive(job.get("id"),"job_invalid"); ok,reason=_classify(job,api.download_job_logs(project.repository,jid,int(policy["maximum_log_bytes"])),policy); decisions.append({"job_id":jid,"eligible":ok,"reason":reason})
        if not ok: raise MaintenanceError("deterministic_or_unproven_failure")
    fresh=api.get_run(project.repository,run_id)
    if fresh is None or _run_snapshot(fresh)!=_run_snapshot(run) or not _target_current(api,project,fresh): raise MaintenanceError("run_changed_before_retry")
    result=OperationResult("success",request_id,retry_run_id=str(run_id),decisions=decisions)
    if not dry_run: api.rerun_failed_jobs(project.repository,run_id); result.mutation_count=1
    return result

def render_result(value:OperationResult)->dict[str,str]:
    return {"result":"success" if value.succeeded else "failure","mutation_count":str(value.mutation_count),"retry_run_id":value.retry_run_id,"report_issue_url":value.report_issue_url,"request_id":value.request_id,"decision_count":str(len(value.decisions))}
