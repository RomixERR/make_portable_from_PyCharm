#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
make_portable.py -- делает портативную (переносимую) сборку проекта из PyCharm.

Что делает:
 1. Находит в проекте виртуальное окружение (venv) и читает из него версию
    и разрядность Python, а также список установленных пакетов.
 2. Находит на этом компьютере такой же Python (та же версия и разрядность)
    и копирует его целиком как автономный рантайм runtime\\.
 3. Копирует исходники проекта (без venv / .idea / кэшей).
 4. Кладёт пакеты из venv внутрь рантайма в runtime\Lib\site-packages.
 5. Пишет Start.bat (плюс Start_gui.bat для GUI-приложений): запуск двойным
    кликом, без интернета, без установленного Python, из любой папки.
 6. Проверяет сборку: запуск из чужой папки и запуск из копии всего каталога
    (эмуляция переноса на другой компьютер), плюс проверку, что сборка нигде
    не ссылается на исходные папки.

Только стандартная библиотека Python. Работает на Python 3.6+.
"""

import argparse
import ast
import fnmatch
import glob
import os
import re
import shutil
import subprocess
import sys
import time

TOOL_VERSION = "1.2"

# Каталог проекта (обычно venv) -- распознаём по содержимому
VENV_NAME_HINTS = ("venv", ".venv", "env", ".env", "virtualenv", ".virtualenv",
                   "venv3", "venv64", "py3env")

# Каталог, который точно не нужен в переносимой копии
COPY_SKIP_DIRS = {
    ".idea", ".vscode", ".git", ".svn", ".hg", ".backup", ".qodo",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".eggs", "node_modules", "build", "dist", ".cache",
}
COPY_SKIP_FILE_PATTERNS = [
    "*.pyc", "*.pyo", "Thumbs.db", ".DS_Store", "desktop.ini",
    "*.tmp", "~", "*.orig", "*.virtualenv",
]
# Из стандартной библиотеки Python не копируем только явные тяжёлые лишние разделы
STDLIB_SKIP_DIRS = {"site-packages", "test", "site-packages-test", "idlelib",
                    "idle_test"}
# Пакеты-инструменты сборки (приложению обычно не нужны)
TOOL_PACKAGES = {"pip", "setuptools", "wheel", "pkg_resources", "pkg_resources._vendor",
                 "_distutils_hack", "easy_install", "distribute", "setuptools._vendor"}

HARD_STDLIB = {
    "sys", "builtins", "__future__", "os", "io", "abc", "site", "encodings",
    "codecs", "gc", "errno", "signal", "_thread", "time", "math", "array",
    "itertools", "functools", "collections", "struct", "binascii", "marshal",
    "unicodedata", "warnings", "weakref", "types", "keyword", "token",
    "tokenize", "linecache", "copy", "operator", "reprlib", "enum", "stat",
    "posixpath", "ntpath", "genericpath", "msvcrt", "winreg", "winsound",
    "nt", "_winapi", "_socket", "select", "pyexpat", "zlib", "_io", "atexit",
    "faulthandler", "symtable", "opcode", "dis", "inspect", "logging", "json",
    "re", "typing", "typing_extensions", "dataclasses", "subprocess",
    "threading", "queue", "socket", "ssl", "email", "urllib", "http", "xml",
    "csv", "datetime", "calendar", "random", "hashlib", "hmac", "base64",
    "shutil", "tempfile", "pathlib", "glob", "fnmatch", "configparser",
    "argparse", "unittest", "sqlite3", "ctypes", "tkinter", "wave", "audioop",
    "copyreg", "bisect", "heapq", "bz2", "lzma", "pickle", "shelve", "dbm",
    "decimal", "fractions", "numbers", "string", "textwrap", "difflib",
    "gettext", "locale", "platform", "sysconfig", "plistlib", "uuid",
    "ipaddress", "secrets", "contextlib", "contextvars", "asyncio",
    "concurrent", "multiprocessing", "traceback", "pdb", "profile", "cProfile",
    "timeit", "distutils", "ensurepip", "venv", "importlib", "py_compile",
    "compileall", "zipfile", "tarfile", "gzip", "zipapp", "webbrowser",
    "wsgiref", "socketserver", "ftplib", "smtplib", "poplib", "imaplib",
    "turtle", "tkinter", "math", "cmath", "array", "mmap", "resource",
    "termios", "tty", "fcntl", "grp", "pwd", "spwd", "crypt", "ossaudiodev",
    "sunau", "aifc", "chunk", "colorsys", "imghdr", "sndhdr", "ossaudiodev",
    "pipes", "sched", "mimetypes", "binhex", "quopri", "uu", "code", "codeop",
    "compileall", "getopt", "getpass", "shlex", "netrc", "uu", "mailbox",
    "mirror", "nntplib", "telnetlib", "smtpd", "asynchat", "asyncore",
    "select", "selectors", "statistics", "trace", "tracemalloc", "types",
    "typing", "unicodedata", "venv", "wave", "winreg", "winsound", "wsgiref",
    "xdrlib", "zoneinfo", "graphlib", "dataclasses", "ast", "symtable",
    "typing", "unicodedata", "zipapp", "zlib", "zimportlib",
}

PROBE_CODE = (
    "import sys,struct;"
    "print('V=%d.%d.%d' % sys.version_info[:3]);"
    "print('A=%d' % (struct.calcsize('P')*8));"
    "print('P=%s' % sys.base_prefix)"
)


# ------------------------------------------------------------------ мелочи
def log(msg=""):
    print(msg, flush=True)


def say(msg):
    print("  " + msg, flush=True)


def die(msg, code=2):
    print("", flush=True)
    print("[ОШИБКА] " + msg, flush=True)
    print("", flush=True)
    sys.exit(code)


def norm(path):
    return os.path.normpath(os.path.abspath(path))


def human(nbytes):
    if nbytes is None:
        return "?"
    mb = nbytes / 1048576.0
    if mb >= 1024:
        return "%.2f ГБ" % (mb / 1024.0)
    return "%.1f МБ" % mb


def dir_size(path):
    total = 0
    for _root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(_root, f))
            except OSError:
                pass
    return total


def pe_arch(path):
    """32/64 разрядность PE-файла (python.exe)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    if len(head) < 128 or head[:2] != b"MZ":
        return None
    off = int.from_bytes(head[60:64], "little")
    if off + 6 > len(head) or head[off:off + 4] != b"PE\0\0":
        return None
    machine = int.from_bytes(head[off + 4:off + 6], "little")
    return {0x14C: 32, 0x8664: 64}.get(machine)


