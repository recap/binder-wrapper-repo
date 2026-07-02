import os
import shlex
import shutil
import subprocess
from pathlib import Path
import json
import urllib.request
from urllib.parse import urlparse
import tornado.web
from jupyter_server.base.handlers import JupyterHandler
from jupyter_server.utils import url_path_join

WORKSPACE_DIR_NAME = "workspace"
KEEP = {".env", ".ipynb_checkpoints"}
# ENV_PREFIX = "BINDER_PARAM_"
ENV_PREFIX = ""
WRAPPER_FILES = {
    "binder_launcher",
    "binder",
    ".binder",
    "pyproject.toml",
    "README.md",
    "binder_launcher.json",
}

# Reserved query parameters understood by the launcher.
#
# repo:          Git repository URL to clone.
#                Example: https://github.com/recap/DataLens
#
# branch:        Optional Git branch, tag, or commit to checkout.
#                Default: repository default branch.
#
# urlpath:       Jupyter URL prefix to redirect to after staging.
#                Typical values:
#                  - lab/tree (default)
#                  - lab
#                  - tree
#
# notebookpath:  Path (relative to the repository root) of the
#                notebook to open automatically.
#                Example: notebooks/analysis.ipynb
#
# targetpath:    Optional target directory inside the workspace where the
#                repository should be staged.
#                Default: workspace root.
#
# overwrite:     Whether to overwrite the current workspace before staging.
#                Values: "1" (default), "1"
#
# cleanup:       Whether to remove the wrapper files after launching.
#                Values: "1" (default), "0"
#
# run_postbuild: Whether to run the postBuild script after staging.
#                Values: "1", "0" (default)
#
# data:          URL-encoded JSON describing data files to download after
#                staging the repository.
#                Schema:
#                [
#                  {
#                    "url": "https://example.org/data.csv",
#                    "path": "data/data.csv"
#                  }
#                ]
#
# All other query parameters are written to the .env file and made
# available to the launched notebook.

RESERVED_PARAMS = {
    "repo",
    "branch",
    "urlpath",
    "notebookpath",
    "targetpath",
    "overwrite",
    "cleanup",
    "data",
    "run_postbuild",
}


def get_server_root(handler: JupyterHandler) -> Path:
    root = handler.settings.get("server_root_dir")

    if root is None:
        root = handler.contents_manager.root_dir

    return Path(os.path.expanduser(root)).resolve()


def run_post_build(target: Path, log):
    candidates = [
        target / "binder" / "postBuild",
        target / ".binder" / "postBuild",
        target / "postBuild",
    ]

    for script in candidates:
        if not script.exists():
            continue

        log.info("Running postBuild: %s", script)

        script.chmod(script.stat().st_mode | 0o111)

        result = subprocess.run(
            ["/bin/bash", str(script)],
            cwd=target,
            text=True,
            capture_output=True,
            env=os.environ.copy(),
        )

        log.info(result.stdout)
        log.info(result.stderr)

        if result.returncode != 0:
            raise RuntimeError(
                f"postBuild failed:\n{result.stderr}"
            )

        return

    log.info("No postBuild script found.")

def is_safe_relative_path(path: str) -> bool:
    p = Path(path)

    if p.is_absolute():
        return False

    if ".." in p.parts:
        return False

    return True


def filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name
    return name or "downloaded_file"


def stage_data_files(work: Path, data_json: str | None, log):
    if not data_json:
        log.info("No data staging requested")
        return

    log.info("Data staging requested")

    try:
        specs = json.loads(data_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid data JSON: {exc}") from exc

    if isinstance(specs, dict):
        specs = [specs]

    if not isinstance(specs, list):
        raise ValueError("data must be a JSON object or a JSON array of objects")

    # data_dir = WORK / "data"
    # data_dir.mkdir(parents=True, exist_ok=True)

    manifest = []

    for i, spec in enumerate(specs):
        if not isinstance(spec, dict):
            raise ValueError(f"data[{i}] must be an object")

        url = spec.get("url")
        if not url:
            raise ValueError(f"data[{i}] is missing required field 'url'")

        parsed = urlparse(url)

        if parsed.scheme not in {"https", "http"}:
            raise ValueError(f"Unsupported URL scheme for {url!r}")

        relative_path = spec.get("path") or filename_from_url(url)

        if not is_safe_relative_path(relative_path):
            raise ValueError(f"Unsafe data path: {relative_path!r}")

        dest = work / relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)

        log.info("Staging data file %s -> %s", url, dest)

        with urllib.request.urlopen(url, timeout=60) as response:
            content = response.read()

        dest.write_bytes(content)

        manifest.append({
            "url": url,
            "path": str(dest.relative_to(work)),
            "size": dest.stat().st_size,
        })

        log.info("Staged %s (%d bytes)", dest, dest.stat().st_size)

    manifest_path = work / "data_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    log.info("Wrote data manifest: %s", manifest_path)


def shell_escape_env_value(value: str) -> str:
    return shlex.quote(value)


def safe_remove_work_contents(work: Path, log):
    work.mkdir(exist_ok=True)
    for item in work.iterdir():
        if item.name in KEEP:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def write_env(env_file: Path, params: dict[str, str], log):
    log.info("Writing environment file: %s", env_file)
    log.info("Received %d parameters", len(params))

    lines = []

    for key, value in sorted(params.items()):
        env_key = ENV_PREFIX + key.upper().replace("-", "_")

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

    env_file.write_text(contents)

    log.info(
        "Successfully wrote %s (%d bytes)",
        env_file,
        env_file.stat().st_size,
    )


