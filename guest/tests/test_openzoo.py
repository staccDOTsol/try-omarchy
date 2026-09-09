#!/usr/bin/env python3
"""Contracts for the openzoo boot seed.

The factory package set is locked, so openzoo arrives through a root oneshot,
never through packages.txt. That oneshot runs on every online boot and installs
`openzoo@latest`, so an image installed months ago upgrades itself instead of
freezing at the version npm served on its first boot. Every user session then
gets a local x402 proxy and a Codex provider that points OpenAI's ChatGPT
desktop app at it. These tests pin the pieces that make that true without
booting anything.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
import unittest


GUEST = Path(__file__).resolve().parents[1]
NATIVE = GUEST / "native-overlay"
FACTORY = GUEST / "factory-overlay"

BOOTSTRAP = NATIVE / "usr/local/lib/try-omarchy/openzoo-bootstrap"
CHATGPT = NATIVE / "usr/local/bin/try-omarchy-chatgpt"
BOOTSTRAP_UNIT = NATIVE / "usr/lib/systemd/system/try-omarchy-openzoo-bootstrap.service"
PROXY_UNIT = NATIVE / "usr/lib/systemd/user/openzoo-proxy.service"
FIRST_LAUNCH = NATIVE / "usr/local/lib/try-omarchy/openzoo-first-launch"
FIRST_LAUNCH_UNIT = NATIVE / "usr/lib/systemd/user/openzoo-first-launch.service"
SEED = NATIVE / "etc/skel/.codex/config.toml"
ENV = FACTORY / "usr/lib/environment.d/91-openzoo.conf"
CONFIGURE = GUEST / "scripts/configure-rootfs.sh"


def unit_fields(text: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith(("#", "[")):
            key, value = line.split("=", 1)
            fields.setdefault(key.strip(), []).append(value.strip())
    return fields


class OpenzooSeedTests(unittest.TestCase):
    def test_scripts_are_executable_bash(self) -> None:
        for script in (BOOTSTRAP, CHATGPT, FIRST_LAUNCH):
            self.assertTrue(os.access(script, os.X_OK), script)
            self.assertTrue(script.read_text(encoding="utf-8").startswith("#!/bin/bash\n"), script)

    def test_bootstrap_leaves_the_package_lock_alone(self) -> None:
        packages = (GUEST / "packages.txt").read_text(encoding="utf-8").split()
        self.assertNotIn("nodejs", packages)
        self.assertNotIn("npm", packages)
        text = BOOTSTRAP.read_text(encoding="utf-8")
        self.assertIn("pacman -Sy --needed --noconfirm nodejs npm", text)
        self.assertIn("npm install -g", text)
        self.assertIn("/var/lib/try-omarchy/openzoo-bootstrapped", text)

    def test_bootstrap_upgrades_openzoo_on_every_boot(self) -> None:
        text = BOOTSTRAP.read_text(encoding="utf-8")
        # @latest is what turns the boot-time install into an upgrade: npm
        # replaces an older global openzoo and no-ops when it is current.
        self.assertIn("openzoo@latest", text)
        # The stamp records that the CLI is present; it must not gate the run,
        # or the guest freezes at whatever npm served on the first boot.
        self.assertNotRegex(text, r"(?m)^\[\[ -e \$stamp \]\] && exit 0$")
        # A hung registry must not hold the boot open on its own.
        self.assertRegex(text, r"timeout \S+ npm install")
        # An upgrade that cannot reach the registry keeps the version already
        # installed instead of failing the boot.
        self.assertIn("had_openzoo", text)

    def test_bootstrap_unit_runs_on_every_online_boot(self) -> None:
        fields = unit_fields(BOOTSTRAP_UNIT.read_text(encoding="utf-8"))
        self.assertEqual(fields["Type"], ["oneshot"])
        self.assertEqual(fields["ExecStart"], ["/usr/local/lib/try-omarchy/openzoo-bootstrap"])
        self.assertIn("network-online.target", fields["After"][0])
        self.assertIn("network-online.target", fields["Wants"][0])
        self.assertEqual(fields["TimeoutStartSec"], ["15min"])
        # No stamp condition: the unit must run again on later boots so the
        # openzoo CLI picks up fixes released after the image was installed.
        self.assertNotIn("ConditionPathExists", fields)
        self.assertEqual(fields["WantedBy"], ["multi-user.target"])

    def test_proxy_unit_is_localhost_only_and_waits_for_bootstrap(self) -> None:
        fields = unit_fields(PROXY_UNIT.read_text(encoding="utf-8"))
        self.assertEqual(fields["ExecStart"], ["/usr/bin/openzoo proxy"])
        self.assertIn("OPENZOO_NO_TUNNEL=1", fields["Environment"])
        self.assertIn("/usr/bin/openzoo", fields["ExecStartPre"][0])
        self.assertEqual(fields["WantedBy"], ["default.target"])
        self.assertEqual(fields["Restart"], ["on-failure"])

    def test_seeded_codex_config_points_at_the_local_proxy(self) -> None:
        text = SEED.read_text(encoding="utf-8")
        self.assertRegex(text, r'(?m)^model_provider = "openzoo"$')
        self.assertIn("[model_providers.openzoo]", text)
        self.assertIn('base_url = "http://localhost:8402/v1"', text)
        self.assertIn('env_key = "OPENZOO_API_KEY"', text)
        self.assertIn('wire_api = "responses"', text)
        # top-level keys must precede the first table — TOML requires it
        self.assertLess(text.index("model_provider"), text.index("["))
        env = ENV.read_text(encoding="utf-8")
        self.assertRegex(env, r"(?m)^OPENZOO_API_KEY=\S+$")

    def test_rootfs_configuration_wires_the_units(self) -> None:
        text = CONFIGURE.read_text(encoding="utf-8")
        self.assertIn("multi-user.target.wants/try-omarchy-openzoo-bootstrap.service", text)
        self.assertIn("default.target.wants/openzoo-proxy.service", text)
        self.assertIn('"$root/usr/local/lib/try-omarchy/openzoo-bootstrap"', text)
        self.assertIn('"$root/usr/local/bin/try-omarchy-chatgpt"', text)
        self.assertIn('"$root/usr/local/lib/try-omarchy/openzoo-first-launch"', text)
        self.assertIn("graphical-session.target.wants/openzoo-first-launch.service", text)

    def test_first_launch_runs_the_omarchymax_one_liner_once(self) -> None:
        text = FIRST_LAUNCH.read_text(encoding="utf-8")
        self.assertIn("https://openzoo.fun/omarchymax", text)
        self.assertIn('stamp="$HOME/.openzoo/omarchymax-done"', text)
        fields = unit_fields(FIRST_LAUNCH_UNIT.read_text(encoding="utf-8"))
        self.assertEqual(fields["Type"], ["oneshot"])
        self.assertEqual(fields["ExecStart"], ["/usr/local/lib/try-omarchy/openzoo-first-launch"])
        self.assertEqual(fields["ConditionPathExists"], ["!%h/.openzoo/omarchymax-done"])
        self.assertEqual(fields["WantedBy"], ["graphical-session.target"])

    def test_chatgpt_helper_never_ships_the_app(self) -> None:
        text = CHATGPT.read_text(encoding="utf-8")
        self.assertIn("https://chatgpt.com/download", text)
        self.assertIn("openzoo chatgpt", text)
        self.assertFalse(re.search(r"\.deb\s+http", text), "no baked-in download URL")


if __name__ == "__main__":
    unittest.main()
