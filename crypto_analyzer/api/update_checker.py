"""
update_checker.py
檢查/套用 GitHub 更新（透過本機 git，沿用既有的 git 認證設定，不需另外的 API token）。
"""
from __future__ import annotations
import os
import sys
import subprocess

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_git(args: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", _REPO_DIR] + args,
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )


def get_local_head() -> str:
    """回傳本機目前的 git HEAD commit SHA（取不到時回傳空字串）。"""
    try:
        result = _run_git(["rev-parse", "HEAD"])
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def has_local_changes() -> bool:
    """是否有尚未提交的本機修改（更新前需先確認，避免 git pull 造成衝突或遺失變更）。"""
    try:
        result = _run_git(["status", "--porcelain"])
        return bool(result.stdout.strip())
    except Exception:
        return True  # 無法確認時，保守視為「有異動」


def check_for_update() -> dict:
    """
    向 origin 執行 git fetch 後比對本機與遠端 main 分支的差異。
    回傳：
        {"available": bool, "local": str, "remote": str, "log": str, "error": str|None}

    注意：這裡比對的是「本機 git 倉庫」與「GitHub 遠端」的差異，適用於「別台機器
    push 了新版本」的一般情境。若同一台機器上，本機倉庫本身在本程式啟動「之後」
    就已經被直接修改並 push（例如開發時直接在同一份工作目錄編輯），push 完成後
    本機 HEAD 會與 origin 一致，此函式會回報「已是最新版本」——但當下這個「已啟動
    的行程」記憶體內仍是啟動當時載入的舊程式碼。這種「本行程本身已過時」的情況
    請改用 get_local_head() 搭配程式啟動時記錄的 HEAD 比對，見 main_window.py
    的 self._startup_head。
    """
    try:
        fetch = _run_git(["fetch", "origin", "main"], timeout=20)
        if fetch.returncode != 0:
            return {"available": False, "error": fetch.stderr.strip() or "git fetch 失敗"}

        local = _run_git(["rev-parse", "HEAD"]).stdout.strip()
        remote = _run_git(["rev-parse", "origin/main"]).stdout.strip()
        if not local or not remote:
            return {"available": False, "error": "無法取得目前版本資訊"}

        if local == remote:
            return {"available": False, "local": local, "remote": remote, "error": None}

        log = _run_git([
            "log", f"{local}..{remote}",
            "--pretty=format:%h  %ad  %s", "--date=short",
        ]).stdout.strip()
        return {"available": True, "local": local, "remote": remote, "log": log, "error": None}

    except FileNotFoundError:
        return {"available": False, "error": "找不到 git 指令，請確認已安裝 Git 並加入系統 PATH"}
    except subprocess.TimeoutExpired:
        return {"available": False, "error": "連線逾時，請檢查網路狀態"}
    except Exception as e:
        return {"available": False, "error": str(e)}


def apply_update() -> tuple[bool, str]:
    """
    執行 git pull 套用更新。呼叫前應先以 has_local_changes() 確認無未提交異動。
    回傳 (是否成功, 訊息)。
    """
    try:
        result = _run_git(["pull", "--ff-only", "origin", "main"], timeout=60)
        if result.returncode == 0:
            return True, result.stdout.strip() or "更新完成"
        return False, (result.stderr or result.stdout).strip() or "git pull 失敗"
    except FileNotFoundError:
        return False, "找不到 git 指令，請確認已安裝 Git 並加入系統 PATH"
    except subprocess.TimeoutExpired:
        return False, "更新逾時，請檢查網路狀態"
    except Exception as e:
        return False, str(e)


def restart_app() -> None:
    """以同一個 Python 直譯器重新啟動 main.py，取代目前行程。"""
    main_script = os.path.join(_REPO_DIR, "main.py")
    os.execv(sys.executable, [sys.executable, main_script])
