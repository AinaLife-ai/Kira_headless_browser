import tempfile
from pathlib import Path

# ⚠️ 每个**进程**一个唯一的数据根，不要写死 /tmp/kira_data：
#    并发跑回归、或上次跑崩了没清，都会让两轮共用同一份目录，
#    表现为"莫名其妙的数据被改"（而且很难复现、很难查）。
_ROOT = Path(tempfile.mkdtemp(prefix="kira_data_"))


def get_data_path(*parts):
    p = _ROOT
    for x in parts:
        p = p / str(x)
    return p
