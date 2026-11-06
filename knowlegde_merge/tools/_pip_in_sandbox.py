"""在 DSH 沙箱里跑 pip 的兼容垫片。

沙箱环境下 tempfile.mkdtemp() 建出来的目录，受限令牌写不进去，
pip 解包时会直接 PermissionError。这里把 mkdtemp 换成 makedirs 实现，
其它行为不变。只在沙箱内需要，普通命令行直接 `pip install` 即可。
"""

import os
import sys
import tempfile

_orig_mkdtemp = tempfile.mkdtemp


def _mkdtemp(suffix=None, prefix=None, dir=None):
    base = dir or tempfile.gettempdir()
    name = (prefix or "tmp") + next(tempfile._RandomNameSequence()) + (suffix or "")
    path = os.path.join(base, name)
    os.makedirs(path, exist_ok=False)
    return path


tempfile.mkdtemp = _mkdtemp

if __name__ == "__main__":
    import runpy

    sys.argv = ["pip"] + sys.argv[1:]
    runpy.run_module("pip", run_name="__main__", alter_sys=True)