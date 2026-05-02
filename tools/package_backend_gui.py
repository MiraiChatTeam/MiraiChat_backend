import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def _data_arg(source: Path, target: str) -> str:
    sep = ";" if os.name == "nt" else ":"
    return f"{source}{sep}{target}"


def main() -> int:
    project_dir = Path(__file__).resolve().parent.parent
    entry_script = project_dir / "tools" / "start_backend_gui.py"
    sync_script = project_dir / "tools" / "sync_cloudflare_pin.py"
    main_py = project_dir / "new_main.py"

    if not entry_script.exists() or not sync_script.exists() or not main_py.exists():
        print("[ERROR] Required files are missing. Expected new_main.py and tools scripts.")
        return 1

    artifact_name = f"chatapp-backend-launcher-{platform.system().lower()}-{platform.machine().lower()}"
    dist_dir = project_dir / "dist"
    build_dir = project_dir / "build" / "pyinstaller_backend_gui"

    if build_dir.exists():
        shutil.rmtree(build_dir)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        artifact_name,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--hidden-import",
        "new_main",
        "--add-data",
        _data_arg(main_py, "."),
        "--add-data",
        _data_arg(sync_script, "tools"),
        str(entry_script),
    ]

    print("[INFO] Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(project_dir), check=False)
    if result.returncode != 0:
        print(f"[ERROR] Packaging failed with exit code {result.returncode}")
        return int(result.returncode)

    output_dir = dist_dir / artifact_name
    if not output_dir.exists():
        print(f"[ERROR] Build output folder not found: {output_dir}")
        return 1

    default_cf_config = output_dir / "tools" / "cloudflare_sync_config.sample.json"
    default_cf_config.parent.mkdir(parents=True, exist_ok=True)
    default_cf_config.write_text(
        "{\n"
        "  \"domain\": \"your.domain.com\",\n"
        "  \"admin_base\": \"https://your.domain.com\",\n"
        "  \"admin_user\": \"admin\",\n"
        "  \"admin_pass\": \"runtime-only-password-do-not-store-for-gui\",\n"
        "  \"admin_totp_secret\": \"\",\n"
        "  \"state_file\": \"tools/.pin_sync_state.json\"\n"
        "}\n",
        encoding="utf-8",
    )

    print(f"[OK] Packaged launcher created at: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
