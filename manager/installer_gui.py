"""
"LAN Usage Monitor - Manager Setup" -- a self-contained installer GUI.

Built by PyInstaller into a single ManagerSetup.exe that embeds the whole
manager payload (LanUsageMonitorManager.exe + its files) as bundled data.
When the user double-clicks it, this script:
  1. Relaunches itself elevated (UAC prompt) if not already admin
  2. Asks for an install directory and the LAN subnet to allow
  3. Copies the payload into place
  4. Generates a TLS cert, registers + starts the Windows Service, configures
     auto-restart-on-crash, opens the firewall for the given subnet, and adds
     an Add/Remove Programs entry
  5. Shows the dashboard URL and where to find each client PC's API key

Every privileged step is delegated to LanUsageMonitorManager.exe itself
(install / gencert / uninstall-all) rather than duplicated here, so the
installer and the app can never disagree about how installation works.
"""

import ctypes
import shutil
import socket
import subprocess
import sys
import threading
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

APP_NAME = "LAN Usage Monitor - Manager"
SERVICE_NAME = "LanUsageMonitorManager"
DEFAULT_INSTALL_DIR = r"C:\Program Files\LAN Usage Monitor\Manager"
FIREWALL_RULE_NAME = "LAN Usage Monitor - Manager"
UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\LanUsageMonitorManager"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_elevated():
    params = " ".join(f'"{a}"' for a in sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)


def payload_dir() -> Path:
    """Where the bundled manager app files live -- PyInstaller's extraction
    dir when frozen (added via --add-data "dist/LanUsageMonitorManager;payload"),
    or the local dev build output otherwise."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    candidate = base / "payload"
    if candidate.exists():
        return candidate
    return Path(__file__).parent / "dist" / "LanUsageMonitorManager"


def guess_subnet():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packet actually sent; just picks the outbound route
        ip = s.getsockname()[0]
    except OSError:
        ip = "192.168.1.10"
    finally:
        s.close()
    parts = ip.split(".")
    return f"{'.'.join(parts[:3])}.0/24"


class InstallerApp:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP_NAME} Setup")
        root.geometry("560x480")
        root.resizable(False, False)

        tk.Label(root, text=APP_NAME, font=("Segoe UI", 14, "bold")).pack(pady=(16, 4))
        tk.Label(
            root,
            text="Installs the manager service that receives usage reports\n"
                 "from your client PCs and serves the monitoring dashboard.",
            justify="center",
        ).pack(pady=(0, 12))

        form = tk.Frame(root)
        form.pack(fill="x", padx=24)

        tk.Label(form, text="Install to:").grid(row=0, column=0, sticky="w", pady=4)
        self.install_dir = tk.StringVar(value=DEFAULT_INSTALL_DIR)
        tk.Entry(form, textvariable=self.install_dir, width=48).grid(row=0, column=1, sticky="w")
        tk.Button(form, text="Browse...", command=self._browse).grid(row=0, column=2, padx=6)

        tk.Label(form, text="Allow connections from LAN subnet:").grid(row=1, column=0, sticky="w", pady=4)
        self.subnet = tk.StringVar(value=guess_subnet())
        tk.Entry(form, textvariable=self.subnet, width=48).grid(row=1, column=1, sticky="w")
        tk.Label(
            root,
            text="Only PCs in this subnet will be able to send reports or view the dashboard.",
            fg="#666", font=("Segoe UI", 8),
        ).pack(anchor="w", padx=24)

        self.install_btn = tk.Button(root, text="Install", width=16, command=self._start_install)
        self.install_btn.pack(pady=12)

        self.log = scrolledtext.ScrolledText(root, height=14, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=24, pady=(0, 16))

    def _browse(self):
        chosen = filedialog.askdirectory()
        if chosen:
            self.install_dir.set(str(Path(chosen) / "LAN Usage Monitor" / "Manager"))

    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.root.update_idletasks()

    def _start_install(self):
        self.install_btn.configure(state="disabled")
        threading.Thread(target=self._run_install, daemon=True).start()

    def _run(self, args, **kwargs):
        self._log("> " + " ".join(args))
        result = subprocess.run(
            args, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW, **kwargs,
        )
        if result.stdout.strip():
            self._log(result.stdout.strip())
        if result.stderr.strip():
            self._log(result.stderr.strip())
        return result

    def _run_install(self):
        try:
            install_dir = Path(self.install_dir.get())
            subnet = self.subnet.get().strip()
            src = payload_dir()

            if not src.exists():
                self._log(f"ERROR: bundled payload not found at {src}")
                messagebox.showerror(APP_NAME, "Installer payload missing -- this build is broken.")
                return

            self._log(f"Installing to {install_dir} ...")
            install_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, install_dir, dirs_exist_ok=True)
            exe = install_dir / "LanUsageMonitorManager.exe"

            cert_path = install_dir / "certs" / "cert.pem"
            if not cert_path.exists():
                self._log("Generating TLS certificate...")
                self._run([str(exe), "gencert"])
            else:
                self._log("TLS certificate already exists, skipping.")

            self._log("Registering Windows Service (auto-start)...")
            self._run([str(exe), "--startup", "auto", "install"])

            self._log("Configuring crash auto-restart...")
            self._run([
                "sc.exe", "failure", SERVICE_NAME,
                "reset=", "86400",
                "actions=", "restart/5000/restart/5000/restart/60000",
            ])

            self._log(f"Opening firewall for TCP/8443 from {subnet}...")
            self._run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE_NAME}"])
            self._run([
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={FIREWALL_RULE_NAME}", "dir=in", "action=allow", "protocol=TCP",
                "localport=8443", f"remoteip={subnet}",
            ])

            self._log("Starting service...")
            self._run([str(exe), "start"])

            self._log("Registering Add/Remove Programs entry...")
            self._register_uninstaller(exe, install_dir)

            self._log("")
            self._log("=== Install complete ===")
            self._show_summary(install_dir)

        except Exception as e:
            self._log(f"FAILED: {e}")
            messagebox.showerror(APP_NAME, f"Install failed: {e}")
        finally:
            self.install_btn.configure(state="normal")

    def _register_uninstaller(self, exe: Path, install_dir: Path):
        with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, "0.1.0")
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "LAN Usage Monitor")
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(exe))
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{exe}" uninstall-all')
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)

    def _show_summary(self, install_dir: Path):
        clients_path = install_dir / "config" / "clients.json"
        self._log(f"Dashboard: https://localhost:8443/  (accept the self-signed cert warning)")
        self._log(f"Client API keys: {clients_path}")
        self._log("Copy each client_id/api_key pair + certs\\cert.pem to the matching client PC's agent install.")
        messagebox.showinfo(
            APP_NAME,
            "Install complete.\n\n"
            "Dashboard: https://localhost:8443/\n\n"
            f"Client API keys are in:\n{clients_path}\n\n"
            "Copy each client's api_key and certs\\cert.pem to that PC's agent installer.",
        )


def main():
    if not is_admin():
        relaunch_elevated()
        return
    root = tk.Tk()
    InstallerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
