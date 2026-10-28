from __future__ import annotations

import base64
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
from functools import wraps

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import serialization

from .common import Denied, canonical, digest, exact_schema, opaque, plain_text, strict_json
from .detector import SyntheticAdapter, clinical_check, residual_check
from .fixtures import find_fixture

REQUEST_KEYS = {"text", "document", "purpose", "source", "source_grade", "destination_grade", "recipient", "media_type", "project"}
PAYLOAD_KEYS = {"schema_version", "text", "document", "release", "purpose", "recipient", "policy_version", "policy_digest", "text_sha256", "expires_at"}
POLICY = {
    "version": "synthetic-cds-2026-10-v1", "release_ttl": 120, "review_ttl": 600,
    "deployment": "local-synthetic-only", "purpose": "scientific-research",
    "project": "demo-research", "basis": "synthetic-data-no-real-subjects",
    "sources": {"synthetic-corpus": "S", "demo-hospital": "S"},
    "recipients": {"local-research": {"grade": "O", "local": True, "country": "KR", "approved": True}},
    "clinical_release_enabled": False,
    "clinical_fact_policy": {"numeric_patient_age": "preserve", "exercise_time": "preserve", "family_subject": "preserve", "fatal_outcome": "preserve", "family_event_age": "preserve", "dose_polarity_certainty_site": "preserve", "sex_inference": "forbid", "purpose_specific_loss": []},
}


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _unb64(data):
    try:
        return base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        raise Denied("INTEGRITY_FAILED") from None


def _secure_directory(path: Path):
    # Reject existing path aliases; this is not an OS privilege boundary.
    if path.absolute() != path.resolve():
        raise Denied("PATH_ALIAS_DENIED")
    for item in [path, *path.parents]:
        if item.is_symlink():
            raise Denied("PATH_ALIAS_DENIED")
    path.mkdir(parents=True, exist_ok=True)


