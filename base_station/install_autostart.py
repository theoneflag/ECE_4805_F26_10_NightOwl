"""Install the Pi's current src checkout and active camera venv as nightowl.service."""

import argparse
import grp
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile

from mavlink_gps import find_pixhawk

SERVICE = "nightowl.service"
LIBRARY_ENVIRONMENT = (
    "LD_LIBRARY_PATH", "GST_PLUGIN_PATH", "GST_PLUGIN_PATH_1_0",
    "GST_PLUGIN_SYSTEM_PATH", "GST_PLUGIN_SYSTEM_PATH_1_0",
)


def stable_serial_path(device, directory=Path("/dev/serial/by-id")):
    path = Path(device).expanduser().absolute()
    if not path.exists():
        raise ValueError(f"Serial device does not exist: {path}")
    target = path.resolve()
    matches = sorted(alias for alias in directory.glob("*")
                     if alias.exists() and alias.resolve() == target)
    if not matches:
        raise ValueError(f"No stable USB by-id path found for {path}; inspect /dev/serial/by-id/")
    return str(matches[0])


def _literal(value):
    value = str(value)
    if any(ord(character) < 32 for character in value):
        raise ValueError("Service arguments cannot contain control characters")
    return value.replace("%", "%%")


def _quote(value, command=False):
    value = _literal(value).replace("\\", "\\\\").replace('"', '\\"')
    if command:
        value = value.replace("$", "$$")
    return '"' + value + '"'


def render_service(code_dir, python, user, group, radio, pixhawk, baud=115200,
                   gps_baud=115200, supplementary_groups=(), library_environment=None):
    code_dir, python = Path(code_dir), Path(python)
    launcher = code_dir / "Night_Owl_Live_Radio.py"
    command = [python, "-u", launcher, "--serial-port", radio, "--baud", baud,
               "--gps", "mavlink", "--gps-device", pixhawk, "--gps-baud", gps_baud]
    preflight = [python, launcher, "--check"]
    # Keep the venv path itself; resolving its Python symlink would select
    # /usr/bin/python3 and lose the venv's package environment.
    environment = {
        "PATH": f"{python.parent}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONUNBUFFERED": "1",
    }
    for name in LIBRARY_ENVIRONMENT:
        if library_environment and name in library_environment:
            environment[name] = library_environment[name]
    lines = [
        "[Unit]",
        "Description=Night Owl camera, GPS and thumbnail radio transmitter",
        "After=systemd-udevd.service",
        "StartLimitIntervalSec=0",
        "",
        "[Service]",
        "Type=exec",
        f"User={_literal(user)}",
        f"Group={_literal(group)}",
        f"WorkingDirectory={_literal(code_dir)}",
    ]
    if supplementary_groups:
        lines.append("SupplementaryGroups=" + " ".join(_literal(name) for name in supplementary_groups))
    lines.extend("Environment=" + _quote(f"{name}={value}") for name, value in environment.items())
    lines.extend([
        "ExecStartPre=" + " ".join(_quote(value, command=True) for value in preflight),
        "ExecStart=" + " ".join(_quote(value, command=True) for value in command),
        "Restart=on-failure",
        "RestartSec=5",
        "KillSignal=SIGINT",
        "KillMode=control-group",
        "TimeoutStopSec=30",
        "SuccessExitStatus=130",
        "StandardOutput=journal",
        "StandardError=journal",
        "SyslogIdentifier=nightowl",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])
    return "\n".join(lines)


def install_service(unit, installed=Path("/etc/systemd/system") / SERVICE, runner=None):
    runner = runner or subprocess.run
    if installed.exists():
        with tempfile.NamedTemporaryFile(dir=unit.parent, prefix="nightowl-service-previous-",
                                         suffix=".bak", delete=False) as backup:
            backup.write(installed.read_bytes())
            print(f"Previous service backed up to {backup.name}", flush=True)
    state = runner(["systemctl", "show", SERVICE, "--property=LoadState", "--value"],
                   capture_output=True, text=True, check=True).stdout.strip()
    if state != "not-found":
        runner(["sudo", "systemctl", "stop", SERVICE], check=True)
    runner(["sudo", "install", "-o", "root", "-g", "root", "-m", "644",
            str(unit), str(installed)], check=True)
    runner(["sudo", "systemctl", "daemon-reload"], check=True)
    runner(["sudo", "systemctl", "enable", "--now", SERVICE], check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial-port", default="/dev/ttyUSB0", help="working JSON radio port; saved as its stable USB by-id path")
    parser.add_argument("--gps-device", default="auto", help="Pixhawk USB port, or auto")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--gps-baud", type=int, default=115200)
    parser.add_argument("--write-only", action="store_true", help="generate and verify the service file without installing it")
    args = parser.parse_args(argv)
    if os.geteuid() == 0:
        parser.error("Run as your normal Pi user with the working camera venv activated; this script invokes sudo for installation")
    if sys.prefix == sys.base_prefix:
        parser.error("Activate the camera venv that passed --check, then run this installer with its python3")
    if args.baud <= 0 or args.gps_baud <= 0:
        parser.error("Baud rates must be positive")
    code_dir = Path(__file__).resolve().parent
    python = Path(sys.executable).absolute()
    try:
        radio = stable_serial_path(args.serial_port)
        pixhawk = stable_serial_path(find_pixhawk(args.gps_device))
        if Path(radio).resolve() == Path(pixhawk).resolve():
            raise ValueError("The JSON radio and Pixhawk must be separate USB devices")
        subprocess.run([str(python), str(code_dir / "Night_Owl_Live_Radio.py"), "--check"],
                       cwd=code_dir, check=True)
        account = pwd.getpwuid(os.getuid())
        user = account.pw_name
        group = grp.getgrgid(account.pw_gid).gr_name
        groups = []
        for name in ("dialout", "video", "render"):
            try:
                grp.getgrnam(name)
                groups.append(name)
            except KeyError:
                pass
        unit = code_dir / SERVICE
        unit.write_text(render_service(code_dir, python, user, group, radio, pixhawk,
                                       args.baud, args.gps_baud, groups, os.environ), encoding="utf-8")
        subprocess.run(["systemd-analyze", "verify", str(unit)], check=True)
        print(f"Prepared {unit}\nPython: {python}\nJSON radio: {radio}\nPixhawk: {pixhawk}", flush=True)
        if args.write_only:
            return 0
        install_service(unit)
        print("Boot startup enabled. Follow logs: journalctl -u nightowl.service -f", flush=True)
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Autostart setup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
