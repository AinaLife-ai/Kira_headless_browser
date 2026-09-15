from pathlib import Path


def get_data_path(*parts):
    p = Path("/tmp/kira_data")
    for x in parts:
        p = p / str(x)
    return p
