"""从数值结果加载点云，提供回放、过滤和导出。"""
import argparse
import time
import threading
import json
from pathlib import Path
import numpy as np
import viser
import trimesh
from stream_storage import ChunkReader, frame_cloud

parser=argparse.ArgumentParser()
parser.add_argument('--result', required=True)
parser.add_argument('--port', type=int, default=8091)
args=parser.parse_args()
root=Path(args.result)
with np.load(root/'cloud.npz', allow_pickle=False) as data:
    points=data['points']; colors=data['colors']; confidence=data['confidence']; frames=data['frames']
    sources=data['source'] if 'source' in data else np.ones(len(points),dtype=np.uint8)
metadata=json.loads((root/'result.json').read_text())
chunked=metadata.get('format')=='chunked-v1'
geometry=ChunkReader(root/'geometry') if chunked else None
total_frames=metadata['frames']
is_stereo=metadata.get('mode') in ('stereo','stereo_lingbot_fusion')
# 居中与缩放仅作用于显示；导出保留原始预测坐标。
center=np.median(points,axis=0)
extent=np.percentile(points,95,axis=0)-np.percentile(points,5,axis=0)
display_scale=2.0/max(float(np.linalg.norm(extent)),1e-6)
server=viser.ViserServer(host='127.0.0.1', port=args.port)
server.gui.configure_theme(control_layout='collapsible')
server.gui.add_markdown('## 双目视差 + LingBot 融合重建\n基线提供物理尺度，实际测量精度未验证。默认仅显示双目支持区域。' if is_stereo else '## 单目神经重建\n鼠标拖动旋转、滚轮缩放。真实尺寸尚未验证。')
if is_stereo:
    server.gui.add_markdown(f"有效双目像素：{metadata['valid_stereo_fraction']:.1%} · 神经深度尺度系数：{metadata['scale']:.3f}\n\n视差深度筛选：0.15–4.0 米（估计范围，非精度保证）。\n\n双目约束位姿：{metadata['stereo_pnp_frames']}/{total_frames-1} 帧；其余使用尺度校准后的神经相对运动。")
if chunked:
    server.gui.add_markdown(f"长序列分块结果 · {total_frames} 帧\n\n全局概览最多30万点；当前帧按需读取磁盘。导出PLY/GLB反映概览/当前帧，完整逐帧几何请下载分块ZIP。")
source_mode=server.gui.add_dropdown('点云来源',options=['双目支持区域','包含神经补全','仅神经补全'],initial_value='双目支持区域' if is_stereo else '包含神经补全')
source_colors=server.gui.add_checkbox('按来源着色（绿双目 / 橙补全）',initial_value=False)
mode=server.gui.add_dropdown('显示方式', options=['全部帧','当前帧'], initial_value='全部帧')
step=server.gui.add_slider('播放帧', min=0, max=max(1,total_frames-1), step=1, initial_value=total_frames-1)
playing=server.gui.add_checkbox('播放回放', initial_value=False)
threshold=server.gui.add_slider('过滤低置信点（百分比）',min=0,max=95,step=5,initial_value=40)
size=server.gui.add_slider('点大小', min=0.0001,max=0.03,step=0.0001,initial_value=0.003)
stride=server.gui.add_slider('显示降采样',min=1,max=10,step=1,initial_value=2)
export_ply=server.gui.add_button('导出当前过滤点云 PLY')
export_glb=server.gui.add_button('导出当前过滤点云 GLB')
status=server.gui.add_markdown('导出文件可在工作台点击下载。相同结果的播放和显示参数在窗口之间共享。')
node=None
render_lock=threading.RLock()

def filtered():
    if geometry and mode.value=='当前帧':
        cloud=frame_cloud(geometry.frame(int(step.value)),int(step.value),limit=10000)
        xyz,color,conf,src,ids=(cloud[k] for k in ('points','colors','confidence','source','frames'))
    else:xyz,color,conf,src,ids=points,colors,confidence,sources,frames
    selected=np.ones(len(xyz),dtype=bool)
    if source_mode.value=='双目支持区域':selected=src>=2
    elif source_mode.value=='仅神经补全':selected=src==1
    values=conf[selected]
    mask=selected & (conf>=np.percentile(values,threshold.value)) if len(values) else selected
    if mode.value=='当前帧': mask &= ids==step.value
    indices=np.flatnonzero(mask)[::stride.value]
    rgb=color[indices].copy()
    if source_colors.value:
        rgb[src[indices]>=2]=[35,190,125]
        rgb[src[indices]==1]=[235,150,45]
    return xyz[indices],rgb

def update(_=None):
    global node
    with render_lock:
        xyz,rgb=filtered()
        node=server.scene.add_point_cloud('/reconstruction',points=(xyz-center)*display_scale,colors=rgb,point_size=size.value)
for control in (mode,step,threshold,size,stride,source_mode,source_colors): control.on_update(update)

@export_ply.on_click
def ply(_):
    with render_lock:xyz,rgb=filtered()
    if not len(xyz): return
    trimesh.points.PointCloud(xyz,colors=rgb).export(root/'pointcloud.ply')
    status.content='已导出 PLY，可回工作台下载。'

@export_glb.on_click
def glb(_):
    with render_lock:xyz,rgb=filtered()
    if not len(xyz): return
    trimesh.Scene(trimesh.points.PointCloud(xyz,colors=rgb)).export(root/'pointcloud.glb')
    status.content='已导出 GLB 点云，可回工作台下载；这不是连续表面网格。'
reset=server.gui.add_button('重置观察视角')

def reset_camera(client):
    client.camera.position=(2.5,-2.5,2.0)
    client.camera.look_at=(0.,0.,0.)
    client.camera.up_direction=(0.,-1.,0.)

@server.on_client_connect
def connect(client):
    reset_camera(client)

@reset.on_click
def reset_all(event):
    if event.client is not None: reset_camera(event.client)

update()
while True:
    time.sleep(0.2)
    if playing.value:
        step.value=(int(step.value)+1) % total_frames
