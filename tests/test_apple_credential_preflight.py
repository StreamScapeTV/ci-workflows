from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts/ci"))
import apple_credential_preflight as preflight
from tests.test_repository_apple_physical_device import (
    SIGNING_CERT_FINGERPRINT, SIGNING_CERT_NAME, SIGNING_CERT_PEM,
)


class CredentialPreflightTests(unittest.TestCase):
    def test_fixed_workflow_never_exposes_root_secrets_to_a_caller_or_pr(self) -> None:
        workflow=yaml.safe_load((ROOT/".github/workflows/apple-credential-preflight.yml").read_text())
        self.assertEqual(workflow["on"],{"workflow_dispatch":None})
        self.assertEqual(workflow["permissions"],{"contents":"read"})
        job=workflow["jobs"]["preflight"]
        self.assertEqual(job["runs-on"],["macos-latest-xl"])
        self.assertIn("refs/heads/main",job["if"])
        self.assertIn("StreamScapeTV/ci-workflows",job["if"])
        self.assertIn("workflow_dispatch",job["if"])
        secret_step=next(s for s in job["steps"] if s.get("name","").startswith("Verify configured"))
        self.assertEqual(set(secret_step["env"])-{"RUNNER_ENVIRONMENT"},{
            "PREFLIGHT_P12_BASE64","PREFLIGHT_P12_PASSWORD","PREFLIGHT_APPLE_TEAM_ID",
            "PREFLIGHT_ASC_P8_BASE64","PREFLIGHT_ASC_KEY_ID","PREFLIGHT_ASC_ISSUER_ID"})
        self.assertTrue(all("secrets." in value for key,value in secret_step["env"].items() if key!="RUNNER_ENVIRONMENT"))
        self.assertNotIn("workflow_call",str(workflow))
        self.assertNotIn("pull_request",str(workflow))
        self.assertNotIn("id-token",str(workflow))
        self.assertIn("always()",job["steps"][-1]["if"])
        self.assertNotIn("secrets.",str(job["steps"][-1]))

    def test_ecdsa_jose_der_is_exact_64_bytes_and_rejects_malformed(self):
        raw=bytes.fromhex("3006020101020102")
        converted=preflight._ecdsa_der_to_jose(raw)
        self.assertEqual(converted,bytes(31)+b"\x01"+bytes(31)+b"\x02")
        self.assertEqual(len(converted),64)
        for candidate in (b"",raw+b"\x00",bytes.fromhex("30060201ff020102"),bytes.fromhex("3006020100020102"),bytes.fromhex("3008020101020102")):
            with self.subTest(candidate=candidate),self.assertRaises(ValueError):
                preflight._ecdsa_der_to_jose(candidate)

    def test_public_certificate_read_failure_does_not_accuse_credentials(self):
        with tempfile.TemporaryDirectory() as td:
            p=preflight.Preflight(Path(td))
            inventory=f'1) {SIGNING_CERT_FINGERPRINT} "{SIGNING_CERT_NAME}"\n1 identity found\n'.encode()
            calls=[]
            def command(args,**_kwargs):
                calls.append(args)
                if args[:2]==['security','find-identity']:
                    return inventory
                if args[:2]==['security','find-certificate']:
                    raise RuntimeError('private certificate tool error')
                return b''
            credentials=('ABCDE12345',b'\x30'+b'a'*63,'synthetic-password',b'','','')
            with (patch.object(p,'_create_keychain'),patch.object(preflight,'_credentials',return_value=credentials),
                  patch.object(preflight,'_call',side_effect=command)):
                output=io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(p.run(),1)
            self.assertIn('P12_IMPORT=PASS',output.getvalue())
            self.assertIn('PRIVATE_KEY_PRESENT=PASS',output.getvalue())
            self.assertIn('CERTIFICATE_READ=READ_FAILED',output.getvalue())
            self.assertIn('CERT_TEAM_MATCH=NOT_APPLICABLE',output.getvalue())
            self.assertIn('API_AUTH=INVALID_KEY',output.getvalue())
            self.assertIn('CLEANUP=PASS',output.getvalue())
            self.assertNotIn('private certificate tool error',output.getvalue())
            self.assertNotIn(['openssl','pkcs12'],[args[:2] for args in calls])
            self.assertFalse(p.root.exists())

    def test_api_status_is_fixed_without_provider_values(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for status,expected_stage,expected_result in ((401,"API_AUTH","UNAUTHORIZED_401"),(403,"API_CERT_ACCESS","FORBIDDEN_403"),(0,"API_AUTH","NETWORK_ERROR"),(500,"API_AUTH","HTTP_ERROR")):
                with self.subTest(status=status):
                    p=preflight.Preflight(root)
                    p._secure_root()
                    with patch.object(preflight,"_jwt",return_value="private-token"),patch.object(preflight,"_apple_certificates",return_value=(status,b"")):
                        with self.assertRaises(preflight.PreflightFailure) as fail:
                            p._api(b"-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----", "AAAAA12345", "fd8567df-5955-4ae5-bf23-13e023d10a6d")
                    self.assertEqual((fail.exception.stage,fail.exception.code),(expected_stage,expected_result))
                    self.assertNotIn("private",str(fail.exception))
                    p.cleanup()
            p=preflight.Preflight(root)
            p._secure_root()
            p.serial="ABCDEF"
            payload=json.dumps({"data":[{"attributes":{"serialNumber":"abcdef"}}]}).encode()
            with patch.object(preflight,"_jwt",return_value="hidden.jwt"),patch.object(preflight,"_apple_certificates",return_value=(200,payload)) as request:
                p._api(b"-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----","AAAAA12345","fd8567df-5955-4ae5-bf23-13e023d10a6d")
            self.assertEqual(p.status["API_AUTH"],"PASS")
            self.assertEqual(p.status["API_CERT_ACCESS"],"PASS")
            self.assertEqual(p.status["API_CERT_ACCOUNT_MATCH"],"PASS")
            self.assertEqual(request.call_args.args[1],"ABCDEF")
            p.cleanup()

    def test_run_uses_isolated_keychain_and_cleans_all_state(self):
        fingerprint=SIGNING_CERT_FINGERPRINT
        common_name=SIGNING_CERT_NAME
        trace=[]
        with tempfile.TemporaryDirectory() as td:
            tmp=Path(td)
            original="/Users/runner/Library/Keychains/login.keychain-db"
            def private_call(args,**kwargs):
                trace.append(args)
                if args[:2]==["security","list-keychains"] and len(args)==4:
                    return f'"{original}"\n'.encode()
                if args[:2]==["security","default-keychain"] and len(args)==4:
                    return f'"{original}"\n'.encode()
                if args[:2]==["security","create-keychain"]:
                    Path(args[-1]).write_bytes(b"stub")
                if args[:2]==["security","delete-keychain"]:
                    Path(args[-1]).unlink()
                if args[:3]==["security","find-identity","-p"]:
                    return (f'Matching identities\n  1) {fingerprint} "{common_name}"\n1 identity found\n'
                            f'Valid identities only\n  1) {fingerprint} "{common_name}"\n1 valid identity found\n').encode()
                if args[:3]==["security","find-identity","-v"]:
                    return f'  1) {fingerprint} "{common_name}"\n1 valid identity found\n'.encode()
                if args[:2]==["security","find-certificate"]:
                    return SIGNING_CERT_PEM
                if args[:2]==["openssl","pkcs12"]:
                    self.fail('must not decrypt a natively imported P12 a second time')
                if args[:2]==["xcrun","clang"]:
                    Path(args[-1]).write_bytes(b"mach-o")
                return b""
            p=preflight.Preflight(tmp)
            def cred():
                return "ABCDE12345",b"\x30"+b"x"*63,"private-password",b"-----BEGIN PRIVATE KEY-----\nfoo\n-----END PRIVATE KEY-----","AAAAA12345","fd8567df-5955-4ae5-bf23-13e023d10a6d"
            proof=json.dumps({"data":[{"attributes":{"serialNumber":"123ABC"}}]}).encode()
            with patch.object(preflight,"_credentials",side_effect=cred),patch.object(preflight,"_call",side_effect=private_call),patch.object(preflight,"_jwt",return_value="hidden.jwt"),patch.object(preflight,"_apple_certificates",return_value=(200,proof)):
                output=io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(p.run(),0)
            self.assertIn("P12_IMPORT=PASS",output.getvalue())
            self.assertIn("API_CERT_ACCOUNT_MATCH=PASS",output.getvalue())
            self.assertIn("CERTIFICATE_READ=PASS",output.getvalue())
            self.assertIn("CLEANUP=PASS",output.getvalue())
            for private in ("private-password","ABCDE12345",fingerprint,"123ABC","hidden.jwt",str(tmp),common_name):
                self.assertNotIn(private,output.getvalue())
            self.assertFalse((tmp/preflight.ROOT_NAME).exists())
            self.assertTrue(any(args[:2]==["codesign","--force"] for args in trace))
            self.assertTrue(any(args[:2]==["codesign","--verify"] for args in trace))
            self.assertTrue(any(args[:2]==["security","delete-keychain"] for args in trace))

    def test_real_ou_match_with_different_display_suffix_passes_preflight(self):
        fingerprint=SIGNING_CERT_FINGERPRINT
        name=SIGNING_CERT_NAME
        original="/Users/runner/login.keychain-db"
        with tempfile.TemporaryDirectory() as td:
            temp=Path(td)
            def command(args,**kwargs):
                if args[:2]==["security","list-keychains"] and len(args)==4:
                    return f'"{original}"\n'.encode()
                if args[:2]==["security","default-keychain"] and len(args)==4:
                    return f'"{original}"\n'.encode()
                if args[:2]==["security","create-keychain"]:
                    Path(args[-1]).write_bytes(b"keychain")
                if args[:2]==["security","delete-keychain"]:
                    Path(args[-1]).unlink()
                if args[:3]==["security","find-identity","-p"]:
                    return (f'Matching identities\n1) {fingerprint} "{name}"\n1 identity found\n'
                            f'Valid identities only\n1) {fingerprint} "{name}"\n1 valid identity found\n').encode()
                if args[:3]==["security","find-identity","-v"]:
                    return f'1) {fingerprint} "{name}"\n1 valid identity found\n'.encode()
                if args[:2]==["security","find-certificate"]:
                    return SIGNING_CERT_PEM
                if args[:2]==["openssl","pkcs12"]:
                    self.fail('must not decrypt a natively imported P12 a second time')
                if args[:2]==["xcrun","clang"]:
                    Path(args[-1]).write_bytes(b"binary")
                return b""
            p=preflight.Preflight(temp)
            pem=b"-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
            creds=("ABCDE12345",b"\x30"+b"a"*63,"secret-password",pem,"ABCDE12345","fd8567df-5955-4ae5-bf23-13e023d10a6d")
            response=json.dumps({"data":[{"attributes":{"serialNumber":"123ABC"}}]}).encode()
            with patch.object(preflight,"_credentials",return_value=creds),patch.object(preflight,"_call",side_effect=command),patch.object(preflight,"_jwt",return_value="private"),patch.object(preflight,"_apple_certificates",return_value=(200,response)):
                output=io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(p.run(),0)
            self.assertIn("CERT_TEAM_MATCH=PASS",output.getvalue())
            self.assertNotIn("CERT_NAME_POLICY",output.getvalue())
            self.assertIn("CODESIGN_VERIFY=PASS",output.getvalue())
            self.assertFalse((temp/preflight.ROOT_NAME).exists())
            self.assertNotIn("PERSON1234",output.getvalue())

    def test_p12_password_import_failure_is_sanitized_and_cleaned(self):
        with tempfile.TemporaryDirectory() as td:
            temp=Path(td)
            original="/Users/runner/login.keychain-db"
            commands=[]
            def command(args,**kwargs):
                commands.append(args)
                if args[:2]==["security","list-keychains"] and len(args)==4:
                    return f'"{original}"\n'.encode()
                if args[:2]==["security","default-keychain"] and len(args)==4:
                    return f'"{original}"\n'.encode()
                if args[:2]==["security","create-keychain"]:
                    Path(args[-1]).write_bytes(b"keychain")
                if args[:2]==["security","import"]:
                    raise RuntimeError("sensitive-provider-stderr")
                if args[:2]==["security","delete-keychain"]:
                    Path(args[-1]).unlink()
                return b""
            creds=("ABCDE12345",b"\x30"+b"a"*63,"private-password",b"", "", "")
            p=preflight.Preflight(temp)
            with patch.object(preflight,"_credentials",return_value=creds),patch.object(preflight,"_call",side_effect=command):
                out=io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(p.run(),1)
            self.assertIn("P12_IMPORT=IMPORT_FAILED",out.getvalue())
            self.assertIn("API_AUTH=INVALID_KEY",out.getvalue())
            self.assertNotIn("sensitive",out.getvalue())
            self.assertNotIn("private-password",out.getvalue())
            self.assertFalse((temp/preflight.ROOT_NAME).exists())
            self.assertTrue(any(a[:2]==["security","delete-keychain"] for a in commands))

    def test_private_key_present_but_security_trust_invalid_does_not_codesign(self):
        fingerprint=SIGNING_CERT_FINGERPRINT
        name=SIGNING_CERT_NAME
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            pre=preflight.Preflight(root)
            pre._secure_root()
            original="/Users/runner/login.keychain-db"
            calls=[]
            def private(args,**kwargs):
                calls.append(args)
                if args[:2]==["security","list-keychains"] and len(args)==4:
                    return f'"{original}"\n'.encode()
                if args[:2]==["security","default-keychain"] and len(args)==4:
                    return f'"{original}"\n'.encode()
                if args[:2]==["security","create-keychain"]:
                    Path(args[-1]).write_bytes(b"keychain")
                if args[:2]==["security","delete-keychain"]:
                    Path(args[-1]).unlink()
                if args[:3]==["security","find-identity","-p"]:
                    return (f'Matching identities\n1) {fingerprint} "{name}"\n1 identity found\n'
                            f'Valid identities only\n0 valid identities found\n').encode()
                if args[:3]==["security","find-identity","-v"]:
                    return b"0 valid identities found\n"
                if args[:2]==["security","find-certificate"]:
                    return SIGNING_CERT_PEM
                return b""
            with patch.object(preflight,"_call",side_effect=private):
                with self.assertRaises(preflight.PreflightFailure) as caught:
                    pre._p12("ABCDE12345",b"\x30"+b"a"*63,"private-password")
                self.assertEqual((caught.exception.stage,caught.exception.code),("CERT_TRUST_VALID","NO_VALID_IDENTITY"))
                pre.cleanup()
            self.assertEqual(pre.status["PRIVATE_KEY_PRESENT"],"PASS")
            self.assertEqual(pre.status["CERT_TEAM_MATCH"],"PASS")
            self.assertNotIn("codesign",[args[0] for args in calls])
            self.assertFalse((root/preflight.ROOT_NAME).exists())

    def test_cleanup_always_purges_credentials_and_probes_then_retries_context(self):
        for failed_operation in ('list-keychains','default-keychain','delete-keychain'):
            with self.subTest(operation=failed_operation),tempfile.TemporaryDirectory() as td:
                p=preflight.Preflight(Path(td))
                p._secure_root()
                p.state.write_text(json.dumps({'search':['/synthetic/login.keychain-db'],'default':'/synthetic/login.keychain-db'}))
                p.keychain.write_bytes(b'synthetic-keychain')
                for name in ('owner.p12','api.p8','pub.pem','leaf.pem','probe.c','probe'):
                    (p.root/name).write_bytes(b'synthetic-private-payload')
                nested=p.root/'central-apple-public-leaf-interrupted'
                nested.mkdir()
                (nested/'leaf.pem').write_bytes(b'synthetic-public-leaf')
                calls=[]
                failure=failed_operation
                def command(args,**_kwargs):
                    calls.append(args)
                    for name in ('owner.p12','api.p8','probe.c','probe'):
                        self.assertFalse((p.root/name).exists())
                    if args[1]==failure:
                        raise RuntimeError('synthetic-private-failure')
                    if args[1]=='delete-keychain':
                        p.keychain.unlink()
                    return b''
                with patch.object(preflight,'_call',side_effect=command):
                    with self.assertRaisesRegex(RuntimeError,'private keychain cleanup failed'):
                        p.cleanup()
                    self.assertTrue(any(args[1]=='list-keychains' for args in calls))
                    self.assertTrue(any(args[1]=='default-keychain' for args in calls))
                    self.assertTrue(p.state.is_file())
                    expected={p.state.name,p.keychain.name} if failed_operation=='delete-keychain' else {p.state.name}
                    self.assertEqual({path.name for path in p.root.iterdir()},expected)
                    failure=None
                    p.cleanup()
                self.assertFalse(p.root.exists())

    def test_invalid_cleanup_state_still_removes_credentials_without_claiming_restoration(self):
        with tempfile.TemporaryDirectory() as td:
            p=preflight.Preflight(Path(td))
            p._secure_root()
            p.state.write_text('{invalid-state')
            (p.root/'owner.p12').write_bytes(b'synthetic-p12')
            (p.root/'api.p8').write_bytes(b'synthetic-p8')
            with patch.object(preflight,'_call') as command:
                with self.assertRaisesRegex(RuntimeError,'private keychain cleanup failed'):
                    p.cleanup()
            command.assert_not_called()
            self.assertEqual(list(p.root.iterdir()),[p.state])

    def test_cleanup_result_never_overwrites_successful_credential_stages(self):
        with tempfile.TemporaryDirectory() as td:
            p=preflight.Preflight(Path(td))
            def signing(*_args):
                for stage in ('P12_IMPORT','PRIVATE_KEY_PRESENT','CERTIFICATE_READ','CERT_TEAM_MATCH','CERT_TRUST_VALID','CODESIGN','CODESIGN_VERIFY'):
                    p._stage(stage,'PASS')
            def api(*_args):
                p._stage('API_AUTH','PASS')
                p._stage('API_CERT_ACCESS','PASS')
            with (patch.object(preflight,'_credentials',return_value=('ABCDE12345',b'x','synthetic-password',b'x','key','issuer')),
                  patch.object(p,'_p12',side_effect=signing),patch.object(p,'_api',side_effect=api),
                  patch.object(p,'cleanup',side_effect=RuntimeError('private-cleanup-error'))):
                output=io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(p.run(),1)
            self.assertIn('P12_IMPORT=PASS',output.getvalue())
            self.assertIn('CODESIGN_VERIFY=PASS',output.getvalue())
            self.assertIn('CLEANUP=CLEANUP_FAILED',output.getvalue())
            self.assertNotIn('private-cleanup-error',output.getvalue())
            p.cleanup()

    def test_invalid_team_does_not_suppress_independent_api_diagnostic(self):
        with tempfile.TemporaryDirectory() as td:
            p=preflight.Preflight(Path(td))
            credentials=("WRONG",b"\x30"+b"x"*63,"private-password",b"-----BEGIN PRIVATE KEY-----\na\n-----END PRIVATE KEY-----","AAAAA12345","fd8567df-5955-4ae5-bf23-13e023d10a6d")
            with patch.object(preflight,"_credentials",return_value=credentials),patch.object(preflight,"_jwt",return_value="jwt"),patch.object(preflight,"_apple_certificates",return_value=(401,b"")):
                output=io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(p.run(),1)
            self.assertIn("CERT_TEAM_MATCH=MISSING",output.getvalue())
            self.assertIn("API_AUTH=UNAUTHORIZED_401",output.getvalue())
            self.assertFalse((Path(td)/preflight.ROOT_NAME).exists())

    def test_bounded_authentication_and_no_device_admission(self):
        env={"GITHUB_REPOSITORY":"StreamScapeTV/ci-workflows","GITHUB_REF":"refs/heads/main", "GITHUB_EVENT_NAME":"workflow_dispatch","RUNNER_OS":"macOS","RUNNER_ENVIRONMENT":"self-hosted"}
        with tempfile.TemporaryDirectory() as td:
            env["RUNNER_TEMP"]=td
            with patch.dict(preflight.os.environ,env,clear=True):
                self.assertEqual(preflight._admission(),Path(td))
            for key,value in (("GITHUB_EVENT_NAME","pull_request"),("GITHUB_REF","refs/heads/test"),("RUNNER_ENVIRONMENT","github-hosted"),("RUNNER_OS","Linux")):
                changed={**env,key:value}
                with patch.dict(preflight.os.environ,changed,clear=True),self.assertRaises(RuntimeError):
                    preflight._admission()


if __name__=="__main__":
    unittest.main()
