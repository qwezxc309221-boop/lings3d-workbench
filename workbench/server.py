"""工作台控制服务：采集状态、重建任务和结果下载。"""
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import socket

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request as APIRequest
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from store import Store, now, write_json, recover_capture
from results import result_path
from stream_storage import geometry_zip

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parent
ROS_ROOT=Path(os.environ.get('LINGS_ROS_ROOT',str(PROJECT/'docker/catkin_ws/src/lings_3d'))).expanduser()
DATA=Path(os.environ.get('LINGS_SCAN_ROOT',str(ROS_ROOT/'scans')))
CONTAINER_DATA='/root/catkin_ws/src/lings_3d/scans'
PYTHON=os.environ.get('LINGS_PYTHON',sys.executable)
MODEL=str(Path(os.environ.get('LINGS_MODEL',str(Path.home()/'models/lingbot-map/lingbot-map.pt'))).expanduser())
BRIDGE='http://127.0.0.1:8092'
RUN=HERE/'runtime'
RUN.mkdir(exist_ok=True)
store=Store(DATA)
lock=threading.RLock()
processes={}
connection={'state':'disconnected','message':'尚未连接相机'}
job=None
selected=None
selected_segment=0
reconstruction_mode='stereo'
view=None
version=0
stopping=False


def changed():
    global version
    version+=1


def bridge(path='/status', data=None, timeout=2):
    req=Request(BRIDGE+path, data=json.dumps(data).encode() if data is not None else None,
                headers={'Content-Type':'application/json'})
    try:
        with urlopen(req,timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        raise ValueError(json.loads(error.read()).get('error','采集服务拒绝操作'))
    except (URLError,TimeoutError,OSError):
        raise ValueError('采集服务未就绪，请先连接相机')


def spawn(name,args,cwd=HERE):
    current=processes.get(name)
    if current and current.poll() is None:
        raise ValueError('该进程已在运行')
    logfile=open(RUN/(name+'.log'),'ab',buffering=0)
    try:
        proc=subprocess.Popen(args,cwd=str(cwd),stdout=logfile,stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL,start_new_session=True,
                              env=dict(os.environ,PYTHONUNBUFFERED='1'))
    finally:
        logfile.close()
    processes[name]=proc
    return proc


def stop(name):
    proc=processes.get(name)
    if proc and proc.poll() is None:
        try:
            os.killpg(proc.pid,signal.SIGINT)
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGTERM)
            proc.wait(timeout=5)
    processes.pop(name,None)


def docker(command,timeout=15):
    result=subprocess.run(['docker']+command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          timeout=timeout,text=True)
    if result.returncode:
        raise ValueError(result.stdout[-1500:])
    return result.stdout


