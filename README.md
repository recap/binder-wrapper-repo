# Binder wrapper repo

Minimal Binder wrapper that accepts launch parameters, writes them to `/home/jovyan/work/.env`, pulls a target Git repository, overlays it into `/home/jovyan/work`, and redirects to the requested notebook.

## Binder URL shape

Use Binder's `urlpath` to call the wrapper extension:

```text
https://mybinder.org/v2/gh/recap/binder-wrapper-repo/main?urlpath=binder-launch%3Frepo%3Dhttps%253A%252F%252Fgithub.com%252FORG%252FTARGET_REPO%26branch%3Dmain%26urlpath%3Dlab%252Ftree%252Fnotebook.ipynb%26csv_url%3Dhttps%253A%252F%252Fexample.org%252Fdata.csv%26dataset_pid%3Ddoi%253A10.1234%252Fabcd
```

Decoded inner route:

```text
binder-launch?repo=https://github.com/ORG/TARGET_REPO&branch=main&urlpath=lab/tree/notebook.ipynb&csv_url=https://example.org/data.csv&dataset_pid=doi:10.1234/abcd
```

The extension writes:

```text
/home/jovyan/work/.env
```

with:

```dotenv
BINDER_PARAM_CSV_URL='https://example.org/data.csv'
BINDER_PARAM_DATASET_PID='doi:10.1234/abcd'
```

## Notebook usage

```python
from dotenv import dotenv_values
from pathlib import Path

params = dotenv_values(Path.home() / "work" / ".env")
csv_url = params.get("BINDER_PARAM_CSV_URL")
dataset_pid = params.get("BINDER_PARAM_DATASET_PID")
```
