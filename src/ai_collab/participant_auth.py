# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""Owner-private participant credentials for Agent-originated Host intents."""

from __future__ import annotations

import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Any, Mapping

from .protocol import CONTRACT_VERSION, canonical_json_bytes, canonical_json_sha256


CONTEXT_SCHEMA_VERSION = 1
COLLABORATION_CONTEXT_SCHEMA_VERSION = 1


class ParticipantAuthError(ValueError):
    pass


class ParticipantAuthStore:
    """Issue one scoped credential per exact Participant generation.

    The credential is only one half of sender authentication.  Host also asks
    the participant driver to prove that the Unix peer PID belongs to the
    bound Harness-owned descendant process chain.
    """

    def __init__(self, state_root: Path, socket_path: Path):
        self.state_root = Path(state_root).resolve()
        self.socket_path = Path(socket_path).resolve()
        self.root = self.state_root / "participant-contexts"
        self.collaboration_root = self.state_root / "participant-collaboration"
        self._ensure_root()

    @staticmethod
    def _identity(
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        participant_generation: int,
    ) -> dict[str, Any]:
        return {
            "project_instance_id": project_instance_id,
            "scenario_id": scenario_id,
            "participant_id": participant_id,
            "participant_generation": participant_generation,
        }

    def _path(
        self,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        participant_generation: int,
    ) -> Path:
        identity = self._identity(
            project_instance_id,
            scenario_id,
            participant_id,
            participant_generation,
        )
        return self.root / f"participant-{canonical_json_sha256(identity)}.json"

    def _collaboration_path(
        self,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        participant_generation: int,
    ) -> Path:
        identity = self._identity(
            project_instance_id,
            scenario_id,
            participant_id,
            participant_generation,
        )
        return self.collaboration_root / (
            f"participant-{canonical_json_sha256(identity)}.json"
        )

    def ensure(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        participant_generation: int,
        participant_state_revision: int,
        collaboration_context: Mapping[str, Any] | None = None,
    ) -> dict[str, str]:
        """Return stable launch material, creating it atomically when absent."""

        if (
            not isinstance(participant_state_revision, int)
            or isinstance(participant_state_revision, bool)
            or participant_state_revision < 1
        ):
            raise ParticipantAuthError("participant state revision is invalid")
        identity = self._identity(
            project_instance_id,
            scenario_id,
            participant_id,
            participant_generation,
        )
        if collaboration_context is None:
            unsigned: dict[str, Any] = {
                "schema_version": COLLABORATION_CONTEXT_SCHEMA_VERSION,
                "context_revision": participant_state_revision,
                "scenario": {
                    "project_instance_id": project_instance_id,
                    "scenario_id": scenario_id,
                    "scenario_generation": 1,
                },
                "participant": {
                    "participant_id": participant_id,
                    "participant_generation": participant_generation,
                    "assignments": [],
                },
                "peers": [],
                "policy": None,
                "allowed_outbound": [],
                "reply_semantics": {
                    "reply_expected_kinds": [],
                    "terminal_kinds": [],
                    "preserve_reply_to": True,
                    "machine_ack_is_silent": True,
                },
            }
            collaboration_context = {
                **unsigned,
                "context_digest": canonical_json_sha256(unsigned),
            }
        path = self._path(**identity)
        collaboration_path = self._collaboration_path(**identity)
        collaboration_context = self._version_collaboration(
            collaboration_path, identity, collaboration_context
        )
        if path.exists() or path.is_symlink():
            value = self.read(identity)
            if value["participant_state_revision"] == participant_state_revision:
                self._write_collaboration(
                    collaboration_path, identity, collaboration_context
                )
                return self._launch_material(path, collaboration_path)
            value["participant_state_revision"] = participant_state_revision
        else:
            value = {
                "schema_version": CONTEXT_SCHEMA_VERSION,
                "contract_version": CONTRACT_VERSION,
                **identity,
                "participant_state_revision": participant_state_revision,
                "host_socket_path": str(self.socket_path),
                "participant_capability": secrets.token_hex(32),
            }
        self._write(path, value)
        self._write_collaboration(collaboration_path, identity, collaboration_context)
        return self._launch_material(path, collaboration_path)

    def _launch_material(
        self, path: Path, collaboration_path: Path
    ) -> dict[str, str]:
        return {
            "context_path": str(path),
            "client_executable": str(Path(sys.executable).resolve(strict=True)),
            "client_pythonpath": str(Path(__file__).resolve().parents[1]),
            "collaboration_context_path": str(collaboration_path),
        }

    def _write_collaboration(
        self,
        path: Path,
        identity: Mapping[str, Any],
        collaboration_context: Mapping[str, Any],
    ) -> None:
        value = self._validate_collaboration(identity, collaboration_context)
        self._write(path, value)

    def _version_collaboration(
        self,
        path: Path,
        identity: Mapping[str, Any],
        collaboration_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        value = self._validate_collaboration(identity, collaboration_context)
        semantic = {
            key: item
            for key, item in value.items()
            if key not in {"context_revision", "context_digest"}
        }
        revision = 1
        if path.exists() or path.is_symlink():
            previous = self._read_collaboration(path, identity)
            previous_semantic = {
                key: item
                for key, item in previous.items()
                if key not in {"context_revision", "context_digest"}
            }
            revision = previous["context_revision"]
            if semantic != previous_semantic:
                revision += 1
        value["context_revision"] = revision
        unsigned = {
            key: item for key, item in value.items() if key != "context_digest"
        }
        value["context_digest"] = canonical_json_sha256(unsigned)
        return value

    def _read_collaboration(
        self, path: Path, identity: Mapping[str, Any]
    ) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise ParticipantAuthError(
                "participant collaboration context is unavailable"
            )
        details = path.stat()
        if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o600:
            raise ParticipantAuthError(
                "participant collaboration context permissions differ"
            )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ParticipantAuthError(
                "participant collaboration context is invalid"
            ) from exc
        return self._validate_collaboration(identity, value)

    @staticmethod
    def _validate_collaboration(
        identity: Mapping[str, Any],
        collaboration_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        value = dict(collaboration_context)
        if (
            set(value)
            != {
                "schema_version",
                "context_revision",
                "context_digest",
                "scenario",
                "participant",
                "peers",
                "policy",
                "allowed_outbound",
                "reply_semantics",
            }
            or value["schema_version"] != COLLABORATION_CONTEXT_SCHEMA_VERSION
            or not isinstance(value["context_revision"], int)
            or isinstance(value["context_revision"], bool)
            or value["context_revision"] < 1
            or value.get("scenario", {}).get("project_instance_id")
            != identity["project_instance_id"]
            or value.get("scenario", {}).get("scenario_id")
            != identity["scenario_id"]
            or value.get("participant", {}).get("participant_id")
            != identity["participant_id"]
            or value.get("participant", {}).get("participant_generation")
            != identity["participant_generation"]
        ):
            raise ParticipantAuthError("participant collaboration context differs")
        unsigned = {
            key: item for key, item in value.items() if key != "context_digest"
        }
        if value["context_digest"] != canonical_json_sha256(unsigned):
            raise ParticipantAuthError("participant collaboration digest differs")
        return value

    def _write(self, path: Path, value: Mapping[str, Any]) -> None:
        temporary = path.parent / (
            f".context.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        )
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(canonical_json_bytes(value) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            self._fsync_root()
        finally:
            if temporary.exists():
                temporary.unlink()

    def read(self, identity: Mapping[str, Any]) -> dict[str, Any]:
        path = self._path(
            project_instance_id=identity["project_instance_id"],
            scenario_id=identity["scenario_id"],
            participant_id=identity["participant_id"],
            participant_generation=identity["participant_generation"],
        )
        if path.is_symlink() or not path.is_file():
            raise ParticipantAuthError("participant context is unavailable")
        details = path.stat()
        if details.st_uid != os.getuid() or stat.S_IMODE(details.st_mode) != 0o600:
            raise ParticipantAuthError("participant context permissions differ")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ParticipantAuthError("participant context is invalid") from exc
        fields = {
            "schema_version",
            "contract_version",
            "project_instance_id",
            "scenario_id",
            "participant_id",
            "participant_generation",
            "participant_state_revision",
            "host_socket_path",
            "participant_capability",
        }
        expected_identity = self._identity(
            identity["project_instance_id"],
            identity["scenario_id"],
            identity["participant_id"],
            identity["participant_generation"],
        )
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or value["schema_version"] != CONTEXT_SCHEMA_VERSION
            or value["contract_version"] != CONTRACT_VERSION
            or any(value[key] != item for key, item in expected_identity.items())
            or not isinstance(value["participant_state_revision"], int)
            or isinstance(value["participant_state_revision"], bool)
            or value["participant_state_revision"] < 1
            or value["host_socket_path"] != str(self.socket_path)
            or not isinstance(value["participant_capability"], str)
            or len(value["participant_capability"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in value["participant_capability"]
            )
        ):
            raise ParticipantAuthError("participant context binding differs")
        return value

    def revoke(
        self,
        *,
        project_instance_id: str,
        scenario_id: str,
        participant_id: str,
        participant_generation: int,
    ) -> None:
        path = self._path(
            project_instance_id,
            scenario_id,
            participant_id,
            participant_generation,
        )
        if path.is_symlink():
            raise ParticipantAuthError("participant context path is unsafe")
        path.unlink(missing_ok=True)
        collaboration_path = self._collaboration_path(
            project_instance_id,
            scenario_id,
            participant_id,
            participant_generation,
        )
        if collaboration_path.is_symlink():
            raise ParticipantAuthError("participant collaboration path is unsafe")
        collaboration_path.unlink(missing_ok=True)
        self._fsync_root()

    def revoke_scenario(self, project_instance_id: str, scenario_id: str) -> None:
        for path in self.root.glob("participant-*.json"):
            if path.is_symlink() or not path.is_file():
                raise ParticipantAuthError("participant context path is unsafe")
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ParticipantAuthError("participant context is invalid") from exc
            if (
                value.get("project_instance_id") == project_instance_id
                and value.get("scenario_id") == scenario_id
            ):
                path.unlink()
        for path in self.collaboration_root.glob("participant-*.json"):
            if path.is_symlink() or not path.is_file():
                raise ParticipantAuthError(
                    "participant collaboration path is unsafe"
                )
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ParticipantAuthError(
                    "participant collaboration context is invalid"
                ) from exc
            if (
                value.get("scenario", {}).get("project_instance_id")
                == project_instance_id
                and value.get("scenario", {}).get("scenario_id") == scenario_id
                and value.get("participant", {}).get("participant_id")
            ):
                path.unlink()
        self._fsync_root()

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.collaboration_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        for root in (self.root, self.collaboration_root):
            details = root.stat()
            if (
                root.is_symlink()
                or details.st_uid != os.getuid()
                or stat.S_IMODE(details.st_mode) != 0o700
            ):
                raise ParticipantAuthError("participant context root differs")

    def _fsync_root(self) -> None:
        for root in (self.root, self.collaboration_root):
            descriptor = os.open(root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
