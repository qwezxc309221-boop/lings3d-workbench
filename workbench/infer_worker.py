"""单会话流式推理、分块保存及双目融合。"""
import argparse
import json
import os
import resource
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault('PYTORCH_CUDA_ALLOC_CONF','expandable_segments:True')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'third_party/lingbot-map'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
import torch
from store import write_json
from stream_storage import ChunkWriter,ChunkReader,VoxelMap,iter_frames,safe_image,frame_cloud,disk_guard
from stream_fusion import StreamingFusion,save_comparison
from stream_runtime import configure_long_session,cap_special_cache
from demo import load_model,postprocess
from lingbot_map.utils.load_fn import load_and_preprocess_images

parser=argparse.ArgumentParser()
parser.add_argument('--input',required=True)
parser.add_argument('--output',required=True)
parser.add_argument('--model',required=True)
parser.add_argument('--mode',choices=['stereo','mono'],default='stereo')
args=parser.parse_args()
out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
segment=Path(args.input).parent;started=time.time()

def progress(phase,done=0,total=0,**extra):
    write_json(out/'progress.json',dict(phase=phase,done=done,total=total,elapsed=round(time.time()-started,1),**extra))

def sample_resources(phase,index):
    current_rss=int(Path('/proc/self/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')/1024**2
    with (out/'resources.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(phase=phase,frame=index,rss_current_mb=current_rss,rss_peak_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
            gpu_allocated_gb=torch.cuda.memory_allocated()/1e9,gpu_reserved_gb=torch.cuda.memory_reserved()/1e9))+'\n')

