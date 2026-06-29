import os
import shlex
import shutil
import subprocess
from pathlib import Path


import tornado.web
from jupyter_server.base.handlers import JupyterHandler
from jupyter_server.utils import url_path_join

HOME = Path.home()
WORK = HOME / "workspace"
TARGET = WORK / "target"
ENV_FILE = WORK / ".env"
KEEP = {".env", ".ipynb_checkpoints"}

RESERVED_PARAMS = {"repo", "branch", "urlpath", "notebookpath", "targetpath", "overwrite"}


def shell_escape_env_value(value: str) -> str:
    return shlex.quote(value)


def safe_remove_work_contents():
    WORK.mkdir(exist_ok=True)
    for item in WORK.iterdir():
        if item.name in KEEP:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

def write_env(params: dict[str, str], log):
    log.info("Writing environment file: %s", ENV_FILE)
    log.info("Received %d parameters", len(params))

    lines = []

    for key, value in sorted(params.items()):
        env_key = "BINDER_PARAM_" + key.upper().replace("-", "_")

        if not env_key.replace("_", "").isalnum():
            log.warning(
                "Skipping invalid parameter '%s' -> '%s'",
                key,
                env_key,
            )
            continue

        escaped = shell_escape_env_value(value)

        log.info(
            "Adding %s=%s",
            env_key,
            escaped,
        )

        lines.append(f"{env_key}={escaped}")

    contents = "\n".join(lines) + "\n"

    log.debug("Writing .env contents:\n%s", contents)

    ENV_FILE.write_text(contents)

    log.info(
        "Successfully wrote %s (%d bytes)",
        ENV_FILE,
        ENV_FILE.stat().st_size,
    )

def git_clone(repo: str, branch: str | None, log):
    import shutil as _shutil

    git = _shutil.which("git")
    log.info("PATH=%s", os.environ.get("PATH"))
    log.info("git executable=%s", git)

    if git is None:
        raise RuntimeError("git executable not found")

    if TARGET.exists():
        shutil.rmtree(TARGET)

    cmd = [git, "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [repo, str(TARGET)]

    log.info("running clone command: %s", " ".join(shlex.quote(c) for c in cmd))

    result = subprocess.run(cmd, text=True, capture_output=True)

    log.info("git returncode=%s", result.returncode)
    log.info("git stdout=%s", result.stdout)
    log.info("git stderr=%s", result.stderr)

    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            cmd,
            output=result.stdout,
            stderr=result.stderr,
        )

    log.info("clone target exists=%s contents=%s", TARGET.exists(), list(TARGET.iterdir()))

def install_requirements(log):
    pip = shutil.which("pip")
    if pip is None:
        raise RuntimeError("pip executable not found")

    candidates = [
        TARGET / "binder" / "requirements.txt",
        TARGET / ".binder" / "requirements.txt",
        TARGET / "requirements.txt",
        TARGET / "binder" / "environment.yml",
        TARGET / ".binder" / "environment.yml",
        TARGET / "environment.yml",
        TARGET / "pyproject.toml",
    ]

    for path in candidates:
        if not path.exists():
            continue

        log.info("Found dependency file: %s", path)

        if path.name == "requirements.txt":
            cmd = [pip, "install", "-r", str(path)]

        elif path.name == "pyproject.toml":
            cmd = [pip, "install", str(TARGET)]

        elif path.name == "environment.yml":
            # Don't try to recreate the conda environment at runtime.
            log.warning(
                "Found environment.yml but runtime conda environment "
                "creation is not supported. Skipping."
            )
            return

        log.info("Running: %s", " ".join(cmd))

        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
        )

        log.info(result.stdout)
        log.info(result.stderr)

        if result.returncode != 0:
            raise RuntimeError(
                f"Dependency installation failed:\n{result.stderr}"
            )

        return

    log.info("No supported dependency files found.")

def copy_target_into_work(log):
    log.info("Copying target repository into work directory")
    log.info("TARGET=%s", TARGET)
    log.info("WORK=%s", WORK)

    if not TARGET.exists():
        raise RuntimeError(f"Target directory does not exist: {TARGET}")

    items = list(TARGET.iterdir())
    log.info("Target contains %d items", len(items))

    for item in items:
        log.info("Processing %s", item)

        if item.name == ".git":
            log.info("Skipping .git directory")
            continue

        dest = WORK / item.name

        if dest.exists():
            log.info("Destination already exists: %s", dest)

            if dest.is_dir():
                log.info("Removing existing directory")
                shutil.rmtree(dest)
            else:
                log.info("Removing existing file")
                dest.unlink()

        log.info("Moving %s -> %s", item, dest)
        shutil.move(str(item), str(dest))

    log.info("Removing temporary clone directory %s", TARGET)
    shutil.rmtree(TARGET)

    log.info("Final work directory contents:")

    for path in sorted(WORK.iterdir()):
        log.info("  %s", path.name)


class LaunchHandler(JupyterHandler):
    @tornado.web.authenticated
    def get(self):
        repo = self.get_argument("repo")
        branch = self.get_argument("branch", None)
        urlpath = self.get_argument("urlpath", "lab/tree")
        notebookpath = self.get_argument("notebookpath", None)
        overwrite = self.get_argument("overwrite", "1") == "1"

        params = {}
        for key, values in self.request.query_arguments.items():
            if key in RESERVED_PARAMS:
                continue
            # Tornado returns bytes; use last value for simplicity.
            params[key] = values[-1].decode("utf-8")

        self.serverapp.log.info(f"Launching with repo={repo}, branch={branch}, urlpath={urlpath}, overwrite={overwrite}, params={params}")

        try:
            if overwrite:
                safe_remove_work_contents()

            write_env(params, self.serverapp.log)
            git_clone(repo, branch, self.serverapp.log)
            install_requirements(self.serverapp.log)
            copy_target_into_work(self.serverapp.log)

        except subprocess.CalledProcessError as exc:
            self.set_status(500)
            self.write({
                "status": "error",
                "message": "git clone failed",
                "stdout": exc.stdout,
                "stderr": exc.stderr,
            })
            return
        except Exception as exc:
            self.set_status(500)
            self.write({"status": "error", "message": str(exc)})
            return

        redirect_url = url_path_join(self.base_url, urlpath)
        self.serverapp.log.info(f"Redirecting to {redirect_url}")

        self.redirect(url_path_join(self.base_url, urlpath))
