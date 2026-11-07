# -*- coding: utf-8 -*-
"""
Автозбереження коду SMC App у GitHub (git add / commit / push) — працює на Mac у фоні.

- Що потрапляє в git, вирішує .gitignore: журнал, кеш бектесту, налаштування, логи й бекапи — ні.
- Коміт робиться, коли набір змінених файлів не змінювався між двома перевірками (щоб не зберегти
  «половину» оновлення, поки файли ще копіюються).
- Пуш — з тими ж обліковими даними, що в Терміналі (macOS Keychain). Без інтерактивних запитів:
  якщо токен прострочено — у «Налаштуваннях» з'являється пояснення, що робити.
"""
import datetime as dt
import json
import os
import re
import subprocess
import threading
import time

TIMEOUT = 60


class GitSync:
    def __init__(self, repo_dir, state_path, enabled=lambda: True, version="", interval=60):
        self.dir = repo_dir
        self.state_path = state_path
        self.enabled = enabled
        self.version = version
        self.interval = interval
        self.lock = threading.Lock()
        self._last_dirty = None
        self.state = self._load()

    # ------------------------------------------------------------------ git
    def _git(self, *args, timeout=TIMEOUT, raw=False):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="echo", SSH_ASKPASS="echo", LC_ALL="C")
        try:
            p = subprocess.run(["git", *args], cwd=self.dir, capture_output=True, text=True, timeout=timeout, env=env)
            return p.returncode, (p.stdout or "") if raw else (p.stdout or "").strip(), (p.stderr or "").strip()
        except FileNotFoundError:
            return 127, "", "git не встановлено"
        except subprocess.TimeoutExpired:
            return 124, "", "git не відповів за хвилину (мережа?)"

    def is_repo(self):
        return os.path.isdir(os.path.join(self.dir, ".git"))

    def remote_url(self):
        code, out, _ = self._git("remote", "get-url", "origin")
        return out if code == 0 else ""

    @staticmethod
    def web_url(remote):
        m = re.match(r"(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?/?$", remote or "")
        return f"https://github.com/{m.group(1)}" if m else ""

    def dirty(self):
        code, out, _ = self._git("status", "--porcelain", "--untracked-files=all", raw=True)
        return sorted(l[3:].strip('"') for l in out.splitlines() if l.strip()) if code == 0 else []

    def _signature(self, files):
        """Змінені файли + їх розмір і час зміни: якщо між перевірками не змінилось — оновлення завершено."""
        sig = []
        for f in files:
            try:
                st = os.stat(os.path.join(self.dir, f))
                sig.append((f, st.st_size, st.st_mtime_ns))
            except OSError:
                sig.append((f, None, None))   # файл видалено
        return sig

    def ahead(self):
        code, out, _ = self._git("rev-list", "--count", "@{u}..HEAD")
        if code == 0 and out.isdigit():
            return int(out)
        code, out, _ = self._git("rev-list", "--count", "HEAD")   # ще немає upstream — усе «попереду»
        return int(out) if code == 0 and out.isdigit() else 0

    # ------------------------------------------------------------------ стан
    def _load(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self):
        try:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    @staticmethod
    def explain(err):
        e = (err or "").lower()
        if "authentication failed" in e or "invalid username or token" in e or "could not read username" in e \
                or "terminal prompts disabled" in e or "403" in e:
            return ("GitHub не прийняв вхід (токен прострочений або не збережений). У Терміналі: "
                    "cd ~/smc_app && git push — логін zbihlei, у полі Password — новий токен ghp_…")
        if "could not resolve host" in e or "unable to access" in e or "timed out" in e or "не відповів" in e:
            return "немає зв'язку з GitHub — спробую знову, коли з'явиться інтернет"
        if "rejected" in e or "fetch first" in e or "non-fast-forward" in e:
            return "на GitHub є зміни, яких немає на Mac. У Терміналі: cd ~/smc_app && git pull --rebase && git push"
        if "please tell me who you are" in e:
            return "git не знає твого імені. У Терміналі: git config --global user.name \"Michel\" && git config --global user.email \"…\""
        if "not a git repository" in e:
            return "папка ще не підключена до git"
        return (err or "невідома помилка").splitlines()[-1][:300]

    def status(self):
        st = dict(self.state)
        st["enabled"] = bool(self.enabled())
        st["repo"] = self.is_repo()
        if st["repo"]:
            remote = self.remote_url()
            st["remote"] = remote
            st["web"] = self.web_url(remote)
            code, br, _ = self._git("rev-parse", "--abbrev-ref", "HEAD")
            st["branch"] = br if code == 0 else ""
            st["dirty"] = self.dirty()[:20]
            st["ahead"] = self.ahead()
        return st

    # ------------------------------------------------------------------ дія
    def sync(self, force=False, now=None):
        """Закомітити зміни (якщо стабільні або force) і запушити. Повертає стан."""
        with self.lock:
            now = now or dt.datetime.now()
            self.state["last_check"] = now.isoformat(timespec="seconds")
            if not self.is_repo():
                self.state.update(ok=False, error="папка ще не підключена до git (див. інструкцію нижче)")
                self._save()
                return self.state
            files = self.dirty()
            sig = self._signature(files)
            if files and (force or sig == self._last_dirty):
                code, _, err = self._git("add", "-A")
                if code == 0:
                    names = ", ".join(os.path.basename(f.rstrip("/")) for f in files[:6]) + (f" та ще {len(files) - 6}" if len(files) > 6 else "")
                    msg = f"Автозбереження{(' v' + self.version) if self.version else ''}: {names}"
                    code, out, err = self._git("commit", "-m", msg)
                if code != 0:
                    self.state.update(ok=False, error=self.explain(err), error_at=now.isoformat(timespec="seconds"))
                    self._save()
                    return self.state
                _, h, _ = self._git("rev-parse", "--short", "HEAD")
                self.state["last_commit"] = {"hash": h, "msg": msg, "at": now.isoformat(timespec="seconds"), "files": len(files)}
                files = []
            self._last_dirty = sig if files else None
            if self.ahead() > 0:
                code, br, _ = self._git("rev-parse", "--abbrev-ref", "HEAD")
                code, out, err = self._git("push", "-u", "origin", br or "HEAD")
                if code == 0:
                    self.state.update(ok=True, error="", last_push=now.isoformat(timespec="seconds"))
                else:
                    self.state.update(ok=False, error=self.explain(err), error_at=now.isoformat(timespec="seconds"))
            elif not files:
                self.state.update(ok=True, error="")
            self._save()
            return self.state

    def loop(self):
        while True:
            time.sleep(self.interval)
            try:
                if self.enabled():
                    self.sync()
            except Exception as e:  # фоновий потік не має падати
                self.state.update(ok=False, error=f"{e.__class__.__name__}: {e}")

    def start(self):
        threading.Thread(target=self.loop, daemon=True).start()
