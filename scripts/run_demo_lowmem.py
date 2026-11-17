# 低显存启动器（8GB 显卡）：checkpoint 先读到 CPU，不改动项目源码。
# 用法：python run_demo_lowmem.py demo.py <demo 参数...>
import runpy, sys, torch

_orig_load = torch.load
def _load_cpu(*a, **kw):
    kw["map_location"] = "cpu"
    kw.setdefault("mmap", True)  # 按需映射，避免 4.6GB 完整拷贝占满内存
    return _orig_load(*a, **kw)
torch.load = _load_cpu

script = sys.argv[1]
sys.argv = sys.argv[1:]
sys.path.insert(0, ".")
runpy.run_path(script, run_name="__main__")
