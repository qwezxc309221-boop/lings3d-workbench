"""检查交付目录的源码语法、配置与文档引用。"""
import ast
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
python_files = list(ROOT.rglob("*.py"))
for path in python_files:
    ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
for path in list((ROOT / "docker").rglob("*.launch")) + [ROOT / "workbench/camera.launch"]:
    ET.parse(path)
ET.parse(ROOT / "docker/catkin_ws/src/lings_3d/package.xml")
# 检查维护文档与历史文档的相对链接；外部绝对路径仅作来源记录。
documents = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "THIRD_PARTY_NOTICES.md", ROOT / "workbench/README.md"]
documents += list((ROOT / "docs").rglob("*.md"))
errors = []
for path in documents:
    for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", path.read_text()):
        target = match.group(1).split("#", 1)[0]
        if not target or "://" in target or target.startswith(("/", "mailto:")):
            continue
        if not (path.parent / target).exists():
            errors.append(f"{path.relative_to(ROOT)}: {target}")
if errors:
    raise SystemExit("无效文档链接：\n" + "\n".join(errors))
print(f"语法与配置检查通过：{len(python_files)} 个 Python 文件，{len(documents)} 份文档。")
