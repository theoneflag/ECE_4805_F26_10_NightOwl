import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

BASE_STATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_STATION))

import install_autostart as autostart


class AutostartTests(unittest.TestCase):
    def test_service_preserves_venv_python_symlink_and_stable_usb_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = root / "camera venv" / "bin" / "python3"
            python.parent.mkdir(parents=True)
            python.symlink_to(sys.executable)
            unit = autostart.render_service(root / "src", python, "nightowl", "nightowl",
                                            "/dev/serial/by-id/JSON", "/dev/serial/by-id/PIXHAWK")
            self.assertIn(f'ExecStart="{python}"', unit)
            self.assertIn(f"WorkingDirectory={root / 'src'}", unit)
            self.assertIn('"/dev/serial/by-id/JSON"', unit)
            self.assertIn('"/dev/serial/by-id/PIXHAWK"', unit)
            self.assertIn("KillMode=control-group", unit)
            self.assertIn("Restart=on-failure", unit)
            self.assertNotIn("network-online.target", unit)

    def test_service_environment_only_includes_camera_library_settings(self):
        unit = autostart.render_service("/home/nightowl/Documents/src", "/venv/bin/python3",
                                        "nightowl", "nightowl", "/dev/JSON", "/dev/GPS",
                                        library_environment={"LD_LIBRARY_PATH": "/hailo/lib", "SECRET": "private"})
        self.assertIn('Environment="LD_LIBRARY_PATH=/hailo/lib"', unit)
        self.assertNotIn("SECRET", unit)
        self.assertNotIn("private", unit)

    def test_usb_port_number_is_replaced_with_matching_stable_identifier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / "ttyUSB0"
            device.touch()
            by_id = root / "by-id"
            by_id.mkdir()
            stable = by_id / "usb-FTDI-JSON-radio"
            stable.symlink_to(device)
            self.assertEqual(autostart.stable_serial_path(device, by_id), str(stable))

    def test_existing_service_is_backed_up_before_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "installed.service"
            installed.write_text("Previous camera service\n")
            unit = root / "nightowl.service"
            unit.write_text("New camera service\n")
            runner = Mock(return_value=SimpleNamespace(stdout="loaded\n"))
            with contextlib.redirect_stdout(io.StringIO()):
                autostart.install_service(unit, installed, runner)
            backups = list(root.glob("*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "Previous camera service\n")
            commands = [call.args[0] for call in runner.call_args_list]
            self.assertEqual(commands[1], ["sudo", "systemctl", "stop", "nightowl.service"])
            self.assertEqual(commands[2][:3], ["sudo", "install", "-o"])
            self.assertEqual(commands[-2], ["sudo", "systemctl", "daemon-reload"])
            self.assertEqual(commands[-1], ["sudo", "systemctl", "enable", "--now", "nightowl.service"])

    def test_write_only_does_not_install_or_start_a_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = str(root / ".venv" / "bin" / "python3")
            with patch.object(autostart, "__file__", str(root / "install_autostart.py")), \
                    patch.object(autostart.os, "geteuid", return_value=1000), \
                    patch.object(autostart.sys, "prefix", str(root / ".venv")), \
                    patch.object(autostart.sys, "base_prefix", "/usr"), \
                    patch.object(autostart.sys, "executable", python), \
                    patch.object(autostart, "stable_serial_path", side_effect=["/dev/JSON", "/dev/GPS"]), \
                    patch.object(autostart, "find_pixhawk", return_value="/dev/GPS"), \
                    patch.object(autostart.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
                    patch.object(autostart, "install_service") as install, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(autostart.main(["--write-only"]), 0)
            install.assert_not_called()
            self.assertTrue((root / "nightowl.service").exists())
            self.assertTrue(all(call.args[0][0] != "sudo" for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
