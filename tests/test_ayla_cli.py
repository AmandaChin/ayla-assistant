from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class AylaCliInstallTests(unittest.TestCase):
    def test_server_uses_ayla_home_for_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "AylaData"
            env = os.environ.copy()
            env["AYLA_HOME"] = str(data_root)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import server; print(server.VAULT_ROOT)",
                ],
                cwd=REPO_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            self.assertEqual(result.stdout.strip(), str(data_root))

    def test_install_creates_mac_app_cli_and_metadata(self) -> None:
        import ayla_cli

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = root / "Application Support" / "Ayla"
            app_dir = root / "Applications"
            bin_dir = root / "bin"

            result = ayla_cli.install(
                source_root=REPO_ROOT,
                install_root=install_root,
                app_dir=app_dir,
                bin_dir=bin_dir,
                force=True,
            )

            app_path = Path(result["app_path"])
            cli_path = Path(result["cli_path"])
            app_executable = app_path / "Contents" / "MacOS" / "Ayla"
            metadata_path = install_root / "install.json"
            runtime_root = install_root / "app"

            self.assertEqual(app_path, app_dir / "Ayla.app")
            self.assertTrue((app_path / "Contents" / "Info.plist").is_file())
            self.assertTrue(app_executable.is_file())
            self.assertTrue(os.access(app_executable, os.X_OK))
            self.assertNotEqual(app_executable.read_bytes()[:2], b"#!")
            self.assertTrue((app_path / "Contents" / "Resources" / "AylaInstallRoot.txt").is_file())
            self.assertTrue((app_path / "Contents" / "Resources" / "AylaAppIcon.icns").is_file())
            self.assertEqual((app_path / "Contents" / "Resources" / "AylaAppIcon.icns").read_bytes()[:4], b"icns")
            self.assertTrue(cli_path.is_file())
            self.assertTrue(os.access(cli_path, os.X_OK))
            self.assertTrue((runtime_root / "server.py").is_file())
            self.assertTrue((runtime_root / "web" / "index.html").is_file())
            self.assertTrue((runtime_root / "macos" / "AylaClient" / "main.swift").is_file())
            self.assertTrue((runtime_root / "macos" / "AylaClient" / "AppIcon.png").is_file())
            self.assertTrue((runtime_root / "scripts" / "build_macos_client.sh").is_file())
            self.assertTrue((runtime_root / "scripts" / "generate_app_icon.py").is_file())
            self.assertFalse((runtime_root / "agent-vault").exists())

            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["source_root"], str(REPO_ROOT))
            self.assertEqual(metadata["install_root"], str(install_root))
            self.assertEqual(metadata["app_path"], str(app_path))
            self.assertEqual(metadata["cli_path"], str(cli_path))
            self.assertEqual(metadata["app_runtime"], "swift-wkwebview")

    def test_cli_wrapper_runs_status_against_installed_layout(self) -> None:
        import ayla_cli

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = root / "Application Support" / "Ayla"
            app_dir = root / "Applications"
            bin_dir = root / "bin"

            ayla_cli.install(
                source_root=REPO_ROOT,
                install_root=install_root,
                app_dir=app_dir,
                bin_dir=bin_dir,
                force=True,
            )

            result = subprocess.run(
                [
                    str(bin_dir / "ayla"),
                    "status",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["install_root"], str(install_root))
            self.assertEqual(payload["installed"], True)
            self.assertEqual(payload["app_exists"], True)


if __name__ == "__main__":
    unittest.main()
