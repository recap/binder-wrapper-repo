# Binder Launcher

Binder Launcher is a small Jupyter Server extension for launching a target notebook repository inside a Binder session.

It lets you start from a lightweight wrapper Binder repository, then dynamically:

- clone a target Git repository;
- copy it into `/home/jovyan/workspace`;
- write launch parameters into `/home/jovyan/workspace/.env`;
- install Python dependencies from the target repository;
- optionally download data files into the workspace;
- redirect the user directly to a notebook.

This is useful when you want to launch an existing notebook repository with different datasets or parameters, without modifying the target repository.

---

## How it works

The wrapper Binder repository provides the environment and the launcher extension.

At runtime, you open the `/launch` route with query parameters. The launcher then:

1.  clears the existing `workspace/` directory, unless disabled;
2.  writes non-reserved URL parameters to `workspace/.env`;
3.  clones the target repository into `workspace/target`;
4.  installs dependencies if supported files are found;
5.  optionally removes wrapper files from `/home/jovyan`;
6.  moves the target repository contents into `workspace/`;
7.  optionally downloads data files;
8.  redirects to JupyterLab.

The final notebook content is placed under:

```text
/home/jovyan/workspace

```

---

## Basic launch URL

```text
https://mybinder.org/v2/gh/OWNER/BINDER_LAUNCHER_REPO/main?urlpath=launch%3Frepo%3Dhttps%253A%252F%252Fgithub.com%252FOWNER%252FTARGET_REPO%26branch%3Dmain%26notebookpath%3Dnotebook.ipynb

```

Decoded, the inner launcher route is:

```text
/launch?repo=https://github.com/OWNER/TARGET_REPO&branch=main&notebookpath=notebook.ipynb

```

---

## Example

```text
https://mybinder.org/v2/gh/recap/binder-launcher/main?urlpath=launch%3Frepo%3Dhttps%253A%252F%252Fgithub.com%252Frecap%252FDataLens%26branch%3Dmain%26notebookpath%3DDataLens_EDA.ipynb%26CSV_URL%3Dhttps%253A%252F%252Fexample.org%252Fdata.csv

```

This will:

- launch the Binder wrapper repository;
- call `/launch`;
- clone `https://github.com/recap/DataLens`;
- write `CSV_URL=https://example.org/data.csv` to `.env`;
- open `workspace/DataLens_EDA.ipynb`.

---

## Launcher parameters

These parameters control the launcher itself.

| Parameter                | Required | Default                   | Description                                                                                                             | Example                                                           |
| ------------------------ | :------: | ------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `repo`                   |    ✓     | —                         | Git repository URL to clone.                                                                                            | `https://github.com/recap/DataLens`                               |
| `branch`                 |          | Repository default branch | Git branch, tag, or commit to checkout.                                                                                 | `main`                                                            |
| `urlpath`                |          | `lab/tree`                | Jupyter URL prefix used for the final redirect. Typical values: `lab/tree`, `lab`, `tree`.                              | `lab/tree`                                                        |
| `notebookpath`           |          | —                         | Path to the notebook to open, relative to the repository root.                                                          | `notebooks/analysis.ipynb`                                        |
| `targetpath`             |          | Workspace root            | Reserved for future use. Intended to specify a subdirectory within the workspace where the repository should be staged. | `examples/`                                                       |
| `overwrite`              |          | `1`                       | Whether to clear the existing workspace before staging the repository. Values: `1` = overwrite, `0` = preserve.         | `1`                                                               |
| `cleanup`                |          | `0`                       | Whether to remove the wrapper repository files after launching. Values: `1` = remove wrapper files, `0` = keep them.    | `1`                                                               |
| `data`                   |          | —                         | URL-encoded JSON describing files to download into the workspace after staging the repository.                          | `[{"url":"https://example.org/data.csv","path":"data/data.csv"}]` |
| _(all other parameters)_ |          | —                         | Written unchanged to `workspace/.env` and made available to the launched notebook.                                      | `CSV_URL=https://example.org/data.csv`                            |