def probe_python(exe):
    """dict(version, short, arch, prefix, exe) либо None."""
    if not exe or not os.path.isfile(exe):
        return None
    try:
        p = subprocess.run([exe, "-c", PROBE_CODE], capture_output=True,
                           text=True, timeout=90)
    except Exception as exc:                       # noqa: BLE001
        return None
    info = {}
    for line in (p.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()
    if "V" not in info:
        return None
    ver = info["V"]
    return {"exe": exe, "version": ver,
            "short": ".".join(ver.split(".")[:2]),
            "arch": int(info.get("A", "0") or 0),
            "prefix": info.get("P", "")}


def looks_like_venv(path):
    if not os.path.isdir(path):
        return False
    if os.path.isfile(os.path.join(path, "pyvenv.cfg")):
        return True
    if os.path.isdir(os.path.join(path, "Lib", "site-packages")):
        return True
    if os.path.isfile(os.path.join(path, "Scripts", "python.exe")):
        return True
    for d in glob.glob(os.path.join(path, "lib", "python*")):
        if os.path.isdir(os.path.join(d, "site-packages")):
            return True
    return False


# ------------------------------------------------------- venv и версия Python
def read_pyvenv(cfg_path):
    data = {}
    try:
        with open(cfg_path, "r", encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                data[k.strip().lower()] = v.strip()
    except OSError:
        return None
    return data


def find_venv(project, explicit=None):
    """Возвращает (путь_к_venv, как_нашли)."""
    if explicit:
        v = norm(explicit)
        if not looks_like_venv(v):
            die("в указанной папке нет признаков виртуального окружения: %s" % v)
        return v, "задано параметром --venv"

    for name in VENV_NAME_HINTS:
        cand = os.path.join(project, name)
        if looks_like_venv(cand):
            return cand, "стандартное имя папки окружения"

    found = []
    for root, dirs, files in os.walk(project):
        rel = os.path.relpath(root, project)
        if root != project and rel.count(os.sep) >= 2:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in COPY_SKIP_DIRS and not d.startswith(".")]
        if "pyvenv.cfg" in files:
            found.append(root)
    if found:
        found.sort(key=lambda p: (len(p), p))
        return found[0], "найдено по файлу pyvenv.cfg"
    return None, None


def venv_site_packages(venv):
    cand = os.path.join(venv, "Lib", "site-packages")
    if os.path.isdir(cand):
        return cand
    for d in glob.glob(os.path.join(venv, "lib", "python*")):
        c = os.path.join(d, "site-packages")
        if os.path.isdir(c):
            return c
    return cand


def version_from_pycache(project):
    """Версия Python по тегам кэша __pycache__\\*.cpython-310.pyc"""
    tags = {}
    for root, dirs, files in os.walk(project):
        rel = os.path.relpath(root, project)
        if root != project and rel.count(os.sep) > 3:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in COPY_SKIP_DIRS]
        for f in files:
            m = re.match(r"^.+\.cpython-(\d{2,3})\.pyc$", f)
            if m:
                tag = m.group(1)
                tags[tag] = tags.get(tag, 0) + 1
    if not tags:
        return None
    tag = sorted(tags.items(), key=lambda kv: (-kv[1], -int(kv[0])))[0][0]
    return tag[0] + "." + tag[1:]


def venv_dir_names(project, venv):
    """Имена папок внутри проекта, которые являются окружениями Python."""
    names = set()
    if venv:
        rel = os.path.relpath(venv, project)
        if rel and rel != ".":
            names.add(rel.split(os.sep)[0])
    for root, dirs, files in os.walk(project):
        rel = os.path.relpath(root, project)
        if root != project and rel.count(os.sep) >= 3:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in COPY_SKIP_DIRS]
        for d in list(dirs):
            if looks_like_venv(os.path.join(root, d)):
                names.add(d)
                dirs.remove(d)
    return names


def norm_version(text):
    """'3.7.0.final.0' -> ('3.7', '3.7.0')"""
    if not text:
        return None, None
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text))
    if not m:
        return None, None
    short = "%s.%s" % (m.group(1), m.group(2))
    full = "%s.%s" % (short, m.group(3) or "0")
    return short, full