def connect_camera():
    global connection
    try:
        reachable=subprocess.run(['ping','-c','1','-W','2','192.168.0.12'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if reachable.returncode:
            raise ValueError('相机 192.168.0.12 未响应，请检查电源和有线连接')
        docker(['start','lings3d'])
        # 仅部署工作台桥接及启动文件，保留原启动文件和标定。
        for source,target in [('ros_bridge.py','workbench_ros_bridge.py'),('camera.launch','workbench_camera.launch')]:
            docker(['cp',str(HERE/source),'lings3d:/tmp/'+target])
        docker(['exec','lings3d','mkdir','-p',CONTAINER_DATA])
        # 检查 ROS 同步节点，按需启动工作台链路。
        check=docker(['exec','lings3d','/entrypoint.sh','bash','-c',
            'timeout 3 rosnode list 2>/dev/null || true'])
        if '/master_sync' not in check:
            spawn('camera',['docker','exec','lings3d','/entrypoint.sh','bash','-c',
                            'exec roslaunch /tmp/workbench_camera.launch'])
        time.sleep(3)
        # 显式清理容器内桥接进程，避免 docker exec 退出后残留。
        docker(['exec','lings3d','bash','-c',
                "pkill -f '^python -u /tmp/workbench_ros_bridge.py' || true"])
        spawn('bridge',['docker','exec','lings3d','/entrypoint.sh','python','-u',
                         '/tmp/workbench_ros_bridge.py','--root',CONTAINER_DATA])
        for _ in range(20):
            if stopping: return
            time.sleep(0.5)
            try:
                state=bridge()
                if state['connected'] and state['calibrated']:
                    connection={'state':'connected','message':'左右校正图像及标定已就绪'}
                    changed();return
            except ValueError:
                pass
        connection={'state':'waiting','message':'接入已启动，等待同步图像/标定；请查看连接日志'}
    except Exception as error:
        connection={'state':'error','message':str(error)}
    changed()


def snapshot():
    try:
        camera=bridge(timeout=0.6)
    except ValueError:
        camera={'connected':False,'calibrated':False,'rates':{},'error':'采集服务未运行'}
    with lock:
        return dict(version=version, connection=connection, camera=camera, scans=store.list(),
                    trash=store.list(True), job=job, selected=selected, selected_segment=selected_segment, reconstruction_mode=reconstruction_mode, view=view,
                    disk_free_gb=round(shutil.disk_usage(DATA).free/1e9,1), server_time=now())


def open_view(scan_id, segment, mode='stereo'):
    global view
    record=store.read(scan_id)
    if segment<0 or segment>=len(record['segments']): raise ValueError('拍摄段不存在')
    result=result_path(store.path(scan_id),record['segments'][segment],mode)
    if not (result/'result.json').exists(): raise ValueError('此段还没有重建结果')
    if view and view['scan_id']==scan_id and view['segment']==segment and view.get('mode','mono')==mode:
        proc=processes.get('viewer')
        if proc and proc.poll() is None: return
    stop('viewer')
    proc=spawn('viewer',[PYTHON,str(HERE/'viewer.py'),'--result',str(result)])
    for attempt in range(60):
        if proc.poll() is not None: raise ValueError('三维显示启动失败，请查看显示日志')
        try:
            with urlopen('http://127.0.0.1:8091',timeout=0.3) as response:
                if response.status==200: break
        except (URLError,OSError,TimeoutError): time.sleep(0.1)
    else:
        stop('viewer')
        raise ValueError('三维显示启动超时，请重新打开结果')
    view=dict(scan_id=scan_id,segment=segment,mode=mode,url='http://127.0.0.1:8091',pid=proc.pid)


def monitor_job(scan_id,segment,proc,result,mode):
    global job,selected_segment
    while proc.poll() is None and not stopping:
        try:
            progress=json.loads((result/'progress.json').read_text())
            with lock:
                if job and job['scan_id']==scan_id:
                    job.update({k:v for k,v in progress.items() if k!='source_images'})
        except (OSError,ValueError): pass
        time.sleep(0.5)
    proc.wait()
    with lock:
        if not job or job['scan_id']!=scan_id: return
        # 轮询间进程可能已结束，仍读取最终指标。
        try:
            progress=json.loads((result/'progress.json').read_text())
            job.update({k:v for k,v in progress.items() if k!='source_images'})
        except (OSError,ValueError): pass
        if job.get('cancelled'):
            state='cancelled'
        else:
            state='complete' if proc.returncode==0 and (result/'result.json').exists() else 'failed'
        job['status']=state
        job['phase']=state
        if state=='complete':job['committed_frames']=job['total']
        if state=='failed':
            try: job['error']=json.loads((result/'progress.json').read_text()).get('error','推理进程退出')
            except (OSError,ValueError): job['error']='推理进程退出，请查看任务日志'
        record=store.read(scan_id)
        inference=dict(job)
        record['segments'][segment].setdefault('results',{})[mode]=state
        if mode=='mono': record['segments'][segment]['result_status']=state
        write_json(store.path(scan_id)/'scan.json',dict(record,inference=inference,updated_at=now()))
        if state=='complete':
            selected_segment=segment
            try: open_view(scan_id,segment,mode)
            except Exception as error: job['viewer_error']=str(error)
        changed()


def cleanup():
    global stopping
    stopping=True
    if processes.get('bridge'):
        try:
            state=bridge()
            if state.get('active'):
                bridge('/pause',{'id':state['active']['id']})
        except ValueError: pass
        try: docker(['exec','lings3d','bash','-c',"pkill -f '^python -u /tmp/workbench_ros_bridge.py' || true"])
        except Exception: pass
    if processes.get('camera'):
        try: docker(['exec','lings3d','bash','-c',"pkill -INT -f '^/usr/bin/python /opt/ros/melodic/bin/roslaunch /tmp/workbench_camera.launch' || true"])
        except Exception: pass
    for name in list(processes):
        try: stop(name)
        except Exception: pass


@asynccontextmanager
async def lifespan(app):
    # 重启时恢复中断任务状态，保留已有文件。
    for record in store.list():
        if record['status'] in ('recording','paused'):
            recover_capture(store.path(record['id']),record)
        if record.get('inference',{}).get('status') in ('running','loading'):
            for seg in record['segments']:
                for mode,status in seg.get('results',{}).items():
                    if status=='running':seg['results'][mode]='interrupted'
            store.update(record['id'],segments=record['segments'],inference={'status':'interrupted','error':'上次工作台退出，可重新推理；将从原图重放恢复模型状态'})
    yield
    await asyncio.to_thread(cleanup)

app=FastAPI(lifespan=lifespan)


@app.middleware('http')
async def local_requests(request:APIRequest,call_next):
    # 拒绝跨站页面发起的写入请求。
    host=request.headers.get('host','').split(':')[0]
    origin=request.headers.get('origin')
    if host not in ('127.0.0.1','localhost','testserver'):
        return JSONResponse({'detail':'仅允许本机访问'},status_code=403)
    if request.method not in ('GET','HEAD') and origin and origin not in ('http://localhost:8080','http://127.0.0.1:8080'):
        return JSONResponse({'detail':'请求来源不匹配'},status_code=403)
    return await call_next(request)


@app.exception_handler(ValueError)
async def value_error(request,exc):
    return JSONResponse({'detail':str(exc)},status_code=409)


@app.exception_handler(PermissionError)
async def permission_error(request,exc):
    return JSONResponse({'detail':'数据目录不可写，请查看采集目录权限'},status_code=409)


@app.exception_handler(FileNotFoundError)
async def missing(request,exc):
    return JSONResponse({'detail':'扫描或文件不存在'},status_code=404)


class Command(BaseModel):
    version:int
    action:str
    id:str|None=None
    name:str='新扫描'
    segment:int=Field(default=0,ge=0)
    mode:str=Field(default='stereo',pattern='^(mono|stereo)$')


@app.get('/api/state')
def state(): return snapshot()


@app.websocket('/ws')
async def websocket(ws:WebSocket):
    if ws.headers.get('origin') not in ('http://localhost:8080','http://127.0.0.1:8080',None):
        await ws.close(code=1008);return
    await ws.accept()
    try:
        while True:
            await ws.send_json(await asyncio.to_thread(snapshot))
            await asyncio.sleep(1)
    except (WebSocketDisconnect,RuntimeError): pass


@app.post('/api/command')
def command(cmd:Command):
    global selected,selected_segment,reconstruction_mode,job,view,connection
    with lock:
        if cmd.version!=version:
            raise ValueError('状态已改变，请稍候重试，避免重复操作')
        action=cmd.action
        if action in ('select','segment','start','pause','save','delete','restore','infer','view') and not cmd.id:
            raise ValueError('请先选择扫描任务')
        if action=='connect':
            if connection['state']=='connecting': raise ValueError('正在连接')
            if any(processes.get(name) and processes[name].poll() is None for name in ('bridge',)):
                try:
                    camera=bridge()
                    if camera.get('active'): raise ValueError('请先结束并保存当前拍摄，再重新连接')
                    if camera['connected']: return snapshot()
                except ValueError as error:
                    if str(error)!='采集服务未就绪，请先连接相机': raise
                stop('bridge')
            connection={'state':'connecting','message':'正在检查相机并启动 ROS 接入…'}
            threading.Thread(target=connect_camera,daemon=True).start()
        elif action=='disconnect':
            camera=bridge()
            if camera.get('active'): raise ValueError('请先结束并保存拍摄')
            docker(['exec','lings3d','bash','-c',"pkill -f '^python -u /tmp/workbench_ros_bridge.py' || true"])
            stop('bridge')
            connection={'state':'disconnected','message':'采集已断开，已保存数据可继续查看'}
        elif action=='new':
            active=bridge().get('active') if processes.get('bridge') else None
            if active: raise ValueError('请先保存当前拍摄')
            selected=store.create(cmd.name)['id'];selected_segment=0
        elif action=='select':
            store.read(cmd.id);selected=cmd.id;selected_segment=0
        elif action=='mode':
            reconstruction_mode=cmd.mode
        elif action=='segment':
            record=store.read(cmd.id)
            if cmd.segment>=len(record['segments']): raise ValueError('拍摄段不存在')
            selected=cmd.id;selected_segment=cmd.segment
        elif action in ('start','pause','save'):
            record=store.read(cmd.id)
            if action=='pause' and record['status']!='recording': raise ValueError('当前未在拍摄')
            if job and job['status'] in ('loading','running'): raise ValueError('请等待推理完成再拍摄')
            if action=='start' and shutil.disk_usage(DATA).free<2e9: raise ValueError('磁盘空间不足 2 GB，停止采集')
            if action=='save' and record['status'] in ('draft','interrupted'):
                store.update(cmd.id,status='saved')
            else:
                bridge('/'+action,{'id':cmd.id,'fps':3},timeout=20)
            selected=cmd.id
        elif action in ('delete','restore'):
            if job and job['scan_id']==cmd.id and job['status'] in ('loading','running'): raise ValueError('请先取消推理')
            if view and view['scan_id']==cmd.id:
                stop('viewer');view=None
            store.move(cmd.id,restore=action=='restore')
            if selected==cmd.id: selected=None
        elif action=='infer':
            record=store.read(cmd.id)
            if record['status']!='saved': raise ValueError('请先结束并保存扫描')
            try:
                if bridge().get('active'): raise ValueError('请先保存当前拍摄')
            except ValueError as error:
                if str(error)!='采集服务未就绪，请先连接相机': raise
            if job and job['status'] in ('loading','running'): raise ValueError('已有推理任务正在运行')
            if cmd.segment>=len(record['segments']): raise ValueError('拍摄段不存在')
            seg=record['segments'][cmd.segment]
            if seg['frames']<2: raise ValueError('至少需要 2 对图像')
            if shutil.disk_usage(DATA).free<2e9: raise ValueError('磁盘空间不足 2 GB')
            result=result_path(store.path(cmd.id),seg,cmd.mode)
            if cmd.mode=='stereo':
                segment_path=store.path(cmd.id)/seg['path']
                if not (segment_path/'calibration.json').is_file() or not (segment_path/'right').is_dir():
                    raise ValueError('缺少双目标定或右目图像，无法双目融合')
            if view and view['scan_id']==cmd.id and view['segment']==cmd.segment and view.get('mode','mono')==cmd.mode:
                stop('viewer');view=None
            result.mkdir(exist_ok=True)
            # 启动新任务前隔离旧结果，避免将旧成功状态用于失败任务。
            for name in ('progress.json','result.json','pointcloud.ply','pointcloud.glb'):
                (result/name).unlink(missing_ok=True)
            job=dict(scan_id=cmd.id,segment=cmd.segment,status='loading',phase='loading',done=0,total=seg['frames'],mode=cmd.mode)
            record['segments'][cmd.segment].setdefault('results',{})[cmd.mode]='running'
            if cmd.mode=='mono':record['segments'][cmd.segment]['result_status']='running'
            write_json(store.path(cmd.id)/'scan.json',dict(record,inference=job,updated_at=now()))
            proc=spawn('inference',[PYTHON,str(HERE/'infer_worker.py'),'--input',str(store.path(cmd.id)/seg['path']/'left'),
                                   '--output',str(result),'--model',MODEL,'--mode',cmd.mode])
            job['status']='running'
            threading.Thread(target=monitor_job,args=(cmd.id,cmd.segment,proc,result,cmd.mode),daemon=True).start()
            selected=cmd.id;selected_segment=cmd.segment;reconstruction_mode=cmd.mode
        elif action=='cancel':
            if not job or job['status'] not in ('loading','running'): raise ValueError('没有运行中的推理')
            job['cancelled']=True
            stop('inference')
        elif action=='view':
            open_view(cmd.id,cmd.segment,cmd.mode)
            selected=cmd.id;selected_segment=cmd.segment;reconstruction_mode=cmd.mode
        else: raise ValueError('未知操作')
        changed()
        return snapshot()


@app.get('/api/preview/{side}')
def preview(side:str):
    if side not in ('left','right'): raise HTTPException(404)
    try:
        with urlopen(BRIDGE+'/preview/'+side,timeout=1) as response:
            return Response(response.read(),media_type='image/jpeg',headers={'Cache-Control':'no-store'})
    except Exception: return Response(status_code=204)


@app.get('/api/log/{name}')
def log(name:str):
    if name not in ('camera','bridge','inference','viewer'): raise HTTPException(404)
    path=RUN/(name+'.log')
    content=path.read_bytes()[-16000:].decode('utf-8',errors='replace') if path.exists() else '暂无日志'
    return {'text':content}


@app.get('/api/download/{scan_id}/{segment}/{filename}')
def download(scan_id:str,segment:int,filename:str,mode:str='stereo'):
    record=store.read(scan_id)
    if not 0<=segment<len(record['segments']): raise HTTPException(404)
    if filename not in ('geometry.npz','geometry.zip','result.json','fusion_report.json','trajectory.jsonl','resources.jsonl','depth_comparison.png','pointcloud.ply','pointcloud.glb'): raise HTTPException(404)
    try: result=result_path(store.path(scan_id),record['segments'][segment],mode)
    except ValueError as error: raise HTTPException(400,str(error))
    if not (result/'result.json').is_file(): raise HTTPException(404,'此结果尚未完成，请先重建')
    if filename=='geometry.zip':
        if not (result/'geometry/index.json').is_file(): raise HTTPException(404,'旧结果请下载NPZ')
        return StreamingResponse(geometry_zip(result/'geometry'),media_type='application/zip',
            headers={'Content-Disposition':'attachment; filename="geometry.zip"'})
    if filename=='geometry.npz' and (result/'geometry/index.json').is_file():
        return StreamingResponse(geometry_zip(result/'geometry'),media_type='application/zip',
            headers={'Content-Disposition':'attachment; filename="geometry.zip"'})
    path=result/filename
    if not path.is_file(): raise HTTPException(404,'请先在三维视口导出对应文件')
    return FileResponse(path,filename=filename)

app.mount('/',StaticFiles(directory=HERE/'static',html=True),name='static')
