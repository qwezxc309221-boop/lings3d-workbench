"""分块几何读写和磁盘体素地图。"""
import json
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path

import numpy as np
from store import write_json

CHUNK_SIZE = 16
OVERVIEW_LIMIT = 300_000


def safe_image(segment, side, name):
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or relative.suffix != '.png':
        raise ValueError('图像清单路径无效')
    root = (Path(segment)/side).resolve()
    target = (root/relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError('图像清单越界')
    return target


def iter_frames(segment):
    with (Path(segment)/'frames.jsonl').open() as stream:
        for index, line in enumerate(stream):
            row = json.loads(line)
            if row.get('index', index) != index:
                raise ValueError('图像清单帧序号不连续')
            if row['left_stamp_ns'] != row['right_stamp_ns']:
                raise ValueError('左右图像不同步')
            for side in ('left', 'right'):
                safe_image(segment, side, row[side])
            yield row


def disk_guard(path, reserve=2*1024**3):
    if shutil.disk_usage(path).free < reserve:
        raise ValueError('磁盘剩余不足2GiB，已停止写入；原始图像保留')


class ChunkWriter:
    """逐块写入，使用原子重命名提交文件。"""
    def __init__(self, root, chunk_size=CHUNK_SIZE):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.chunk_size=chunk_size;self.buffer=[];self.count=0;self.chunks=[]

    def append(self, frame):
        self.buffer.append(frame)
        if len(self.buffer)>=self.chunk_size:self.flush()

    def flush(self):
        if not self.buffer:return
        disk_guard(self.root)
        start=self.count;name=f'{start:08d}.npz'
        temporary=self.root/(name+'.tmp')
        with temporary.open('wb') as stream:
            np.savez_compressed(stream,**{k:np.stack([r[k] for r in self.buffer]) for k in self.buffer[0]})
        os.replace(temporary,self.root/name)
        self.count+=len(self.buffer)
        self.chunks.append(dict(file=name,start=start,count=len(self.buffer)))
        self.buffer.clear()
        write_json(self.root/'index.json',dict(format='chunked-v1',frames=self.count,chunks=self.chunks))

    def close(self):self.flush()


class ChunkReader:
    def __init__(self, root):
        self.root=Path(root);self.index=json.loads((self.root/'index.json').read_text())
        self.cached=None;self.data=None
        self.starts=[c['start'] for c in self.index['chunks']]

    def frame(self, index):
        if not 0<=index<self.index['frames']:raise IndexError(index)
        # 每块至多 16 帧，逐块读取。
        import bisect
        chunk=self.index['chunks'][bisect.bisect_right(self.starts,index)-1]
        if self.cached!=chunk['file']:
            self.data=None
            with np.load(self.root/chunk['file'],allow_pickle=False) as archive:
                self.data={k:archive[k] for k in archive.files}
            self.cached=chunk['file']
        return {k:v[index-chunk['start']] for k,v in self.data.items()}

    def __iter__(self):
        for i in range(self.index['frames']):yield self.frame(i)


def frame_cloud(frame, frame_index, limit=None):
    depth=frame['depth'];depth=depth[...,0] if depth.ndim==3 else depth
    y,x=np.indices(depth.shape)
    rays=np.stack([x,y,np.ones_like(x)],axis=-1)@np.linalg.inv(frame['intrinsic']).T
    camera=(rays*depth[...,None]).reshape(-1,3)
    e=frame['extrinsic']
    xyz=(camera-e[:,3])@e[:,:3]
    conf=frame['depth_conf'].reshape(-1)
    source=frame.get('source',np.ones(depth.shape,dtype=np.uint8)).reshape(-1)
    valid=np.isfinite(xyz).all(axis=1)&np.isfinite(conf)&(depth.reshape(-1)>0)&(source>0)
    indices=[]
    for mask in (source>=2,source==1):
        group=np.flatnonzero(valid&mask)
        if limit:group=group[::max(1,int(np.ceil(len(group)/max(1,limit//2))))]
        indices.extend(group)
    indices=np.asarray(indices,dtype=np.int64)
    return dict(points=xyz[indices].astype(np.float32),colors=frame['rgb'].reshape(-1,3)[indices],
                confidence=conf[indices],source=source[indices],
                frames=np.full(len(indices),frame_index,dtype=np.int32))


class VoxelMap:
    """地图存入 SQLite，内存仅保留当前帧和有限概览。"""
    def __init__(self, path, voxel_size=.02):
        self.db=sqlite3.connect(str(path));self.voxel_size=voxel_size
        self.db.execute('PRAGMA cache_size=-16384')
        self.db.execute('PRAGMA temp_store=FILE')
        self.db.execute('CREATE TABLE IF NOT EXISTS voxels (x INTEGER,y INTEGER,z INTEGER,s INTEGER,px REAL,py REAL,pz REAL,r REAL,g REAL,b REAL,c REAL,n INTEGER,f INTEGER,PRIMARY KEY(x,y,z,s)) WITHOUT ROWID')

    def add(self, cloud):
        points=cloud['points']
        if not len(points):return
        coords=np.floor(points/self.voxel_size).astype(np.int64)
        # 双目支持点与神经补全点分别写入体素。
        category=np.where(cloud['source']>=2,2,1)
        keys=np.column_stack([coords,category])
        _,first=np.unique(keys,axis=0,return_index=True)
        rows=(tuple(map(int,keys[i]))+tuple(map(float,points[i]))+tuple(map(float,cloud['colors'][i]))+
              (float(cloud['confidence'][i]),1,int(cloud['frames'][i])) for i in first)
        self.db.executemany('INSERT INTO voxels VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(x,y,z,s) DO UPDATE SET px=px+excluded.px,py=py+excluded.py,pz=pz+excluded.pz,r=r+excluded.r,g=g+excluded.g,b=b+excluded.b,c=c+excluded.c,n=n+1,f=excluded.f',rows)

    def commit(self):self.db.commit()

    def preview(self, limit=OVERVIEW_LIMIT):
        self.commit();parts=[]
        for category in (2,1):
            count=self.db.execute('SELECT count(*) FROM voxels WHERE s=?',(category,)).fetchone()[0]
            capacity=limit//2;stride=max(1,int(np.ceil(count/capacity)))
            rows=self.db.execute('SELECT px/n,py/n,pz/n,r/n,g/n,b/n,c/n,s,f FROM voxels WHERE s=?',(category,))
            selected=[row for i,row in enumerate(rows) if i%stride==0]
            if selected:parts.append(np.asarray(selected))
        a=np.concatenate(parts) if parts else np.empty((0,9))
        return dict(points=a[:,:3].astype(np.float32),colors=a[:,3:6].clip(0,255).astype(np.uint8),
                    confidence=a[:,6].astype(np.float32),source=a[:,7].astype(np.uint8),frames=a[:,8].astype(np.int32))

    def close(self):self.db.commit();self.db.close()


def geometry_zip(root):
    """逐块输出 ZIP64，避免生成完整中间副本。"""
    class Sink:
        def __init__(self):self.buffer=bytearray();self.position=0
        def write(self, data):self.buffer.extend(data);self.position+=len(data);return len(data)
        def tell(self):return self.position
        def flush(self):pass
        def take(self):data=bytes(self.buffer);self.buffer.clear();return data
    sink=Sink();root=Path(root)
    with zipfile.ZipFile(sink,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
        for path in sorted(root.glob('*')):
            if not path.is_file():continue
            with path.open('rb') as source, archive.open(path.name,'w',force_zip64=True) as dest:
                while True:
                    block=source.read(256*1024)
                    if not block:break
                    dest.write(block)
                    yield sink.take()
            yield sink.take()
    yield sink.take()
