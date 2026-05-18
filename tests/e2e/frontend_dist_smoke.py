from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = ROOT / "apps" / "frontend" / "noteapp-web"
PORT = int(os.environ.get("NOTEAPP_FRONTEND_DIST_SMOKE_PORT", "8093"))
BASE_URL = f"http://127.0.0.1:{PORT}"


def isolated_node_env(base_env: dict[str, str], runtime_root: Path) -> dict[str, str]:
    home = runtime_root / "home"
    appdata = runtime_root / "appdata"
    local_appdata = runtime_root / "local-appdata"
    temp = runtime_root / "temp"
    npm_cache = runtime_root / "npm-cache"
    npmrc = runtime_root / "npmrc"
    for path in (home, appdata, local_appdata, temp, npm_cache):
        path.mkdir(parents=True, exist_ok=True)
    return {
        **base_env,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(appdata),
        "LOCALAPPDATA": str(local_appdata),
        "TEMP": str(temp),
        "TMP": str(temp),
        "npm_config_cache": str(npm_cache),
        "npm_config_userconfig": str(npmrc),
        "NPM_CONFIG_CACHE": str(npm_cache),
        "NPM_CONFIG_USERCONFIG": str(npmrc),
    }


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
    url = urllib.parse.urljoin(f"{BASE_URL}/", path)
    with urllib.request.urlopen(url, timeout=10) as response:
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
    with tempfile.TemporaryDirectory() as runtime_dir:
        env = isolated_node_env({**os.environ, "PORT": str(PORT)}, Path(runtime_dir))
        run_checked(
            ["node", "-e", "require('node:fs').rmSync('dist', { recursive: true, force: true })"],
            cwd=FRONTEND_ROOT,
            env=env,
        )
        run_checked(["node", "node_modules/vite/bin/vite.js", "build"], cwd=FRONTEND_ROOT, env=env)

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
            assert "\u540c\u6b65\u72b6\u6001" in bundle, "settings sync view was not bundled"
            assert "\u540c\u6b65\u5361\u7247" in bundle, "sync cards view was not bundled"
            assert "\u8fde\u63a5" in bundle, "settings snapshot connection view was not bundled"
            assert "AI \u6a21\u578b" in bundle, "settings AI view was not bundled"
            assert "\u5de5\u4f5c\u533a" in bundle, "workspace explorer view was not bundled"
            assert "\u5de5\u4f5c\u533a\u6587\u4ef6\u5939" in bundle, "workspace folder selector was not bundled"
            assert "\u9009\u62e9\u6587\u4ef6\u5939" in bundle, "workspace folder picker action was not bundled"
            assert "\u6587\u4ef6\u5143\u6570\u636e" in bundle, "workspace file metadata view was not bundled"
            assert "Markdown \u7f16\u8f91\u5668" in bundle, "workspace markdown editor was not bundled"
            assert "\u5df2\u68c0\u67e5\u672c\u5730\u53d8\u66f4" in bundle, "sync action notice view was not bundled"
            assert "\u672c\u5730\u53d8\u66f4\u5df2\u63d0\u4ea4" in bundle, "sync submit result notice was not bundled"

            assert "\u51b2\u7a81\u526f\u672c\u5df2\u6e05\u7406" in bundle, "sync conflict cleanup notice was not bundled"
            assert "\u6e05\u7406\u5168\u90e8\u51b2\u7a81\u526f\u672c" in bundle, "live conflicts cleanup view was not bundled"
            assert "\u5f53\u524d\u6ca1\u6709\u672a\u5904\u7406\u51b2\u7a81" in bundle, "live conflicts empty state was not bundled"
            assert "\u5df2\u5e94\u7528\u8fdc\u7aef\u5185\u5bb9\uff0c\u8bf7\u5904\u7406\u672c\u5730\u51b2\u7a81\u526f\u672c" in bundle, "sync pull conflict notice was not bundled"
            assert "\u5df2\u62c9\u53d6\u8fdc\u7aef\u57fa\u7ebf\uff0c\u53ef\u7ee7\u7eed\u63d0\u4ea4" in bundle, "sync pull recovery notice was not bundled"
            assert "\u8fdc\u7aef\u7248\u672c\u5df2\u66f4\u65b0\uff0c\u9700\u8981\u5148\u62c9\u53d6" in bundle, "sync submit conflict notice was not bundled"
            assert "\u540c\u6b65\u64cd\u4f5c\u5931\u8d25" in bundle, "sync failure notice was not bundled"
            assert "\u65e9\u4e0a\u597d" not in bundle, "static dashboard demo page should not be bundled in MVP shell"
            assert "\u795e\u7ecf\u7f51\u7edc\u7b80\u4ecb" not in bundle, "static graph demo page should not be bundled in MVP shell"
            assert "\u521d\u59cb\u5316\u65b0\u7684 Wiki" not in bundle, "static wiki demo page should not be bundled in MVP shell"
            assert "\u56de\u6536\u7ad9\u4e2d\u7684\u9879\u76ee" not in bundle, "static trash demo page should not be bundled in MVP shell"

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