def git_clone(target: Path, repo: str, branch: str | None, log):
    import shutil as _shutil

    git = _shutil.which("git")
    log.info("PATH=%s", os.environ.get("PATH"))
    log.info("git executable=%s", git)

    if git is None:
        raise RuntimeError("git executable not found")

    if target.exists():
        shutil.rmtree(target)

    cmd = [git, "clone", "--depth", "1"]
    if branch:
        cmd += ["--branch", branch]
    cmd += [repo, str(target)]

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

    log.info("clone target exists=%s contents=%s", target.exists(), list(target.iterdir()))


def install_requirements(target: Path, log):
    pip = shutil.which("pip")
    if pip is None:
        raise RuntimeError("pip executable not found")

    candidates = [
        target / "binder" / "requirements.txt",
        target / ".binder" / "requirements.txt",
        target / "requirements.txt",
        target / "binder" / "environment.yml",
        target / ".binder" / "environment.yml",
        target / "environment.yml",
        target / "pyproject.toml",
    ]

    for path in candidates:
        if not path.exists():
            continue

        log.info("Found dependency file: %s", path)

        if path.name == "requirements.txt":
            cmd = [pip, "install", "-r", str(path)]

        elif path.name == "pyproject.toml":
            cmd = [pip, "install", str(target)]

        elif path.name == "environment.yml":
            # Don't try to recreate the conda environment at runtime.
            # log.warning(
            #     "Found environment.yml but runtime conda environment "
            #     "creation is not supported. Skipping."
            # )
            conda = shutil.which("conda")
            if conda is None:
                raise RuntimeError("conda executable not found")

            log.info("Updating current conda environment from %s", path)

            cmd = [
                conda,
                "env",
                "update",
                "--prefix",
                os.environ.get("CONDA_PREFIX"),
                "--file",
                str(path),
                "--prune",
            ]
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


def copy_target_into_work(target: Path, work: Path, log):
    log.info("Copying target repository into work directory")
    log.info("TARGET=%s", target)
    log.info("WORK=%s", work)

    if not target.exists():
        raise RuntimeError(f"Target directory does not exist: {target}")

    items = list(target.iterdir())
    log.info("Target contains %d items", len(items))

    for item in items:
        log.info("Processing %s", item)

        if item.name == ".git":
            log.info("Skipping .git directory")
            continue

        dest = work / item.name

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

    log.info("Removing temporary clone directory %s", target)
    shutil.rmtree(target)

    log.info("Final work directory contents:")

    for path in sorted(work.iterdir()):
        log.info("  %s", path.name)


def clean_wrapper_files(root: Path, log):
    log.info("Removing Binder launcher files from %s", root)

    for item in root.iterdir():
        if item.name in WRAPPER_FILES or item.name.endswith(".egg-info"):
            log.info("Removing %s", item)

            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

     # Remove all *.egg-info
    for item in root.glob("*.egg-info"):
        log.info("Removing %s", item)

        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    log.info("Wrapper cleanup complete")


class LaunchHandler(JupyterHandler):
    @tornado.web.authenticated
    def get(self):
        repo = self.get_argument("repo")
        branch = self.get_argument("branch", None)
        urlpath = self.get_argument("urlpath", "lab/tree")
        notebookpath = self.get_argument("notebookpath", None)
        overwrite = self.get_argument("overwrite", "1") == "1"
        cleanup = self.get_argument("cleanup", "1") == "1"
        data_json = self.get_argument("data", None)
        run_postbuild = self.get_argument("run_postbuild", "0") == "1"
        server_root = get_server_root(self)

        work = server_root / WORKSPACE_DIR_NAME
        target = work / "target"
        env_file = work / ".env"

        self.log.info("server_root=%s", server_root)
        self.log.info("work=%s", work)
        self.log.info("target=%s", target)


        params = {}
        for key, values in self.request.query_arguments.items():
            if key in RESERVED_PARAMS:
                continue
            # Tornado returns bytes; use last value for simplicity.
            params[key] = values[-1].decode("utf-8")

        self.serverapp.log.info("Received %d parameters", len(params))

        try:
            if overwrite:
                safe_remove_work_contents(work, self.serverapp.log)


            write_env(env_file, params, self.serverapp.log)
            git_clone(target, repo, branch, self.serverapp.log)
            install_requirements(target, self.serverapp.log)
            if run_postbuild:
                run_post_build(target, self.serverapp.log)
            if cleanup:
                clean_wrapper_files(server_root, self.serverapp.log)
            copy_target_into_work(target, work, self.serverapp.log)
            stage_data_files(work, data_json, self.serverapp.log)

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

        redirect_url = url_path_join(self.base_url, urlpath, WORKSPACE_DIR_NAME, notebookpath) if notebookpath else url_path_join(self.base_url, urlpath, WORKSPACE_DIR_NAME)
        # redirect_url = url_path_join(self.base_url, urlpath, notebookpath)
        self.serverapp.log.info(f"Redirecting to {redirect_url}")

        self.redirect(redirect_url)
