from __future__ import annotations

import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = ROOT / "apps" / "frontend" / "noteapp-web"
PORT = int(os.environ.get("NOTEAPP_FRONTEND_DIST_SMOKE_PORT", "8093"))
BASE_URL = f"http://127.0.0.1:{PORT}"


def run_checked(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            "command failed with exit code "
            f"{result.returncode}: {' '.join(command)}\n{result.stdout}"
        )


def read_url(path: str) -> tuple[int, str]:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=10) as response:
        return response.status, response.read().decode("utf-8")


def wait_for_dist_server() -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            status, content = read_url("/")
            if status == 200 and '<div id="root"></div>' in content:
                return
        except OSError:
            pass
        time.sleep(0.25)
    raise RuntimeError("frontend dist server did not become ready")


def main() -> int:
    env = {**os.environ, "PORT": str(PORT)}
    run_checked(["npm.cmd", "run", "clean"], cwd=FRONTEND_ROOT, env=env)
    run_checked(["npm.cmd", "run", "build"], cwd=FRONTEND_ROOT, env=env)

    process = subprocess.Popen(
        ["node", "scripts/serve-dist.mjs"],
        cwd=FRONTEND_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_dist_server()
        status, index_html = read_url("/")
        assert status == 200, status

        status, fallback_html = read_url("/settings")
        assert status == 200, status
        assert fallback_html == index_html, "SPA fallback should return index.html"

        assets = re.findall(r'src="([^"]+\.js)"', index_html)
        assert assets, index_html
        status, bundle = read_url(assets[0])
        assert status == 200, status
        assert "同步状态" in bundle, "settings sync view was not bundled"
        assert "同步卡片" in bundle, "sync cards view was not bundled"
        assert "连接" in bundle, "settings snapshot connection view was not bundled"
        assert "本地模型" in bundle, "settings AI view was not bundled"
        assert "工作区" in bundle, "workspace explorer view was not bundled"
        assert "文件元数据" in bundle, "workspace file metadata view was not bundled"
        assert "内容编辑器" in bundle, "workspace file content editor was not bundled"
        assert "已检查本地变更" in bundle, "sync action notice view was not bundled"
        assert "本地变更已提交" in bundle, "sync submit result notice was not bundled"

        assert "冲突副本已清理" in bundle, "sync conflict cleanup notice was not bundled"
        assert "已应用远端内容，请处理本地冲突副本" in bundle, "sync pull conflict notice was not bundled"
        assert "已拉取远端基线，可继续提交" in bundle, "sync pull recovery notice was not bundled"
        assert "远端版本已更新，需要先拉取" in bundle, "sync submit conflict notice was not bundled"
        assert "同步操作失败" in bundle, "sync failure notice was not bundled"

        print(
            {
                "ok": True,
                "base_url": BASE_URL,
                "bundle": assets[0],
            }
        )
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