---

## Passing parameters to the notebook

Any query parameter that is not reserved becomes an entry in `.env`.

For example:

```text
/launch?repo=https://github.com/recap/DataLens&CSV_URL=https://example.org/data.csv&DATASET_PID=doi:10.1234/abcd

```

creates:

```dotenv
CSV_URL=https://example.org/data.csv
DATASET_PID=doi:10.1234/abcd

```

The notebook can read it with `python-dotenv`:

```python
from dotenv import load_dotenv
import os

load_dotenv(".env")

csv_url = os.getenv("CSV_URL")
dataset_pid = os.getenv("DATASET_PID")

print(csv_url)
print(dataset_pid)

```

---

## Staging data files

The `data` parameter accepts URL-encoded JSON.

Decoded example:

```json
[
  {
    "url": "https://example.org/data.csv",
    "path": "data/data.csv"
  },
  {
    "url": "https://example.org/config.json",
    "path": "config.json"
  }
]
```

Each object supports:

Field

Required

Description

`url`

yes

HTTP or HTTPS URL to download.

`path`

no

Destination path relative to `workspace/`. If omitted, the filename is inferred from the URL.

After staging, the launcher writes:

```text
/home/jovyan/workspace/data_manifest.json

```

Example manifest:

```json
[
  {
    "url": "https://example.org/data.csv",
    "path": "data/data.csv",
    "size": 12345
  }
]
```

The notebook can then read staged files from the workspace:

```python
from pathlib import Path

data_file = Path("data/data.csv")
print(data_file.exists())

```

---

## Dependency installation

After cloning the target repository, the launcher looks for dependency files in this order:

1.  `binder/requirements.txt`
2.  `.binder/requirements.txt`
3.  `requirements.txt`
4.  `binder/environment.yml`
5.  `.binder/environment.yml`
6.  `environment.yml`
7.  `pyproject.toml`

Supported at runtime:

- `requirements.txt`
- `pyproject.toml`

`environment.yml` is detected but skipped. The launcher does not recreate a Conda environment at runtime.

---

## Cleanup behaviour

By default:

```text
cleanup=0

```

This keeps the wrapper repository files visible in `/home/jovyan`.

To remove wrapper files after launch:

```text
cleanup=1

```

The launcher removes common wrapper files such as:

```text
binder_launcher/
binder/
.binder/
pyproject.toml
README.md
binder_launcher.json
*.egg-info

```

The target repository remains available under:

```text
/home/jovyan/workspace

```

---

## Workspace behaviour

The target repository is copied into:

```text
/home/jovyan/workspace

```

If `overwrite=1`, existing workspace contents are removed before launch, except:

```text
.env
.ipynb_checkpoints

```

Use:

```text
overwrite=0

```

to avoid clearing the workspace before launch.

---

## URL encoding helper

Because Binder uses `urlpath=` and the launcher also has its own query parameters, the inner launch route must be URL-encoded.

Example in Python:

```python
from urllib.parse import urlencode, quote

inner = "launch?" + urlencode({
    "repo": "https://github.com/recap/DataLens",
    "branch": "main",
    "notebookpath": "DataLens_EDA.ipynb",
    "CSV_URL": "https://example.org/data.csv",
})

binder_url = (
    "https://mybinder.org/v2/gh/recap/binder-launcher/main"
    "?urlpath="
    + quote(inner, safe="")
)

print(binder_url)

```

---

## Notes and limitations

- Runtime dependency installation can be slow.
- Large or compiled dependencies may fail on public Binder.
- `environment.yml` files are detected but not installed.
- Data staging currently supports only HTTP and HTTPS URLs.
- Staged paths must be relative and cannot contain `..`.
- Secrets should not be passed in launch URLs because URLs may appear in browser history, logs, or shared links.

---

## Intended use

Binder Launcher is intended for interactive research and demonstration workflows where a notebook repository should be launched with dynamic context, such as:

- a dataset URL;
- a dataset PID;
- configuration parameters;
- staged input files;
- a notebook path selected by an external workflow system.
