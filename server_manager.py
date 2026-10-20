"""Desktop server control panel for the RentalDesk Windows installation."""
import argparse
import ipaddress
import json
import re
import socket
import subprocess
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


BG = "#f2f5fa"
INK = "#17263c"
MUTED = "#68768b"
BLUE = "#315ee8"
GREEN = "#16945c"
RED = "#c73b43"


def default_ip():
    candidates = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            candidates.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return next((ip for ip in candidates if not ip.startswith("127.") and not ip.startswith("169.254.")), "127.0.0.1")


class ServerManager:
    def __init__(self, config_path):
        self.config_path = Path(config_path).resolve()
        self.config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        self.root = tk.Tk()
        self.root.title("RentalDesk · Сервер")
        self.root.geometry("700x690")
        self.root.minsize(620, 640)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self._build()
        self._load()
        self._refresh_status()

    def _build(self):
        header = tk.Frame(self.root, bg=INK, padx=28, pady=22)
        header.pack(fill="x")
        title_area = tk.Frame(header, bg=INK)
        title_area.pack(side="left")
        tk.Label(title_area, text="RentalDesk", bg=INK, fg="white", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(title_area, text="ПАНЕЛЬ СЕРВЕРА", bg=INK, fg="#b9c7df", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(3, 0))
        status_area = tk.Frame(header, bg=INK)
        status_area.pack(side="right", anchor="ne")
        self.status_dot = tk.Label(status_area, text="●", bg=INK, fg="#f0b44c", font=("Segoe UI", 16))
        self.status_dot.pack(side="left", padx=(0, 7))
        self.status_text = tk.Label(status_area, text="Перевірка…", bg=INK, fg="white", font=("Segoe UI", 10, "bold"))
        self.status_text.pack(side="left")

        body = tk.Frame(self.root, bg=BG, padx=24, pady=20)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Налаштування доступу", bg=BG, fg=INK, font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(0, 12))
        network = self._card(body)
        network.pack(fill="x")
        tk.Label(network, text="IP-АДРЕСА СЕРВЕРА", bg="white", fg=MUTED, font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 5))
        tk.Label(network, text="ПОРТ", bg="white", fg=MUTED, font=("Segoe UI", 9, "bold")).grid(row=0, column=1, sticky="w", padx=(12, 16), pady=(14, 5))
        self.ip_var = tk.StringVar()
        self.port_var = tk.StringVar()
        self.ip_entry = tk.Entry(network, textvariable=self.ip_var, font=("Segoe UI", 11), relief="solid", bd=1)
        self.ip_entry.grid(row=1, column=0, sticky="ew", padx=16, ipady=7)
        self.port_entry = tk.Entry(network, textvariable=self.port_var, font=("Segoe UI", 11), relief="solid", bd=1, width=9)
        self.port_entry.grid(row=1, column=1, sticky="ew", padx=(12, 16), ipady=7)
        self._button(network, "IP цього пристрою", self.use_device_ip, kind="light").grid(row=1, column=2, padx=(0, 10), ipady=4)
        self._button(network, "Порт 5000", lambda: self.port_var.set("5000"), kind="light").grid(row=1, column=3, padx=(0, 16), ipady=4)
        network.grid_columnconfigure(0, weight=1)
        tk.Label(network, text="Цю адресу працівники використовуватимуть у браузері.", bg="white", fg=MUTED, font=("Segoe UI", 9)).grid(row=2, column=0, columnspan=4, sticky="w", padx=16, pady=(8, 14))

        tk.Label(body, text="Автоматизація", bg=BG, fg=INK, font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(20, 12))
        options = self._card(body)
        options.pack(fill="x")
        self.autostart_var = tk.BooleanVar()
        self.backup_enabled_var = tk.BooleanVar()
        tk.Checkbutton(options, text="Автозапуск сервера під час запуску Windows", variable=self.autostart_var, bg="white", fg=INK, activebackground="white", font=("Segoe UI", 10), selectcolor="white").grid(row=0, column=0, sticky="w", padx=16, pady=(14, 6), columnspan=2)
        tk.Checkbutton(options, text="Щоденне резервне копіювання", variable=self.backup_enabled_var, bg="white", fg=INK, activebackground="white", font=("Segoe UI", 10), selectcolor="white", command=self._toggle_backup_time).grid(row=1, column=0, sticky="w", padx=16, pady=6)
        tk.Label(options, text="ЧАС", bg="white", fg=MUTED, font=("Segoe UI", 9, "bold")).grid(row=1, column=1, sticky="e", padx=(8, 16), pady=6)
        self.backup_time_var = tk.StringVar()
        self.backup_time_entry = tk.Entry(options, textvariable=self.backup_time_var, font=("Segoe UI", 10), relief="solid", bd=1, width=9)
        self.backup_time_entry.grid(row=2, column=1, sticky="e", padx=16, pady=(0, 14), ipady=5)
        tk.Label(options, text="Якщо сервер вимкнений у цей час, копія створиться після наступного запуску Windows.", bg="white", fg=MUTED, font=("Segoe UI", 9), wraplength=520, justify="left").grid(row=2, column=0, sticky="w", padx=16, pady=(0, 14))
        options.grid_columnconfigure(0, weight=1)

        self.url_label = tk.Label(body, text="", bg=BG, fg=BLUE, font=("Segoe UI", 10, "underline"), cursor="hand2")
        self.url_label.pack(anchor="w", pady=(13, 0))
        self.url_label.bind("<Button-1>", lambda _event: self._open_url())
        controls = tk.Frame(body, bg=BG)
        controls.pack(fill="x", pady=(17, 10))
        self._button(controls, "▶ Запустити", self.start_server, kind="primary").pack(side="left", padx=(0, 8), ipady=5)
        self._button(controls, "↻ Перезапустити", self.restart_server, kind="light").pack(side="left", padx=8, ipady=5)
        self._button(controls, "■ Зупинити", self.stop_server, kind="danger").pack(side="left", padx=8, ipady=5)
        self._button(controls, "Зберегти налаштування", self.save_settings, kind="primary").pack(side="right", ipady=5)
        tk.Label(body, text=f"База даних: {self.config.get('database', '')}", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(8, 0))

    @staticmethod
    def _card(parent):
        frame = tk.Frame(parent, bg="white", highlightthickness=1, highlightbackground="#dfe5ef")
        return frame

    @staticmethod
    def _button(parent, text, command, kind):
        palette = {"primary": (BLUE, "white"), "light": ("#e9eef8", INK), "danger": ("#fff0f0", RED)}
        bg, fg = palette[kind]
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=bg,
                         activeforeground=fg, relief="flat", padx=14, pady=8, cursor="hand2",
                         font=("Segoe UI", 9, "bold"))

    def _load(self):
        self.ip_var.set(self.config.get("advertised_host") or default_ip())
        self.port_var.set(str(self.config.get("port", 5000)))
        self.backup_time_var.set(self.config.get("backup_time", "06:00"))
        self.autostart_var.set(bool(self.config.get("auto_start", False)))
        self.backup_enabled_var.set(bool(self.config.get("backup_enabled", False)))
        self._toggle_backup_time()
        self._update_url_label()

    def _toggle_backup_time(self):
        self.backup_time_entry.configure(state="normal" if self.backup_enabled_var.get() else "disabled")

    def use_device_ip(self):
        self.ip_var.set(default_ip())
        self._update_url_label()

    def _get_settings(self):
        host = self.ip_var.get().strip()
        if not host or any(ch.isspace() for ch in host) or any(ch in host for ch in "/\\:"):
            raise ValueError("Вкажіть IP-адресу або ім’я вузла без http:// та порту.")
        if re.fullmatch(r"[0-9.]+", host):
            try:
                ipaddress.IPv4Address(host)
            except ipaddress.AddressValueError as exc:
                raise ValueError("Введена IP-адреса має некоректний формат.") from exc
        try:
            port = int(self.port_var.get())
        except ValueError as exc:
            raise ValueError("Порт має бути числом від 1 до 65535.") from exc
        if not 1 <= port <= 65535:
            raise ValueError("Порт має бути числом від 1 до 65535.")
        backup_time = self.backup_time_var.get().strip()
        if len(backup_time) != 5 or backup_time[2] != ":" or not backup_time[:2].isdigit() or not backup_time[3:].isdigit():
            raise ValueError("Вкажіть час копіювання у форматі 24 години, наприклад 06:00.")
        hour, minute = map(int, backup_time.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("Вкажіть коректний час від 00:00 до 23:59.")
        return host, port, backup_time

    def _update_url_label(self):
        host = self.ip_var.get().strip() or "IP сервера"
        port = self.port_var.get().strip() or "5000"
        self.url_label.configure(text=f"Адреса програми:  http://{host}:{port}")

    def _powershell(self, command):
        result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "Помилка керування службою.").strip())

    def save_settings(self):
        try:
            host, port, backup_time = self._get_settings()
            was_running = self._is_running()
            old_port = int(self.config.get("port", 5000))
            self.config.update({"advertised_host": host, "port": port, "bind_host": "0.0.0.0",
                                "public_url": f"http://{host}:{port}", "backup_time": backup_time,
                                "auto_start": self.autostart_var.get(), "backup_enabled": self.backup_enabled_var.get()})
            self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
            self._configure_tasks(old_port, port)
            info = [f"Адреса для працівників: {self.config['public_url']}",
                    f"Налаштування адміністратора: {self.config['public_url']}/setup",
                    f"Автоматичне резервне копіювання: {'щодня о ' + backup_time if self.config['backup_enabled'] else 'вимкнено'}",
                    f"Папка копій: {self.config['backup_dir']}"]
            (Path(self.config["data_dir"]) / "SERVER-ADDRESS.txt").write_text("\n".join(info), encoding="utf-8")
            self._update_url_label()
            if was_running:
                self._restart_task()
            messagebox.showinfo("Налаштування збережено", "Налаштування сервера збережено." + (" Сервер перезапущено для застосування нового порту." if was_running else " Запустіть сервер, щоб застосувати зміни."), parent=self.root)
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            messagebox.showerror("Не вдалося зберегти", str(exc), parent=self.root)

    def _configure_tasks(self, old_port, port):
        auto = "$true" if self.config["auto_start"] else "$false"
        backup_enabled = "$true" if self.config["backup_enabled"] else "$false"
        time_text = self.config["backup_time"]
        config_path = str(self.config_path)
        install_dir = str(Path(__file__).resolve().parent)
        python = str(Path(install_dir) / ".venv" / "Scripts" / "python.exe")
        run_script = str(Path(install_dir) / "run_server.py")
        backup_script = str(Path(install_dir) / "backup_db.py")
        command = f"""
$ErrorActionPreference='Stop'
$principal=New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\\SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$server=Get-ScheduledTask -TaskName 'RentalDesk Server'
if ({auto}) {{ Enable-ScheduledTask -TaskName 'RentalDesk Server' | Out-Null }} else {{ Disable-ScheduledTask -TaskName 'RentalDesk Server' | Out-Null }}
$backupAction=New-ScheduledTaskAction -Execute '{python}' -Argument '"{backup_script}" --config "{config_path}"' -WorkingDirectory '{install_dir}'
$backupTrigger=New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.Add([TimeSpan]::ParseExact('{time_text}','hh\\:mm',[Globalization.CultureInfo]::InvariantCulture)))
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName 'RentalDesk Daily Backup' -Action $backupAction -Trigger $backupTrigger -Principal $principal -Settings $settings -Description 'Daily SQLite backup of RentalDesk' -Force | Out-Null
if ({backup_enabled}) {{ Enable-ScheduledTask -TaskName 'RentalDesk Daily Backup' | Out-Null }} else {{ Disable-ScheduledTask -TaskName 'RentalDesk Daily Backup' | Out-Null }}
if ({old_port} -ne {port}) {{ Get-NetFirewallRule -DisplayName 'RentalDesk TCP *' -ErrorAction SilentlyContinue | Remove-NetFirewallRule; New-NetFirewallRule -DisplayName 'RentalDesk TCP {port}' -Direction Inbound -Action Allow -Protocol TCP -LocalPort {port} -Profile Domain,Private | Out-Null }}
"""
        self._powershell(command)

    def _task(self, action):
        command = f"$ErrorActionPreference='Stop'; {action} -TaskName 'RentalDesk Server' | Out-Null"
        self._powershell(command)

    def start_server(self):
        try:
            self._task("Enable-ScheduledTask")
            self._task("Start-ScheduledTask")
            if not self.config.get("auto_start", False):
                self._task("Disable-ScheduledTask")
            self.root.after(700, self._refresh_status)
        except (RuntimeError, subprocess.SubprocessError) as exc:
            messagebox.showerror("Помилка запуску", str(exc), parent=self.root)

    def stop_server(self):
        try:
            self._task("Stop-ScheduledTask")
            if not self.config.get("auto_start", False):
                self._task("Disable-ScheduledTask")
            self.root.after(500, self._refresh_status)
        except (RuntimeError, subprocess.SubprocessError) as exc:
            messagebox.showerror("Помилка зупинки", str(exc), parent=self.root)

    def _restart_task(self):
        self._task("Enable-ScheduledTask")
        self._task("Stop-ScheduledTask")
        self._task("Start-ScheduledTask")
        if not self.config.get("auto_start", False):
            self._task("Disable-ScheduledTask")

    def restart_server(self):
        try:
            self._restart_task()
            self.root.after(700, self._refresh_status)
        except (RuntimeError, subprocess.SubprocessError) as exc:
            messagebox.showerror("Помилка перезапуску", str(exc), parent=self.root)

    def _is_running(self):
        try:
            with urlopen(f"http://127.0.0.1:{self.config.get('port', 5000)}/login", timeout=0.8):
                return True
        except (URLError, OSError, ValueError):
            return False

    def _refresh_status(self):
        if self._is_running():
            self.status_dot.configure(fg="#35d08a")
            self.status_text.configure(text="Сервер працює")
        else:
            self.status_dot.configure(fg="#ee6870")
            self.status_text.configure(text="Сервер зупинено")
        self._update_url_label()
        self.root.after(2500, self._refresh_status)

    def _open_url(self):
        import webbrowser
        try:
            host, port, _ = self._get_settings()
            webbrowser.open(f"http://{host}:{port}")
        except ValueError as exc:
            messagebox.showerror("Перевірте адресу", str(exc), parent=self.root)

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    ServerManager(args.config).run()


if __name__ == "__main__":
    main()