# ------------------------------------------------ поиск подходящего Python
def candidate_interpreters(project, venv, cfg, want_short, want_arch, explicit):
    """Список кандидатов. Обычный путь - python.exe; для py-lauchер - 'EXE||-3.10-64'."""
    out = []

    def push(path):
        if path and path not in out:
            out.append(path)

    if explicit:
        p = norm(explicit)
        if os.path.isdir(p):
            push(os.path.join(p, "python.exe"))
        else:
            push(p)

    for key in ("base-executable", "home", "base-prefix", "base-exec-prefix"):
        val = (cfg or {}).get(key)
        if not val:
            continue
        val = val.strip('"')
        if val.lower().endswith(("python.exe", "pythonw.exe")):
            push(val)
        elif os.path.isdir(val):
            push(os.path.join(val, "python.exe"))

    if venv:
        push(os.path.join(venv, "Scripts", "python.exe"))

    py = shutil.which("py")
    if py and want_short:
        for suffix in ("", "-64", "-32"):
            push("%s||-%s%s" % (py, want_short, suffix))
    elif py:
        push("%s||-3" % py)

    push(shutil.which("python"))
    push(shutil.which("python3"))

    roots = []
    drive = os.environ.get("SystemDrive", "C:") + os.sep
    roots += glob.glob(drive + "Python*")
    roots += glob.glob(drive + "Python" + os.sep + "Python*")
    for env_key, default in (("ProgramFiles", r"C:\Program Files"),
                             ("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        pf = os.environ.get(env_key) or default
        roots += glob.glob(os.path.join(pf, "Python*"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots += glob.glob(os.path.join(local, "Programs", "Python", "Python*"))
        roots += glob.glob(os.path.join(local, "Programs", "Python Software", "*"))
    for r in sorted(set(roots), key=len, reverse=True):
        if not os.path.isdir(r):
            continue
        name = os.path.basename(r)
        if not re.match(r"(?i)^python\d", name):
            continue
        digits = re.sub(r"\D", "", name)
        if want_short:
            want = want_short.replace(".", "")
            if not digits.startswith(want) and want not in digits:
                continue
        push(os.path.join(r, "python.exe"))
    return out


def find_runtime(want_short, want_arch, project, venv, cfg, explicit):
    checked = []
    mismatch = []
    for cand in candidate_interpreters(project, venv, cfg, want_short, want_arch, explicit):
        if "||" in cand:
            exe, flag = cand.split("||", 1)
            try:
                p = subprocess.run([exe, flag, "-c", PROBE_CODE],
                                   capture_output=True, text=True, timeout=90)
            except Exception:                      # noqa: BLE001
                continue
            prefix = None
            for line in (p.stdout or "").splitlines():
                if line.startswith("P="):
                    prefix = line[2:].strip()
            if not prefix:
                continue
            info = probe_python(os.path.join(prefix, "python.exe"))
            label = "%s (%s)" % (prefix, flag)
        else:
            info = probe_python(cand)
            label = cand
        if not info:
            continue
        checked.append((label, info))
        if want_short and info["short"] != want_short:
            mismatch.append((label, "версия %s, нужна %s" % (info["version"], want_short)))
            continue
        if want_arch and info["arch"] != want_arch:
            mismatch.append((label, "%d-бит, нужна %d-бит" % (info["arch"], want_arch)))
            continue
        if not os.path.isdir(os.path.join(info["prefix"], "Lib")):
            mismatch.append((label, "нет стандартной библиотеки (Lib) - это не полная установка Python"))
            continue
        return info, checked, mismatch
    return None, checked, mismatch


# ------------------------------------------------------------ копирование
def copy_tree(src, dst, skip_dirs=(), skip_files=(), skip_venv_dirs=True,
              verbose_missing=False):
    """Рекурсивно копирует src -> dst (перезаписывая существующее)."""
    skip_dirs = set(skip_dirs)
    skip_files = set(skip_files)
    n_files = 0
    n_bytes = 0
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        keep = []
        for d in dirs:
            if d in skip_dirs or d in COPY_SKIP_DIRS or d in skip_files:
                continue
            if skip_venv_dirs and d in VENV_NAME_HINTS and \
                    looks_like_venv(os.path.join(root, d)):
                continue
            keep.append(d)
        dirs[:] = sorted(keep)
        target = dst if rel == "." else os.path.join(dst, rel)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as exc:
            if verbose_missing:
                say("не могу создать %s (%s)" % (target, exc))
            continue
        for f in sorted(files):
            if f in skip_files:
                continue
            if any(fnmatch.fnmatch(f.lower(), pat) for pat in COPY_SKIP_FILE_PATTERNS):
                continue
            s = os.path.join(root, f)
            d = os.path.join(target, f)
            try:
                shutil.copyfile(s, d)
                try:
                    shutil.copystat(s, d)
                except OSError:
                    pass
                n_files += 1
                try:
                    n_bytes += os.path.getsize(s)
                except OSError:
                    pass
            except OSError as exc:
                say("пропущен файл (ошибка чтения): %s (%s)" % (s, exc))
    return n_files, n_bytes


# -------------------------------------------------- разбор пакетов окружения
def site_index(site_dir):
    """dist-пакеты venv: {ключ: {dist_name, version, toplevels, requires, dir}}"""
    index = {}
    loose = []
    if not os.path.isdir(site_dir):
        return index, loose
    entries = sorted(os.scandir(site_dir), key=lambda e: e.name)
    for e in entries:
        name = e.name
        if name == "__pycache__":
            continue
        if not e.is_dir():
            if name.endswith(".py") or name.endswith(".pth"):
                loose.append(name)
            continue
        if name.endswith(".dist-info") or name.endswith(".egg-info"):
            meta = {"dist_name": name.split("-")[0], "version": "?",
                    "toplevels": [], "requires": [], "dir": e.path,
                    "distinfo": name}
            m = re.match(r"^(.+?)-([^-]+(?:\.[^-]+)*)-(?:dist|egg)-info$", name)
            if m:
                meta["dist_name"] = m.group(1)
                meta["version"] = m.group(2)
            for metafile in ("METADATA", "PKG-INFO"):
                path = os.path.join(e.path, metafile)
                if os.path.isfile(path):
                    try:
                        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                            for line in fh:
                                if line.startswith("Name: "):
                                    meta["dist_name"] = line[6:].strip()
                                elif line.startswith("Version: "):
                                    meta["version"] = line[7:].strip()
                                elif line.startswith("Requires-Dist: "):
                                    req = re.split(r"[;(<>=!\[\]]", line[13:].strip())[0]
                                    req = req.strip()
                                    if req:
                                        meta["requires"].append(req)
                    except OSError:
                        pass
                    break
            top = os.path.join(e.path, "top_level.txt")
            if os.path.isfile(top):
                try:
                    with open(top, "r", encoding="utf-8-sig", errors="replace") as fh:
                        meta["toplevels"] = [x.strip() for x in fh if x.strip()]
                except OSError:
                    pass
            if not meta["toplevels"]:
                guess = meta["dist_name"].replace("-", "_")
                for cand in (guess, guess.lower()):
                    if os.path.exists(os.path.join(site_dir, cand)):
                        meta["toplevels"] = [cand]
                        break
            key = meta["dist_name"].lower().replace("-", "_")
            index[key] = meta
        else:
            loose.append(name)
    # привязываем свободные модули к их dist-info, если имя совпадает
    for name in loose:
        stem = re.sub(r"\.(py|pth)$", "", name)
        key = stem.lower().replace("-", "_")
        if key in index and stem not in index[key]["toplevels"]:
            index[key]["toplevels"].append(stem)
    return index, loose


def module_to_dist(site_dir, index, mod):
    key = mod.lower().replace("-", "_")
    meta = index.get(key)
    if meta:
        return meta
    for meta in index.values():
        if mod in meta["toplevels"]:
            return meta
        if key == meta["dist_name"].lower().replace("-", "_"):
            return meta
    if site_dir and (os.path.exists(os.path.join(site_dir, mod))
                     or os.path.exists(os.path.join(site_dir, mod + ".py"))):
        return {"dist_name": mod, "version": "?(метаданных нет)", "toplevels": [mod],
                "requires": [], "dir": "", "distinfo": ""}
    return None


def stdlib_names(base_prefix, builtin_names=()):
    names = set(HARD_STDLIB)
    names |= {b for b in builtin_names if b}
    lib = os.path.join(base_prefix, "Lib")
    if os.path.isdir(lib):
        try:
            for e in os.scandir(lib):
                if e.name.startswith("__") or e.name == "site-packages":
                    continue
                if e.is_dir():
                    if os.path.isfile(os.path.join(e.path, "__init__.py")):
                        names.add(e.name)
                elif e.name.endswith(".py"):
                    names.add(e.name[:-3])
        except OSError:
            pass
    dlls = os.path.join(base_prefix, "DLLs")
    if os.path.isdir(dlls):
        try:
            for e in os.scandir(dlls):
                if e.name.endswith(".pyd"):
                    names.add(e.name.split(".")[0])
        except OSError:
            pass
    return names


IMPORT_RE = re.compile(r"^[ \t]*(?:from[ \t]+|import[ \t]+)([A-Za-z_][\w\.]*)", re.M)


def scan_imports(files):
    """Множество импортируемых модулей верхнего уровня + предупреждения."""
    mods = set()
    warns = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            warns.append("этот Python не смог разобрать %s (сборка ведётся запасным способом)" % path)
            for m in IMPORT_RE.finditer(src):
                mods.add(m.group(1).split(".")[0])
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if getattr(node, "level", 0) == 0 and node.module:
                    mods.add(node.module.split(".")[0])
    return mods, warns


def project_py_files(project, skip_venvs=()):
    skip_venvs = set(skip_venvs)
    out = []
    for root, dirs, files in os.walk(project):
        rel = os.path.relpath(root, project)
        if root != project and rel.count(os.sep) > 6:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs
                   if d not in COPY_SKIP_DIRS and d not in skip_venvs and d != "site-packages"
                   and not (d in VENV_NAME_HINTS and looks_like_venv(os.path.join(root, d)))]
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.join(root, f))
    return out


def find_entries(project, files):
    """Файлы-точки входа (с if __name__ == '__main__')."""
    cands = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                src = fh.read()
        except OSError:
            continue
        if re.search(r"if\s+__name__\s*==\s*['\"]__main__['\"]\s*:", src):
            cands.append(os.path.relpath(path, project).replace("\\", "/"))
    stem = os.path.basename(project).lower().replace("-", "_").replace(" ", "_")

    def rank(rel):
        base = os.path.basename(rel).lower()
        score = 0
        if base == "main.py":
            score -= 100
        if os.path.splitext(base)[0] == stem:
            score -= 60
        if base in ("app.py", "run.py", "start.py", "launcher.py", "gui.py",
                    "main_gui.py", "index.py", "program.py"):
            score -= 40
        score += rel.count("/") * 3
        return (score, len(rel), rel)

    cands.sort(key=rank)
    return cands


def parse_requirements(project):
    res = []
    for name in ("requirements.txt", "requirements-dev.txt", "requirements-full.txt"):
        path = os.path.join(project, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
                for line in fh:
                    line = line.split("#")[0].strip()
                    if not line or line.startswith("-"):
                        continue
                    m = re.match(r"^\[?([A-Za-z0-9_.\-]+)", line)
                    if m:
                        res.append((name, m.group(1)))
        except OSError:
            pass
    return res


def needed_closure(project_mods, index, site_dir):
    """Проектные импорты + их объявленные зависимости -> нужные имена пакетов/модулей."""
    needed = set()
    queue = []
    seen = set()
    for m in project_mods:
        meta = module_to_dist(site_dir, index, m)
        if meta:
            needed.update(meta["toplevels"])
            needed.add(m)
            key = meta["dist_name"].lower().replace("-", "_")
            if key not in seen:
                seen.add(key)
                queue.append(meta)
    by_name = {k: v for k, v in index.items()}
    guard = 0
    while queue and guard < 800:
        guard += 1
        meta = queue.pop()
        for req in meta.get("requires", []):
            key = req.lower().replace("-", "_")
            sub = by_name.get(key)
            if sub and key not in seen:
                seen.add(key)
                needed.update(sub["toplevels"])
                needed.add(sub["dist_name"].lower().replace("-", "_"))
                queue.append(sub)
    return needed


# ------------------------------------------------------------- перенос runtime
def copy_runtime(base_prefix, dst_runtime, site_dir, copy_base_site=False,
                 lean_needed=None, index=None, extra_site_dirs=()):
    os.makedirs(dst_runtime, exist_ok=True)
    total_files = 0
    total_bytes = 0
    skipped = []

    # корень установки: python.exe, pythonw.exe, pythonXX.dll, vcruntime...dll
    for e in sorted(os.scandir(base_prefix), key=lambda x: x.name.lower()):
        if not e.is_file():
            continue
        keep = (e.name.lower() in ("python.exe", "pythonw.exe", "license.txt")
                or e.name.lower().endswith(".dll")
                or e.name.lower() == "python.exe.manifest")
        if keep:
            shutil.copyfile(e.path, os.path.join(dst_runtime, e.name))
            total_files += 1
            total_bytes += os.path.getsize(e.path)

    # DLLs и tcl/tk
    for name in ("DLLs", "tcl"):
        src = os.path.join(base_prefix, name)
        if os.path.isdir(src):
            c, b = copy_tree(src, os.path.join(dst_runtime, name))
            total_files += c
            total_bytes += b

    # стандартная библиотека Lib (site-packages добавим отдельно)
    lib_src = os.path.join(base_prefix, "Lib")
    lib_dst = os.path.join(dst_runtime, "Lib")
    if os.path.isdir(lib_src):
        c, b = copy_tree(lib_src, lib_dst, skip_dirs=STDLIB_SKIP_DIRS)
        total_files += c
        total_bytes += b

    dst_site = os.path.join(lib_dst, "site-packages")
    os.makedirs(dst_site, exist_ok=True)

    if copy_base_site and os.path.isdir(os.path.join(base_prefix, "Lib", "site-packages")):
        c, b = copy_tree(os.path.join(base_prefix, "Lib", "site-packages"), dst_site)
        total_files += c
        total_bytes += b
        say("venv использовал системные пакеты - копирую и их")

    for extra in extra_site_dirs:
        if os.path.isdir(extra):
            c, b = copy_tree(extra, dst_site)
            total_files += c
            total_bytes += b

    if site_dir and os.path.isdir(site_dir):
        skip_names = {"__pycache__"}
        if lean_needed is not None:
            keep_distinfo, keep_modules = set(), set()
            drop_distinfo, drop_modules = set(), set()
            metas = list((index or {}).values())
            for meta in metas:
                key = meta["dist_name"].lower().replace("-", "_")
                needed = (key in lean_needed) or any(t in lean_needed for t in meta["toplevels"])
                if needed:
                    keep_distinfo.add(meta["distinfo"])
                    keep_modules.update(meta["toplevels"])
                    keep_modules.add(meta["dist_name"].replace("-", "_"))
                else:
                    drop_distinfo.add(meta["distinfo"])
                    drop_modules.update(meta["toplevels"])
            for meta in metas:
                if meta["dist_name"].lower().replace("-", "_") in TOOL_PACKAGES:
                    drop_distinfo.add(meta["distinfo"])
            drop_modules -= keep_modules
            for e in sorted(os.scandir(site_dir), key=lambda x: x.name.lower()):
                name = e.name
                if name.endswith(".dist-info") or name.endswith(".egg-info"):
                    if name in drop_distinfo:
                        skip_names.add(name)
                        skipped.append(name)
                elif name in drop_modules:
                    skip_names.add(name)
                    skipped.append(name)
                elif name.split(".")[0] in TOOL_PACKAGES and name.split(".")[0] not in lean_needed:
                    skip_names.add(name)
                    skipped.append(name)
            # .pth-файл setuptools ссылается на _distutils_hack: убирать их надо вместе
            if "_distutils_hack" in skip_names:
                skip_names.add("distutils-precedence.pth")
        c, b = copy_tree(site_dir, dst_site, skip_files=skip_names)
        total_files += c
        total_bytes += b
    return total_files, total_bytes, skipped


# --------------------------------------------------------------- запускатели
def reserve_name(out_dir, name, moved):
    """Освободить имя для нашего файла: файл проекта с таким же именем сохраняем как .orig.txt.

    Нужно на случай, если в проекте уже есть start.bat (он вызывает venv и в
    портативной сборке работать не будет). Ничего не удаляем молча.
    """
    lower = name.lower()
    try:
        entries = list(os.scandir(out_dir))
    except OSError:
        return
    for e in entries:
        if e.is_file() and e.name.lower() == lower:
            backup = e.name + ".orig.txt"
            try:
                os.replace(e.path, os.path.join(out_dir, backup))
                moved.append((e.name, backup))
            except OSError:
                pass


def write_start_bat(out_dir, entry, all_entries, gui=False, moved=None):
    lines = [
        "@echo off",
        "setlocal EnableExtensions",
        "rem ==============================================================",
        "rem  Portable launcher (built by make_portable.py v%s)" % TOOL_VERSION,
        "rem  This folder is self-contained: no Python and no Internet are",
        "rem  needed on this PC. Copy or rename the whole folder freely -",
        "rem  paths inside are resolved from the .bat location, not fixed.",
        "rem",
        "rem  Entry file used:  %s" % entry,
        "rem  Run another script:  Start.bat  other_file.py",
    ]
    if len(all_entries) > 1:
        lines.append("rem  Other entry files found in this project: " + ", ".join(all_entries[1:]))
    if gui:
        lines.append("rem  Start_gui.bat = same program without a console window.")
    lines += [
        "rem ==============================================================",
        'set "ROOT=%~dp0"',
        'set "ENTRY=%s"' % entry,
        'set "PYEXE=%ROOT%runtime\\pythonw.exe"' if gui else 'set "PYEXE=%ROOT%runtime\\python.exe"',
        "",
        'if not exist "%PYEXE%" set "PYEXE=%ROOT%runtime\\python.exe"',
        'if not exist "%PYEXE%" (',
        '  echo [ERROR] runtime\\python.exe not found next to this .bat',
        '  echo The "runtime" folder must stay in the same folder as this launcher.',
        "  pause",
        "  exit /b 1",
        ")",
        'if not "%~1"=="" set "ENTRY=%~1"',
        'if not exist "%ROOT%%ENTRY%" (',
        "  echo [ERROR] entry file not found",
        "  echo.",
        "  echo Files in this folder:",
        "  dir /b /a-d",
        "  echo.",
        '  echo Usage:  Start.bat  path\\to\\script.py',
        "  pause",
        "  exit /b 1",
        ")",
        "rem --- isolation: ignore site-packages and PYTHON* variables of the target PC",
        'set "PYTHONNOUSERSITE=1"',
        'set "PYTHONPATH="',
        'set "PYTHONHOME="',
        'cd /d "%ROOT%"',
        '"%PYEXE%" "%ENTRY%" %2 %3 %4 %5 %6 %7 %8 %9',
        'set "RC=%ERRORLEVEL%"',
        'if not "%RC%"=="0" (',
        "  echo.",
        '  echo [INFO] program finished with exit code %RC%',
        "  pause",
        ")",
        "exit /b %RC%",
    ]
    name = "Start_gui.bat" if gui else "Start.bat"
    if moved is not None:
        reserve_name(out_dir, name, moved)
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="ascii", errors="replace", newline="\r\n") as fh:
        fh.write("\r\n".join(lines) + "\r\n")
    return name


def write_extra_entry_bats(out_dir, entries, moved=None):
    made = []
    for rel in entries[1:]:
        stem = os.path.splitext(os.path.basename(rel))[0]
        safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", stem) or "entry"
        name = "Run_%s.bat" % safe
        if moved is not None:
            reserve_name(out_dir, name, moved)
        with open(os.path.join(out_dir, name), "w", encoding="ascii",
                  errors="replace", newline="\r\n") as fh:
            body = ("@echo off\r\n"
                    'if exist "%~dp0Start_gui.bat" goto GUI\r\n'
                    'call "%~dp0Start.bat" "' + rel + '" %*\r\n'
                    "goto END\r\n"
                    ":GUI\r\n"
                    'call "%~dp0Start_gui.bat" "' + rel + '" %*\r\n'
                    ":END\r\n"
                    "exit /b %ERRORLEVEL%\r\n")
            fh.write(body)
        made.append(name)
    return made


# ------------------------------------------------------------------ проверка
TEST_TEMPLATE = """import sys, os
RUNTIME = __RUNTIME__
CWD = __CWD__
MODS = __MODS__
print('PY     = ' + sys.version.split()[0] + ' (' + ('64' if sys.maxsize > 2**31 else '32') + '-bit)')
print('EXE    = ' + sys.executable)
print('PREFIX = ' + sys.prefix)
rt = os.path.normcase(os.path.abspath(RUNTIME))
cwdn = os.path.normcase(os.path.abspath(CWD))
leaks = []
for p in sys.path:
    if not p:
        continue
    np = os.path.normcase(os.path.abspath(p))
    if np.startswith(rt) or np.startswith(cwdn):
        continue
    leaks.append(p)
print('PATH_LEAKS = ' + (' | '.join(leaks) if leaks else 'none (полностью автономно)'))
bad = []
for m in MODS:
    try:
        __import__(m)
    except Exception as e:
        bad.append(m + ' -> ' + type(e).__name__ + ': ' + str(e))
print('IMPORTS = ' + (', '.join(MODS) if MODS else '(нечего проверять)'))
print('IMPORT_ERRORS = ' + (' || '.join(bad) if bad else 'нет'))
sys.exit(1 if (bad or leaks) else 0)
"""


def test_runtime(runtime_dir, mods, cwd, label):
    exe = os.path.join(runtime_dir, "python.exe")
    if not os.path.isfile(exe):
        say("[ПРОВАЛ] %s: нет %s" % (label, exe))
        return False
    code = (TEST_TEMPLATE
            .replace("__RUNTIME__", repr(runtime_dir))
            .replace("__CWD__", repr(cwd))
            .replace("__MODS__", repr(sorted(mods))))
    env = dict(os.environ)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONPATH"] = ""
    env["PYTHONHOME"] = ""
    try:
        p = subprocess.run([exe, "-c", code], capture_output=True, text=True,
                           timeout=300, cwd=cwd, env=env)
    except Exception as exc:                       # noqa: BLE001
        say("[ПРОВАЛ] %s: %s" % (label, exc))
        return False
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    ok = (p.returncode == 0)
    say("[%s] %s" % ("OK" if ok else "ПРОБЛЕМА", label))
    for line in out.splitlines():
        line = line.strip()
        if line:
            say("   " + line)
    return ok


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(
        description="Портативная сборка проекта из PyCharm (переносится на любой ПК).")
    ap.add_argument("project", nargs="?", help="папка проекта из PyCharm")
    ap.add_argument("--out", help="куда собрать (по умолчанию рядом с проектом: <Проект>_Portable)")
    ap.add_argument("--venv", help="папка окружения, если она называется не venv")
    ap.add_argument("--runtime", help="путь к нужному Python (папка установки или python.exe)")
    ap.add_argument("--python", help="требуемая версия Python, напр. --python 3.10")
    ap.add_argument("--entry", action="append", default=[],
                    help="стартовый .py файл (можно несколько; первый - основной)")
    ap.add_argument("--lean", action="store_true",
                    help="экономия места: не копировать пакеты venv, которые нигде не импортируются")
    ap.add_argument("--overwrite", action="store_true", help="перезаписать готовую сборку")
    ap.add_argument("--no-test", action="store_true", help="не проверять собранную сборку")
    ap.add_argument("--no-relocate", action="store_true",
                    help="не проверять перенос (копирование всей сборки во временную папку)")
    args = ap.parse_args()

    print("=" * 70)
    print("ПОРТАТИВНАЯ СБОРКА ПРОЕКТА   |   make_portable.py v%s" % TOOL_VERSION)
    print("=" * 70)

    # ---------------- входная папка проекта
    project = args.project or ""
    if not project:
        try:
            project = input("Введите папку проекта (из PyCharm): ").strip().strip('"')
        except EOFError:
            project = ""
    if not project:
        die("папка проекта не указана")
    project = norm(project)
    if not os.path.isdir(project):
        die("папка не найдена: %s" % project)
    name = os.path.basename(project)
    say("проект:    %s" % project)

    out_dir = norm(args.out) if args.out else norm(os.path.join(os.path.dirname(project),
                                                                name + "_Portable"))
    if out_dir == project or out_dir.startswith(project + os.sep):
        die("папка результата не может находиться внутри проекта: %s" % out_dir)
    say("результат: %s" % out_dir)

    if os.path.isdir(out_dir):
        if not args.overwrite:
            if sys.stdin.isatty():
                ans = input("Такая папка уже есть. Пересобрать поверх неё? (y/N): ").strip().lower()
                if ans not in ("y", "yes", "д", "да"):
                    die("отменено: удалите %s или запустите с --overwrite" % out_dir)
            else:
                die("папка %s уже существует (нужен --overwrite)" % out_dir)
        say("удаляю предыдущую сборку ...")
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    # ---------------- окружение проекта
    venv, how = find_venv(project, args.venv)
    site_dir = venv_site_packages(venv) if venv else None
    cfg = read_pyvenv(os.path.join(venv, "pyvenv.cfg")) if venv else None
    if venv:
        say("окружение: %s  (%s)" % (venv, how))
        if cfg:
            say("pyvenv.cfg: home=%s | version=%s | системные пакеты=%s"
                % (cfg.get("home", "?"), cfg.get("version_info") or cfg.get("version", "?"),
                   cfg.get("include-system-site-packages", "?")))
    else:
        say("окружения (venv) в проекте нет - возьму Python из системы этого компьютера")

    skip_venvs = venv_dir_names(project, venv)

    # ---------------- версия и разрядность Python
    ver_short = ver_full = None
    if cfg:
        ver_short, ver_full = norm_version(cfg.get("version_info") or cfg.get("version"))
    if not ver_short:
        ver_short = version_from_pycache(project)
        if ver_short:
            ver_short, ver_full = norm_version(ver_short)
            say("версия Python определена по кэшу __pycache__: %s" % ver_short)
    if args.python:
        s, f = norm_version(args.python)
        if not s:
            die("не понял версию Python: %s" % args.python)
        ver_short, ver_full = s, f
        say("версия Python задана вручную: %s" % ver_short)
    if not ver_short:
        die("не удалось определить версию Python проекта - укажите её, например --python 3.10")

    arch = None
    if venv:
        for exe in ("python.exe", "pythonw.exe"):
            arch = pe_arch(os.path.join(venv, "Scripts", exe))
            if arch:
                break
    say("нужен Python: %s%s" % (ver_full or ver_short,
                                (" (%d-бит)" % arch) if arch else " (разрядность возьму от найденного Python)"))

    # ---------------- поиск нужного интерпретатора
    say("ищу такой Python на этом компьютере ...")
    runtime, checked, mismatch = find_runtime(ver_short, arch, project, venv, cfg, args.runtime)
    if not runtime:
        print("")
        print("На этом компьютере нет того Python, из которого создано окружение проекта:")
        print("нужна версия %s%s." % (ver_full or ver_short, (" (%d-бит)" % arch) if arch else ""))
        if checked:
            print("Проверенные интерпретаторы:")
            for label, info in checked[:10]:
                print("   %-52s %s (%d-бит)" % (label, info["version"], info["arch"]))
        if mismatch:
            print("Не подошли:")
            for label, why in mismatch[:10]:
                print("   %-52s %s" % (label, why))
        print("")
        die("установите Python %s нужной разрядности и повторите сборку, "
            "или укажите его вручную: --runtime C:\\Python%s"
            % (ver_full or ver_short, ver_short.replace(".", "")))
    say("найден: Python %s (%d-бит), установка %s"
        % (runtime["version"], runtime["arch"], runtime["prefix"]))
    base_prefix = runtime["prefix"]
    arch = runtime["arch"] or arch

    # ---------------- анализ проекта
    py_files = project_py_files(project, skip_venvs)
    if not py_files:
        die("в проекте нет ни одного .py файла")
    mods, warns = scan_imports(py_files)
    index, loose = site_index(site_dir) if site_dir else ({}, [])
    stdlib = stdlib_names(base_prefix)
    local_modules = {os.path.splitext(os.path.basename(p))[0] for p in py_files}
    # локальные пакеты: папки проекта, в которых лежат .py файлы
    for _r, _dirs, _files in os.walk(project):
        _rel = os.path.relpath(_r, project)
        if _r != project and _rel.count(os.sep) >= 2:
            _dirs[:] = []
            continue
        _dirs[:] = [d for d in _dirs
                    if d not in COPY_SKIP_DIRS and d not in skip_venvs and d != "site-packages"
                    and not (d in VENV_NAME_HINTS and looks_like_venv(os.path.join(_r, d)))]
        for _d in _dirs:
            try:
                names = [e.name for e in os.scandir(os.path.join(_r, _d))]
            except OSError:
                continue
            if "__init__.py" in names or any(n.endswith(".py") for n in names):
                local_modules.add(_d)

    third = []
    unknown = []
    std_used = []
    for m in sorted(mods):
        if m in local_modules:
            continue
        meta = module_to_dist(site_dir, index, m) if site_dir else None
        if meta:
            third.append((m, meta["dist_name"], meta["version"]))
        elif m in stdlib:
            std_used.append(m)
        elif site_dir and os.path.exists(os.path.join(base_prefix, "Lib", "site-packages", m)):
            third.append((m, m, "(установлен вне venv)"))
        else:
            unknown.append(m)

    entries = list(args.entry) or find_entries(project, py_files)
    entries = [e.replace("\\", "/") for e in entries]
    for e in list(entries):
        if not os.path.isfile(os.path.join(project, e.replace("/", os.sep))):
            die("стартовый файл не найден в проекте: %s" % e)
    if not entries:
        die("не найден файл с `if __name__ == \"__main__\":` - укажите стартовый: --entry main.py")

    reqs = parse_requirements(project)
    if reqs:
        have = {d.lower().replace("-", "_") for _m, d, _v in third}
        have |= {k for k in index}
        missing = sorted({n for _f, n in reqs if n.lower().replace("-", "_") not in have})
        say("requirements.txt: " + ", ".join(sorted({n for _f, n in reqs})))
        if missing:
            say("ВНИМАНИЕ: в requirements.txt есть, но в окружении не установлено: "
                + ", ".join(missing))
    if unknown:
        say("ВНИМАНИЕ: импортируется, но не найдено в окружении проекта: " + ", ".join(unknown))
    for w in warns:
        say("предупреждение: " + w)

    print("")
    print("--- что нашёл в проекте ---")
    say(".py файлов: %d" % len(py_files))
    say("стартовый файл по умолчанию: %s" % entries[0])
    if len(entries) > 1:
        say("найденные другие точки входа (будут отдельные .bat): " + ", ".join(entries[1:]))
    if third:
        say("сторонние пакеты (из окружения проекта):")
        shown = set()
        for _m, dist, ver in third:
            if dist in shown:
                continue
            shown.add(dist)
            say("   %-26s %s" % (dist, ver))
    else:
        say("сторонних пакетов нет - только стандартная библиотека Python")
    if std_used:
        say("стандартная библиотека Python используется: %d модулей" % len(std_used))

    lean_needed = needed_closure(mods, index, site_dir) if (site_dir and args.lean) else None

    # ---------------- копируем проект
    print("")
    print("--- копирую проект ---")
    if skip_venvs:
        say("папки окружения Python не копируются: " + ", ".join(sorted(skip_venvs)))
    c1, b1 = copy_tree(project, out_dir, skip_dirs=skip_venvs)
    say("исходники проекта: %d файлов, %s" % (c1, human(b1)))

    # ---------------- копируем Python и его пакеты
    print("")
    print("--- укладываю Python и пакеты в runtime\\ ---")
    copy_base_site = bool(cfg and str(cfg.get("include-system-site-packages", "false")).lower() == "true")
    extra_site = []
    if not copy_base_site and unknown:
        base_site = os.path.join(base_prefix, "Lib", "site-packages")
        if os.path.isdir(base_site):
            for m in unknown:
                for cand in (m, m.replace("-", "_"), m + ".py"):
                    if os.path.exists(os.path.join(base_site, cand)):
                        extra_site.append(base_site)
                        say("пакет '%s' найден в системном site-packages - переношу и его" % m)
                        break
        extra_site = list(dict.fromkeys(extra_site))
    c2, b2, skipped = copy_runtime(base_prefix, os.path.join(out_dir, "runtime"),
                                   site_dir, copy_base_site=copy_base_site,
                                   lean_needed=lean_needed, index=index,
                                   extra_site_dirs=extra_site)
    say("Python + пакеты: %d файлов, %s" % (c2, b2 and human(b2)))
    if skipped:
        say("--lean: не перенесено %d пакетов: %s" % (len(skipped), ", ".join(skipped[:20])))
    say("пакеты: %s" % ("копирую только нужные (--lean)" if args.lean
                        else "копирую все пакеты окружения (надёжный режим)"))

    # ---------------- запускатели
    print("")
    print("--- создаю запускатели ---")
    gui_app = bool({"tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "kivy",
                    "pygame", "pyglet", "arcade", "glfw", "PyQt4", "TkinterDnD2",
                    "tkinterdnd2"} & set(mods))
    moved = []
    start_main = write_start_bat(out_dir, entries[0], entries, gui=False, moved=moved)
    say("%s  - запуск с консолью (видно ошибки и вывод)" % start_main)
    start_gui = None
    if gui_app:
        start_gui = write_start_bat(out_dir, entries[0], entries, gui=True, moved=moved)
        say("%s  - тот же запуск без окна консоли" % start_gui)
    extra_bats = write_extra_entry_bats(out_dir, entries, moved=moved)
    for b in extra_bats:
        say("%s  - запуск этого файла" % b)
    for old, backup in moved:
        say("ВНИМАНИЕ: в проекте уже был файл %s (он обращается к venv и здесь работать не будет). "
            "Он сохранён как %s, а рядом лежит новый запускатель." % (old, backup))

    # ---------------- проверка
    print("")
    print("--- проверка готовой сборки ---")
    runtime_dir = os.path.join(out_dir, "runtime")
    test_mods = sorted({m for m, _d, _v in third}
                       | {m for m in std_used if m in ("tkinter", "sqlite3", "ctypes", "ssl",
                                                       "zlib", "multiprocessing", "xml", "email")})
    test_ok = True
    if args.no_test:
        say("проверка пропущена (--no-test)")
    else:
        other_cwd = os.environ.get("SystemRoot", r"C:\Windows")
        test_ok &= test_runtime(runtime_dir, test_mods, other_cwd,
                                "запуск из другой папки этого ПК")
        if not args.no_relocate:
            tmp_root = os.path.join(os.environ.get("TEMP") or os.environ.get("TMP") or project,
                                    "portable_relocate_test")
            shutil.rmtree(tmp_root, ignore_errors=True)
            os.makedirs(tmp_root)
            copy_dst = os.path.join(tmp_root, os.path.basename(out_dir))
            say("эмуляция переноса: копирую всю сборку в %s" % tmp_root)
            t0 = time.time()
            copy_tree(out_dir, copy_dst)
            say("копирование заняло %.1f с" % (time.time() - t0))
            test_ok &= test_runtime(os.path.join(copy_dst, "runtime"), test_mods,
                                    tmp_root, "запуск из перенесённой копии сборки")
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            say("проверка переноса пропущена (--no-relocate)")

    # ---------------- отчёт-инструкция
    reserve_name(out_dir, "PORTABLE_INFO.txt", moved)
    total = dir_size(out_dir)
    lines = []
    lines.append("ПОРТАТИВНАЯ СБОРКА: %s" % os.path.basename(out_dir))
    lines.append("=" * 66)
    lines.append("Собрано: %s   (make_portable.py v%s)"
                 % (time.strftime("%d.%m.%Y %H:%M:%S"), TOOL_VERSION))
    lines.append("Исходный проект: %s" % project)
    lines.append("")
    lines.append("КАК ЗАПУСКАТЬ (ничего ставить не нужно)")
    lines.append("  1. Скопируйте ВСЮ эту папку куда угодно: флешка, другой компьютер,")
    lines.append("     любая папка. Папку можно переименовывать.")
    lines.append("  2. Дважды кликните Start.bat - программа запустится сама,")
    lines.append("     никакого Python и интернета на этом компьютере не нужно.")
    if start_gui:
        lines.append("     Start_gui.bat  - то же самое, но без окна консоли.")
    if extra_bats:
        lines.append("     Отдельные запускатели для других файлов: " + ", ".join(extra_bats))
    if moved:
        lines.append("")
        lines.append("  ВНИМАНИЕ про старые файлы проекта (молча ничего не удалено):")
        for old, backup in moved:
            lines.append("    %-24s сохранён как %s  (старый файл обращается к venv и"
                         % (old, backup))
            lines.append("    здесь работать не будет; для запуска используйте Start.bat)")
    lines.append("  3. Если стартовый файл нужен другой:")
    lines.append("        Start.bat  имя_файла.py")
    lines.append("     (например Start.bat  Crop4.py  или  Start.bat  src\\main.py)")
    lines.append("")
    lines.append("ЧТО ВНУТРИ ПАПКИ")
    lines.append("  Start.bat              запуск программы")
    if start_gui:
        lines.append("  Start_gui.bat          запуск без окна консоли")
    if extra_bats:
        lines.append("  Run_*.bat              запуск других стартовых файлов")
    lines.append("  runtime\\               Python %s (%d-бит) целиком"
                 % (runtime["version"], arch))
    lines.append("  runtime\\Lib\\site-packages   все пакеты проекта")
    lines.append("  исходники проекта    ваша программа (копия проекта)")
    lines.append("  PORTABLE_INFO.txt      этот файл (инструкция и отчёт)")
    lines.append("  размер сборки: %s" % human(total))
    lines.append("")
    lines.append("ВАЖНЫЕ ОГРАНИЧЕНИЯ (чтобы всё работало)")
    lines.append("  - Разрядность Windows должна совпадать: сборка %s."
                 % ("64-битная (x64)" if arch == 64 else "32-битная (x86)"))
    lines.append("  - Папку runtime\\ нельзя удалять и нельзя переносить отдельно от .bat.")
    lines.append("  - Windows 7/8 для Python 3.9+ может потребовать обновление системы")
    lines.append("    (KB2999226 / UCRT). На Windows 10 и 11 всё есть по умолчанию.")
    lines.append("  - Если программа обращается к файлам по относительным путям, она")
    lines.append("    будет считать текущей папкой эту папку сборки.")
    lines.append("")
    lines.append("ВЕРСИИ И ЗАВИСИМОСТИ")
    lines.append("  Python: %s (%d-бит), взят из %s" % (runtime["version"], arch, base_prefix))
    if site_dir:
        lines.append("  окружение проекта: %s" % site_dir)
    if third:
        lines.append("  зависимости, которые реально используются кодом:")
        shown = set()
        for _m, dist, ver in third:
            if dist in shown:
                continue
            shown.add(dist)
            lines.append("    %-28s %s" % (dist, ver))
    if index:
        lines.append("  всего пакетов в окружении проекта: %d%s"
                     % (len(index), " (часть не перенесена из-за --lean)" if skipped else ""))
        for key in sorted(index):
            meta = index[key]
            lines.append("    %-28s %s" % (meta["dist_name"], meta["version"]))
    if unknown:
        lines.append("")
        lines.append("  ВНИМАНИЕ - импортируется, но не найдено в окружении: " + ", ".join(unknown))
        lines.append("  (такое приложение соберётся, но при запуске может сообщить,")
        lines.append("   что модуля нет - установите его в venv проекта и пересоберите)")
    if warns:
        lines.append("  предупреждения: " + "; ".join(warns))
    lines.append("")
    lines.append("ПРОВЕРКА СБОРКИ: %s"
                 % ("успешна" if test_ok else "ЕСТЬ ЗАМЕЧАНИЯ - см. вывод сборки"))
    if not args.no_test:
        lines.append("  Проверено: запуск из другой папки этого ПК и запуск из копии")
        lines.append("  всей сборки (эмуляция переноса на другой компьютер), а также то,")
        lines.append("  что сборка не ссылается на исходные папки проекта и Python.")
    lines.append("")
    lines.append("Если на другом компьютере появится сообщение об ошибке - пришлите")
    lines.append("его текст. Обычно причин три: не та разрядность Windows, удалена")
    lines.append("папка runtime, или в исходном venv не было нужного пакета.")
    with open(os.path.join(out_dir, "PORTABLE_INFO.txt"), "w",
              encoding="utf-8-sig", newline="\r\n") as fh:
        fh.write("\r\n".join(lines) + "\r\n")
    say("PORTABLE_INFO.txt - инструкция и отчёт о сборке")

    print("")
    print("=" * 70)
    print("ГОТОВО.  Портативная сборка: %s" % out_dir)
    print("Размер: %s    Проверка переноса: %s" % (human(total), "OK" if test_ok else "ЕСТЬ ЗАМЕЧАНИЯ"))
    print("На другом ПК: скопировать папку целиком и запустить Start.bat")
    print("=" * 70)
    return 0 if test_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nпрервано пользователем", flush=True)
        sys.exit(130)
