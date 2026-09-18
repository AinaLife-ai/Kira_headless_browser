import atexit
import shutil
import tempfile
from pathlib import Path

# ⚠️ 每个**进程**一个唯一的数据根，不要写死 /tmp/kira_data：
#    并发跑回归、或上次跑崩了没清，都会让两轮共用同一份目录，
#    表现为"莫名其妙的数据被改"（而且很难复现、很难查）。
_ROOT = Path(tempfile.mkdtemp(prefix="kira_data_"))

# 进程退出时把这个目录收掉 —— 否则每跑一次回归就在系统临时目录里
# 留一份数据（包含日志、配置、可能的下载文件），跑几十次就堆成垃圾。
atexit.register(shutil.rmtree, _ROOT, True)


def get_data_path(*parts):
    p = _ROOT
    for x in parts:
        p = p / str(x)
    return p