def audit_failures(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        try:
            return function(self, *args, **kwargs)
        except Exception as error:
            code = error.code if isinstance(error, Denied) else "INTERNAL_ERROR"
            conn = None
            try:
                conn = self._db()
                conn.execute("BEGIN IMMEDIATE")
                # No caller document, source, token, arbitrary exception or raw ID.
                self._audit(conn, "DENIED", codes=[code])
                conn.commit()
            except Exception:
                # An unavailable/tampered audit store cannot authorize an export.
                pass
            finally:
                if conn:
                    conn.close()
            raise Denied(code) from None
    return wrapped


class Gateway:
    """All decisions and release writes occur here; callers supply no trusted flags.

    State and keys must be accessible only to a dedicated service account in a real
    deployment. Local users with direct disk access can bypass this simulation.
    """
    def __init__(self, root, adapter=None, clock=time.time, *, synthetic_validator=None):
        self.root = Path(root).absolute()
        self.internal = self.root / "internal"
        self.external = self.root / "external"
        self.adapter = adapter or SyntheticAdapter()
        # Trusted server-side extension for authored synthetic LLM demonstrations.
        # No request field can enable or replace this validator.
        self.synthetic_validator = synthetic_validator
        self.clock = clock
        if not (self.internal / "state.sqlite").is_file():
            raise Denied("NOT_INITIALIZED")
        _secure_directory(self.internal)
        _secure_directory(self.external)
        try:
            self.vault_key = (self.internal / "vault.key").read_bytes()
            self.auth_key = (self.internal / "state.key").read_bytes()
            self.signer = Ed25519PrivateKey.from_private_bytes((self.internal / "signing.key").read_bytes())
            if len(self.vault_key) != 32 or len(self.auth_key) != 32:
                raise ValueError()
        except (OSError, ValueError):
            raise Denied("KEY_STATE_FAILED") from None

    @classmethod
    def initialize(cls, root):
        root = Path(root).absolute()
        _secure_directory(root)
        if any(root.iterdir()):
            raise Denied("STATE_EXISTS")
        internal, external = root / "internal", root / "external"
        _secure_directory(internal)
        _secure_directory(external)
        vault_key, auth_key = secrets.token_bytes(32), secrets.token_bytes(32)
        signer = Ed25519PrivateKey.generate()
        for name, data in {"vault.key": vault_key, "state.key": auth_key,
                           "signing.key": signer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())}.items():
            with (internal / name).open("xb") as file:
                file.write(data)
            os.chmod(internal / name, 0o600)
        public_key = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        (external / "verification.key").write_bytes(public_key)
        conn = sqlite3.connect(internal / "state.sqlite")
        conn.executescript("""
            CREATE TABLE policy (id INTEGER PRIMARY KEY CHECK(id=1), body BLOB, mac TEXT);
            CREATE TABLE identities (token_hash TEXT PRIMARY KEY, subject TEXT, role TEXT, active INTEGER);
            CREATE TABLE releases (id TEXT PRIMARY KEY, document TEXT, state TEXT, record BLOB, mac TEXT);
            CREATE TABLE documents (id TEXT PRIMARY KEY, current_release TEXT);
            CREATE TABLE audit (sequence INTEGER PRIMARY KEY, body BLOB, mac TEXT);
        """)
        body = canonical(POLICY)
        conn.execute("INSERT INTO policy VALUES (1,?,?)", (body, hmac.digest(auth_key, b"policy\0" + body, "sha256").hex()))
        for role in ["submitter", "reviewer", "exporter"]:
            token = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO identities VALUES (?,?,?,1)", (digest(token), role + "-demo", role))
            (internal / (role + ".token")).write_text(token, encoding="ascii")
            os.chmod(internal / (role + ".token"), 0o600)
        conn.commit()
        conn.close()
        return cls(root)

    def _db(self):
        conn = sqlite3.connect(self.internal / "state.sqlite", timeout=20, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _mac(self, kind, body):
        return hmac.digest(self.auth_key, kind.encode() + b"\0" + body, "sha256").hex()

    def _policy(self, conn):
        row = conn.execute("SELECT body,mac FROM policy WHERE id=1").fetchone()
        if not row or not hmac.compare_digest(row["mac"], self._mac("policy", row["body"])):
            raise Denied("POLICY_INTEGRITY_FAILED")
        policy = strict_json(row["body"])
        # Unsupported policy shape/version fails closed; only trusted operator changes it.
        exact_schema(policy, set(POLICY))
        if policy["deployment"] != "local-synthetic-only" or policy["clinical_release_enabled"] is not False:
            raise Denied("UNSUPPORTED_POLICY")
        return policy

    def _authenticate(self, conn, token, role):
        if type(token) is not str or len(token) > 128:
            raise Denied("AUTH_DENIED")
        row = conn.execute("SELECT subject,role,active FROM identities WHERE token_hash=?", (digest(token),)).fetchone()
        if not row or row["active"] != 1 or row["role"] != role:
            raise Denied("AUTH_DENIED")
        return row["subject"]

    def _audit(self, conn, event, release=None, codes=()):
        previous = conn.execute("SELECT sequence,mac FROM audit ORDER BY sequence DESC LIMIT 1").fetchone()
        seq = previous["sequence"] + 1 if previous else 1
        body = canonical({"sequence": seq, "event": event, "release": release,
                          "codes": list(codes), "time": int(self.clock()), "previous": previous["mac"] if previous else "0" * 64})
        conn.execute("INSERT INTO audit VALUES (?,?,?)", (seq, body, self._mac("audit", body)))

    def _seal(self, value, release):
        nonce = secrets.token_bytes(12)
        return {"nonce": _b64(nonce), "ciphertext": _b64(AESGCM(self.vault_key).encrypt(nonce, canonical(value), release.encode()))}

    def _open(self, sealed, release):
        try:
            return strict_json(AESGCM(self.vault_key).decrypt(_unb64(sealed["nonce"]), _unb64(sealed["ciphertext"]), release.encode()), max_bytes=400000)
        except Exception:
            raise Denied("CAPSULE_INTEGRITY_FAILED") from None

    def _save(self, conn, release, doc, state, record):
        body = canonical(record)
        mac = self._mac("record:" + release + ":" + doc + ":" + state, body)
        conn.execute("INSERT OR REPLACE INTO releases VALUES (?,?,?,?,?)", (release, doc, state, body, mac))

    def _load(self, conn, release):
        opaque(release)
        row = conn.execute("SELECT * FROM releases WHERE id=?", (release,)).fetchone()
        if not row:
            raise Denied("RELEASE_UNKNOWN")
        if not hmac.compare_digest(row["mac"], self._mac("record:" + release + ":" + row["document"] + ":" + row["state"], row["record"])):
            raise Denied("RECORD_INTEGRITY_FAILED")
        return row, strict_json(row["record"])

    def _policy_decision(self, request, policy):
        codes = []
        if request["media_type"] != "text/plain":
            codes.append("FORMAT_DENIED")
        if request["source"] not in policy["sources"] or request["source_grade"] != policy["sources"].get(request["source"]):
            codes.append("SOURCE_GRADE_DENIED")
        if request["purpose"] != policy["purpose"] or request["project"] != policy["project"] or not policy["basis"]:
            codes.append("LEGAL_BASIS_MISSING")
        recipient = policy["recipients"].get(request["recipient"], {})
        if recipient.get("approved") is not True or recipient.get("local") is not True or recipient.get("country") != "KR" or request["destination_grade"] != recipient.get("grade"):
            codes.append("RECIPIENT_DENIED")
        return codes

    def _registered_synthetic(self, request):
        if request["source"] != "synthetic-corpus":
            return False
        if self.synthetic_validator is not None:
            return self.synthetic_validator.is_registered(request["text"])
        return find_fixture(request["text"])[1] is not None

    def _clinical_validation(self, request, text):
        if self.synthetic_validator is not None:
            return self.synthetic_validator.validate(request, text)
        return clinical_check(request["text"], text)

    def _residual_validation(self, request, text):
        codes = residual_check(request["text"], text)
        date_permission = getattr(self.synthetic_validator, "permits_clinical_dates", None)
        if "RESIDUAL_DATE" in codes and date_permission is not None and date_permission(request, text):
            # Only the bound, validated clinical-date finding may be resolved.
            # Names, contacts, IDs and every other finding remain unchanged.
            return tuple(code for code in codes if code != "RESIDUAL_DATE")
        return codes

    @audit_failures
    def submit(self, request, token):
        exact_schema(request, REQUEST_KEYS)
        plain_text(request["text"])
        for key in REQUEST_KEYS - {"text", "media_type", "source_grade", "destination_grade"}:
            opaque(request[key])
        for key in ["media_type", "source_grade", "destination_grade"]:
            if type(request[key]) is not str or len(request[key]) > 64:
                raise Denied("SCHEMA_DENIED")
        conn = self._db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            subject = self._authenticate(conn, token, "submitter")
            policy = self._policy(conn)
            codes = self._policy_decision(request, policy)
            release = uuid.uuid4().hex
            doc = self._mac("document", canonical([request["source"], request["document"]]))[:32]
            state = "BLOCK" if codes else "REVIEW"
            candidate = None
            if not codes:
                try:
                    if hasattr(self.adapter, "transform_bound"):
                        context = {"project": request["project"], "document": doc,
                                   "release": release, "purpose": "research"}
                        candidate = self.adapter.transform_bound(request["text"], self.vault_key, context)
                    else:
                        candidate = self.adapter.transform(request["text"])
                    residual = self._residual_validation(request, candidate.text)
                    clinical = self._clinical_validation(request, candidate.text)
                    codes.extend(residual)
                    codes.extend(clinical)
                    fixture_id, fixture = find_fixture(request["text"])
                    trusted_synthetic = self._registered_synthetic(request)
                    if not trusted_synthetic or not candidate.supported:
                        codes.append("REAL_TEXT_REQUIRES_INSTITUTIONAL_REVIEW")
                    if residual or (clinical and (fixture or self.synthetic_validator is not None)):
                        state = "BLOCK"
                    elif not codes:
                        codes.append("HUMAN_APPROVAL_REQUIRED")
                except Exception:
                    state, codes = "BLOCK", ["TRANSFORM_OR_VALIDATION_FAILED"]
                    candidate = None
            record = {
                "submitted_by": subject, "created": int(self.clock()), "policy": digest(canonical(policy)),
                "codes": sorted(set(codes)), "capsule": self._seal({"request": request,
                    "candidate": candidate.text if candidate else None,
                    "mapping": list(candidate.mapping) if candidate else [],
                    "adapter": candidate.adapter if candidate else None,
                    "deid_result": candidate.internal_result if candidate else None}, release),
                "manifest": None, "approval": None,
            }
            self._save(conn, release, doc, state, record)
            conn.execute("INSERT OR REPLACE INTO documents VALUES (?,?)", (doc, release))
            self._audit(conn, "SUBMITTED", release, record["codes"])
            conn.commit()
            return {"release": release, "state": state, "codes": record["codes"]}
        finally:
            conn.close()

    def _revalidate(self, conn, row, record):
        policy = self._policy(conn)
        if digest(canonical(policy)) != record["policy"]:
            raise Denied("POLICY_CHANGED")
        current = conn.execute("SELECT current_release FROM documents WHERE id=?", (row["document"],)).fetchone()
        if not current or current[0] != row["id"]:
            raise Denied("SUPERSEDED_RELEASE")
        private = self._open(record["capsule"], row["id"])
        request, text = private["request"], private["candidate"]
        exact_schema(request, REQUEST_KEYS)
        plain_text(request["text"])
        expected_doc = self._mac("document", canonical([request["source"], request["document"]]))[:32]
        if expected_doc != row["document"]:
            raise Denied("DOCUMENT_BINDING_FAILED")
        if self._policy_decision(request, policy):
            raise Denied("POLICY_DENIED")
        fixture_id, fixture = find_fixture(request["text"])
        if not self._registered_synthetic(request):
            raise Denied("REAL_TEXT_RELEASE_DISABLED")
        if self._residual_validation(request, text):
            raise Denied("RESIDUAL_PHI")
        if self._clinical_validation(request, text):
            raise Denied("CLINICAL_FACT_DAMAGED")
        return policy, request, text

    @audit_failures
    def approve(self, release, token, expected_text_sha256):
        conn = self._db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            subject = self._authenticate(conn, token, "reviewer")
            row, record = self._load(conn, release)
            if row["state"] != "REVIEW":
                raise Denied("NOT_REVIEWABLE")
            if subject == record["submitted_by"]:
                raise Denied("SEPARATION_OF_DUTIES")
            policy, request, text = self._revalidate(conn, row, record)
            if self.clock() > record["created"] + policy["review_ttl"]:
                raise Denied("REVIEW_EXPIRED")
            if type(expected_text_sha256) is not str or not hmac.compare_digest(expected_text_sha256, digest(text)):
                raise Denied("REVIEW_CONTENT_MISMATCH")
            manifest = {"schema_version": "clinical-cds-public/v1", "text": text,
                        "document": row["document"], "release": release, "purpose": request["purpose"],
                        "recipient": request["recipient"], "policy_version": policy["version"],
                        "policy_digest": record["policy"], "text_sha256": digest(text),
                        "expires_at": int(self.clock()) + policy["release_ttl"]}
            exact_schema(manifest, PAYLOAD_KEYS)
            record["manifest"] = self._seal(manifest, release + ":manifest")
            record["approval"] = {"by": subject, "hash": digest(canonical(manifest)), "time": int(self.clock())}
            private = self._open(record["capsule"], release)
            record["approval"]["request_hash"] = digest(canonical(request))
            record["approval"]["adapter"] = private["adapter"]
            self._save(conn, release, row["document"], "APPROVED", record)
            self._audit(conn, "APPROVED", release)
            conn.commit()
            return {"release": release, "state": "APPROVED", "manifest_sha256": record["approval"]["hash"]}
        finally:
            conn.close()

    @audit_failures
    def inspect(self, release, token):
        """Internal-only review API. Do not place review response in egress/logs."""
        conn = self._db()
        try:
            self._authenticate(conn, token, "reviewer")
            row, record = self._load(conn, release)
            private = self._open(record["capsule"], release)
            return {"release": release, "state": row["state"], "codes": record["codes"],
                    "candidate": private["candidate"], "text_sha256": digest(private["candidate"] or "")}
        finally:
            conn.close()

    @audit_failures
    def export(self, release, token):
        conn = self._db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._authenticate(conn, token, "exporter")
            row, record = self._load(conn, release)
            if row["state"] != "APPROVED":
                raise Denied("NOT_APPROVED_OR_CONSUMED")
            policy, request, text = self._revalidate(conn, row, record)
            manifest = self._open(record["manifest"], release + ":manifest")
            exact_schema(manifest, PAYLOAD_KEYS)
            if manifest["expires_at"] < self.clock():
                raise Denied("APPROVAL_EXPIRED")
            if (manifest["text"] != text or manifest["text_sha256"] != digest(text) or
                manifest["document"] != row["document"] or manifest["release"] != release or
                manifest["recipient"] != request["recipient"] or manifest["purpose"] != request["purpose"] or
                manifest["policy_version"] != policy["version"] or manifest["policy_digest"] != record["policy"] or
                digest(canonical(manifest)) != record["approval"]["hash"]):
                raise Denied("APPROVAL_BINDING_FAILED")
            private = self._open(record["capsule"], release)
            if digest(canonical(request)) != record["approval"]["request_hash"] or private["adapter"] != record["approval"]["adapter"]:
                raise Denied("SOURCE_OR_TRANSFORM_CHANGED")
            envelope = {"payload": manifest, "signature": _b64(self.signer.sign(canonical(manifest)))}
            # Claim before file I/O: crash can lose one release, never authorize replay.
            self._save(conn, release, row["document"], "CONSUMED", record)
            self._audit(conn, "CONSUMED", release)
            conn.commit()
            _secure_directory(self.external)
            path = self.external / (release + ".json")
            # The full validated bytes are held locally; no reread of caller files.
            temp = self.external / ("." + release + ".pending")
            try:
                with temp.open("xb") as file:
                    file.write(canonical(envelope))
                    file.flush()
                    os.fsync(file.fileno())
                # Hard link creates a complete file exclusively; no overwrite allowed.
                os.link(temp, path)
                temp.unlink()
            except OSError:
                if temp.exists() and not temp.is_symlink():
                    temp.unlink()
                raise Denied("EGRESS_WRITE_FAILED_CONSUMED") from None
            conn.execute("BEGIN IMMEDIATE")
            self._audit(conn, "EXPORTED", release)
            conn.commit()
            return {"release": release, "state": "EXPORTED", "file": str(path)}
        finally:
            conn.close()

    def audit_export(self, token):
        conn = self._db()
        try:
            self._authenticate(conn, token, "reviewer")
            result, previous = [], "0" * 64
            for index, row in enumerate(conn.execute("SELECT * FROM audit ORDER BY sequence"), 1):
                if not hmac.compare_digest(row["mac"], self._mac("audit", row["body"])):
                    raise Denied("AUDIT_INTEGRITY_FAILED")
                item = strict_json(row["body"])
                if item["sequence"] != index or item["previous"] != previous:
                    raise Denied("AUDIT_CHAIN_FAILED")
                previous = row["mac"]
                result.append({k: item[k] for k in ["sequence", "event", "release", "codes", "time"]})
            return {"schema_version": "clinical-cds-audit/v1", "events": result}
        finally:
            conn.close()

    @audit_failures
    def revoke(self, release, token):
        conn = self._db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._authenticate(conn, token, "reviewer")
            row, record = self._load(conn, release)
            if row["state"] not in {"REVIEW", "APPROVED"}:
                raise Denied("NOT_REVOCABLE")
            self._save(conn, release, row["document"], "REVOKED", record)
            self._audit(conn, "REVOKED", release)
            conn.commit()
            return {"release": release, "state": "REVOKED"}
        finally:
            conn.close()

    @audit_failures
    def restore_internal(self, release, token):
        """Internal original retrieval; intentionally not a public CLI stdout route."""
        conn = self._db()
        try:
            self._authenticate(conn, token, "reviewer")
            _, record = self._load(conn, release)
            return self._open(record["capsule"], release)["request"]["text"].encode("utf-8")
        finally:
            conn.close()


def verify_external(envelope, public_key: bytes, now=None, expected_recipient="local-research", expected_policy_digest=None):
    """Authenticity only; receiver must persist consumed release IDs for replay defense."""
    exact_schema(envelope, {"payload", "signature"})
    manifest = envelope["payload"]
    exact_schema(manifest, PAYLOAD_KEYS)
    if manifest["schema_version"] != "clinical-cds-public/v1" or manifest["purpose"] != "scientific-research":
        raise Denied("EXTERNAL_SCHEMA_FAILED")
    if manifest["recipient"] != expected_recipient or (expected_policy_digest is not None and manifest["policy_digest"] != expected_policy_digest):
        raise Denied("EXTERNAL_SCOPE_FAILED")
    for field in ["document", "release", "recipient", "policy_version"]:
        opaque(manifest[field])
    if type(manifest["expires_at"]) is not int:
        raise Denied("EXTERNAL_SCHEMA_FAILED")
    plain_text(manifest["text"])
    if digest(manifest["text"]) != manifest["text_sha256"]:
        raise Denied("EXTERNAL_HASH_FAILED")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(_unb64(envelope["signature"]), canonical(manifest))
    except Exception:
        raise Denied("EXTERNAL_SIGNATURE_FAILED") from None
    if manifest["expires_at"] < (time.time() if now is None else now):
        raise Denied("EXTERNAL_EXPIRED")
    return manifest