try:
    total=sum(1 for _ in iter_frames(segment))
    if total<2:raise ValueError('至少需要2对同步图像')
    if not torch.cuda.is_available():raise ValueError('未检测到CUDA')
    disk_guard(out)
    # 失败或取消后，从原图重放以重建模型状态。
    # 仅更新当前模式的结果，保留采集图像及另一模式的输出。
    for name in ['geometry','neural']:
        if (out/name).exists():shutil.rmtree(out/name)
    for name in ['map.sqlite','map.sqlite-journal','resources.jsonl','trajectory.jsonl','result.json','fusion_report.json']:
        (out/name).unlink(missing_ok=True)
    progress('loading',total=total)
    original_load=torch.load
    def cpu_load(*a,**kw):
        kw['map_location']='cpu';kw.setdefault('mmap',True);return original_load(*a,**kw)
    torch.load=cpu_load
    options=SimpleNamespace(mode='streaming',image_size=350,patch_size=14,enable_3d_rope=True,
        max_frame_num=max(1024,total+16),kv_cache_sliding_window=16,num_scale_frames=2,
        use_sdpa=True,camera_num_iterations=1,model_path=args.model)
    model=load_model(options,torch.device('cuda'))
    model.aggregator=model.aggregator.to(torch.bfloat16);model.eval()
    configure_long_session(model,total)
    model.clean_kv_cache();load_seconds=time.time()-started
    writer=ChunkWriter(out/'neural')
    rows=iter(iter_frames(segment));first=[next(rows),next(rows)]
    next_index=0;infer_start=time.time()
    def batches():
        yield first
        for row in rows:yield [row]
    with torch.no_grad(),torch.amp.autocast('cuda',dtype=torch.bfloat16):
        for batch_rows in batches():
            disk_guard(out)
            images=load_and_preprocess_images([str(safe_image(segment,'left',r['left'])) for r in batch_rows],mode='crop',image_size=350,patch_size=14)
            append=next_index==0 or (next_index-2)%8==0
            model._set_skip_append(not append)
            output=model.forward(images.unsqueeze(0).to('cuda'),num_frame_for_scale=2,
                num_frame_per_block=len(batch_rows),causal_inference=True)
            model._set_skip_append(False)
            predictions={k:output[k].detach().float().cpu() for k in ['pose_enc','depth','depth_conf']}
            del output
            cap_special_cache(model,64)
            # 解码当前小批次时抑制逐帧控制台输出。
            import contextlib,io
            with contextlib.redirect_stdout(io.StringIO()):
                predictions,images=postprocess(predictions,images)
            rgb=(images.permute(0,2,3,1).numpy()*255).clip(0,255).astype(np.uint8)
            for j in range(len(batch_rows)):
                frame={k:predictions[k][j].float().numpy() for k in ['depth','depth_conf','extrinsic','intrinsic']}
                frame['rgb']=rgb[j];writer.append(frame)
                next_index+=1
            if next_index%32==0 or next_index==total:sample_resources('neural',next_index)
            progress('running',next_index,total,committed_frames=writer.count)
            del predictions,images,rgb,frame
    writer.close();torch.cuda.synchronize()
    infer_seconds=time.time()-infer_start;peak=torch.cuda.max_memory_allocated()/1e9
    model.clean_kv_cache();del model;torch.cuda.empty_cache()
    # 进入 CPU 融合前释放权重相关内存。
    import gc
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except (OSError,AttributeError):pass
    neural=ChunkReader(out/'neural')
    shape=list(neural.frame(0)['rgb'].shape[:2])
    input_shape=[total,3,*shape]
    geometry=ChunkWriter(out/'geometry');voxel=VoxelMap(out/'map.sqlite')
    fusion=StreamingFusion(segment) if args.mode=='stereo' else None
    fusion_started=time.time()
    if fusion:
        # 逐帧读取最多 16 个候选帧，初始化固定米制尺度。
        pending=[]
        for i,row in enumerate(iter_frames(segment)):
            frame=neural.frame(i);stereo=fusion.stereo(row,frame);pending.append((frame,stereo))
            if len(pending)>=min(8,total):
                try:fusion.initialize_scale(pending);break
                except ValueError:
                    if len(pending)>=min(16,total):raise
            if i+1==total:fusion.initialize_scale(pending);break
        # 释放尺度初始化数组；主流程按有限块重新读取。
        del pending,frame,stereo
    best_support=-1
    with (out/'trajectory.jsonl').open('w') as trajectory:
        for i,row in enumerate(iter_frames(segment)):
            disk_guard(out);frame=neural.frame(i)
            if fusion:
                stereo=fusion.stereo(row,frame);frame,report=fusion.process(frame,stereo)
                support=int((frame['source']>=2).sum())
                if support>best_support:
                    save_comparison(out/'depth_comparison.png',frame,fusion.scale);best_support=support
            else:
                frame['source']=np.ones(frame['depth'].shape[:2],dtype=np.uint8)
                report=dict(frame=i,method='lingbot')
            trajectory.write(json.dumps(dict(report,extrinsic=frame['extrinsic'].tolist()))+'\n')
            geometry.append(frame)
            # 空间融合写入磁盘；完整逐像素几何按块保存。
            voxel.add(frame_cloud(frame,i,limit=10000))
            if (i+1)%16==0:voxel.commit();trajectory.flush()
            if (i+1)%32==0 or i+1==total:sample_resources('fusion',i+1)
            progress('fusion' if fusion else 'postprocessing',i+1,total,committed_frames=geometry.count)
    geometry.close()
    cloud=voxel.preview();voxel.close()
    np.savez_compressed(out/'cloud.npz',**cloud)
    stats=fusion.report() if fusion else {}
    if fusion:write_json(out/'fusion_report.json',dict(stats,trajectory_file='trajectory.jsonl'))
    metadata=dict(frames=total,input_shape=input_shape,mode=args.mode,format='chunked-v1',
        load_seconds=round(load_seconds,1),inference_seconds=round(infer_seconds,1),gpu_peak_gb=round(peak,2),
        stereo_fusion_seconds=round(time.time()-fusion_started,1),point_count=len(cloud['points']),
        geometry_chunks=len(geometry.chunks),overview_limit=300000,
        voxel_size_m=.02 if fusion else None,special_cache_limit=64,
        restart_policy='replay_original_frames',**stats)
    metadata.setdefault('metric_scale_verified',False)
    write_json(out/'result.json',metadata)
    # 最终几何保留原神经预测，临时预测块可清理。
    shutil.rmtree(out/'neural')
    progress('complete',total,total,committed_frames=total,**metadata)
except BaseException as error:
    import traceback
    traceback.print_exc()
    progress('failed',error=str(error) or type(error).__name__)
    sys.exit(1)
