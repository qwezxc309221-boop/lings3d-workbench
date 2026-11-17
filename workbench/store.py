"""扫描索引与回收站，写入范围限于受管 UUID 目录。"""
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def recover_capture(directory, record):
    """按提交日志校正快照帧数，忽略未写完的图像对。"""
    directory=Path(directory)
    for segment in record['segments']:
        root=directory/segment['path'];manifest=root/'frames.jsonl';count=0
        if manifest.is_file():
            with manifest.open('rb+') as stream:
                committed_end=0
                for line in stream:
                    try:
                        row=json.loads(line)
                        if row['index']!=count:break
                        valid=True
                        for side in ('left','right'):
                            path=(root/side/row[side]).resolve()
                            if not path.is_relative_to((root/side).resolve()) or not path.is_file():valid=False
                        if not valid:break
                        count+=1;committed_end+=len(line)
                    except (ValueError,KeyError):break
                stream.truncate(committed_end)
        segment['frames']=count
    record['frames']=sum(s['frames'] for s in record['segments'])
    record['status']='interrupted'
    write_json(directory/'scan.json',dict(record,updated_at=now()))


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.trash = self.root / '.trash'
        self.trash.mkdir(exist_ok=True)

    def path(self, scan_id, trash=False):
        if not re.fullmatch(r'[0-9a-f]{32}', scan_id):
            raise ValueError('扫描编号无效')
        path = (self.trash if trash else self.root) / scan_id
        if path.is_symlink():
            raise ValueError('扫描目录不能是符号链接')
        return path

    def read(self, scan_id, trash=False):
        return json.loads((self.path(scan_id, trash) / 'scan.json').read_text())

    def update(self, scan_id, **fields):
        record = self.read(scan_id)
        record.update(fields, updated_at=now())
        write_json(self.path(scan_id) / 'scan.json', record)
        return record

    def create(self, name, source='camera'):
        scan_id = uuid.uuid4().hex
        directory = self.path(scan_id)
        directory.mkdir()
        record = dict(id=scan_id, name=name.strip()[:80] or '未命名扫描',
                      source=source, status='draft', created_at=now(), updated_at=now(),
                      segments=[], frames=0, inference={'status': 'idle'})
        write_json(directory / 'scan.json', record)
        return record

    def list(self, trash=False):
        records = []
        for path in (self.trash if trash else self.root).glob('*/scan.json'):
            try:
                if not path.parent.is_symlink():
                    record = json.loads(path.read_text())
                    record['in_trash'] = trash
                    records.append(record)
            except (ValueError, OSError):
                continue
        return sorted(records, key=lambda item: item['created_at'], reverse=True)

    def move(self, scan_id, restore=False):
        record = self.read(scan_id, trash=restore)
        if not restore and (record['status'] in ('recording', 'paused') or
                            record.get('inference', {}).get('status') in ('loading', 'running')):
            raise ValueError('请先结束拍摄或取消推理，再删除')
        source, target = self.path(scan_id, restore), self.path(scan_id, not restore)
        if target.exists():
            raise ValueError('目标任务已存在')
        shutil.move(str(source), str(target))
        return record
