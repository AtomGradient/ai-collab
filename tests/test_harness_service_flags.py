# SPDX-License-Identifier: MIT
# Copyright © 2026 AtomGradient
# 版权所有 © 2026 质子梯度（北京）科技有限公司

"""The embedded Host service must start without the optional adapters.

A build without an embedded integration payload launches the service with no
``--adapter-config`` and no ``--security-adapter-config``. The underlying Host
treats both as optional and answers with typed refusals
(``project.adapter-unavailable`` / ``auth.confirmation-required``), so the
service wrapper must not be the layer that refuses to start.
"""

from __future__ import annotations

import pytest

from ai_collab import service


def test_service_accepts_absent_adapter_and_security_configs() -> None:
    parser = service.build_parser()
    arguments = parser.parse_args(
        ["--participant-driver-config", "/tmp/driver.json"]
    )
    assert arguments.adapter_config is None
    assert arguments.security_adapter_config is None


def test_service_still_requires_the_participant_driver_config() -> None:
    parser = service.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
