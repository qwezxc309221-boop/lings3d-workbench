#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ROS Melodic/Python 2 采集桥接，负责同步、校正与保存。"""
from __future__ import print_function
import argparse
import json
import os
import copy
import threading
import time
import uuid
from collections import deque
try:
    import Queue as queue
except ImportError:
    import queue
try:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
    from SocketServer import ThreadingMixIn
    from urlparse import urlparse
except ImportError:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from socketserver import ThreadingMixIn
    from urllib.parse import urlparse
import cv2
import numpy as np
import rospy
import message_filters
from sensor_msgs.msg import Image, CameraInfo
from cv_bridge import CvBridge


def atomic_json(path, value):
    temporary = path + '.tmp'
    with open(temporary, 'w') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
    os.rename(temporary, path)


def stamp():
    return time.strftime('%Y-%m-%dT%H:%M:%S+08:00', time.gmtime(time.time()+8*3600))


class Bridge(object):
    def __init__(self, root):
        self.root = os.path.realpath(root)
        self.lock = threading.RLock()
        self.cv = CvBridge()
        self.info = {}
        self.latest = {}
        self.maps = {}
        self.map_signatures = {}
        self.preview_times = {}
        self.counts = {'left': 0, 'right': 0, 'pairs': 0}
        self.times = {key: deque(maxlen=90) for key in self.counts}
        self.last_pair = 0
        self.error = ''
        self.active = None
        self.chunk_frames = 300
        self.write_queue = queue.Queue(maxsize=4)
        self.accepting = False
        self.last_persist = 0
        self.write_dropped = 0
        self.pending_write_error = False
        self.fps = 3
        self.last_save = 0
        self.subscriptions = []
        for side in ('left', 'right'):
            self.subscriptions.append(rospy.Subscriber('/sensor_stereo_2/' + side + '/camera_info', CameraInfo,
                                                       self.camera_info, callback_args=side, queue_size=1))
        # 订阅同步原图，使用已有 CameraInfo 校正。
        # 避免与远程同步的 stereo_image_proc 节点冲突。
        self.left = message_filters.Subscriber('/sensor_stereo_2/left/image_raw', Image, queue_size=2)
        self.right = message_filters.Subscriber('/sensor_stereo_2/right/image_raw', Image, queue_size=2)
        self.left.registerCallback(self.preview, 'left')
        self.right.registerCallback(self.preview, 'right')
        self.sync = message_filters.TimeSynchronizer([self.left, self.right], 8)
        self.sync.registerCallback(self.pair)
        self.recover()
        worker = threading.Thread(target=self.writer_loop)
        worker.daemon = True
        worker.start()

    def recover(self):
        for scan_id in os.listdir(self.root):
            if len(scan_id) != 32:
                continue
            path = os.path.join(self.root, scan_id, 'scan.json')
            try:
                with open(path) as stream:
                    record = json.load(stream)
                if record['status'] in ('recording', 'paused'):
                    record['status'] = 'interrupted'
                    # 快照写入中断时，以已提交帧日志为准。
                    for segment in record['segments']:
                        manifest = os.path.join(self.root, scan_id, segment['path'], 'frames.jsonl')
                        count = 0
                        if os.path.isfile(manifest):
                            with open(manifest) as frames:
                                for line in frames:
                                    try:
                                        row = json.loads(line)
                                        if row.get('index') != count: break
                                        if any(not os.path.isfile(os.path.join(self.root,scan_id,segment['path'],side,row[side])) for side in ('left','right')): break
                                        count += 1
                                    except (ValueError,KeyError): break
                        segment['frames'] = count
                    record['frames'] = sum(seg['frames'] for seg in record['segments'])
                    atomic_json(path, record)
            except (IOError, ValueError, KeyError):
                pass

    def camera_info(self, msg, side):
        value = dict(width=msg.width, height=msg.height, distortion_model=msg.distortion_model,
                     K=list(msg.K), D=list(msg.D), R=list(msg.R), P=list(msg.P),
                     frame_id=msg.header.frame_id, stamp_ns=msg.header.stamp.to_nsec())
        with self.lock:
            self.info[side] = value
            signature=repr([value[k] for k in ('K','D','R','P','width','height','distortion_model')])
            if self.map_signatures.get(side)==signature: return
            if self.active and self.active['status']=='recording' and side in self.map_signatures:
                self.accepting=False
                self.pending_write_error=True
                self.active['status']='paused'
                self.active['notice']='calibration_changed'
                self.error='Calibration changed during capture; start a new segment'
                self.persist()
            self.maps.pop(side,None)
            if value['K'][0] and value['P'][0]:
                K = np.array(value['K']).reshape(3,3)
                D = np.array(value['D'])
                R = np.array(value['R']).reshape(3,3)
                P = np.array(value['P']).reshape(3,4)[:,:3]
                if value['distortion_model'] not in ('plumb_bob', 'rational_polynomial'):
                    self.error = 'Unsupported distortion model: ' + value['distortion_model']
                    return
                self.map_signatures[side] = signature
                self.maps[side] = cv2.initUndistortRectifyMap(K, D, R, P, (value['width'],value['height']), cv2.CV_32FC1)

    def rectified(self, msg, side):
        image = self.cv.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        if side not in self.maps:
            return image
        return cv2.remap(image, self.maps[side][0], self.maps[side][1], cv2.INTER_LINEAR)

    def preview(self, msg, side):
        try:
            with self.lock:
                self.counts[side] += 1
                self.times[side].append(time.time())
                if time.time() - self.preview_times.get(side,0) < 0.2: return
                self.preview_times[side] = time.time()
            image = self.rectified(msg, side)
            ok, encoded = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 75])
            with self.lock:
                if ok:
                    self.latest[side] = encoded.tobytes()
        except Exception as error:
            self.error = str(error)

    def pair(self, left, right):
        with self.lock:
            current = time.time()
            self.counts['pairs'] += 1
            self.times['pairs'].append(current)
            self.last_pair = current
            if not self.active or self.active['status'] != 'recording' or not self.accepting:
                return
            if current-self.last_save < 1.0/self.fps: return
            self.last_save = current
            try:
                self.write_queue.put_nowait((self.active['id'],self.active['segments'][-1]['id'],left,right))
            except queue.Full:
                self.write_dropped += 1
                self.accepting = False
                self.active['status'] = 'paused'
                self.active['notice'] = 'writer_overload'
                self.error = 'Writer queue full; recording paused'
                self.persist()

    def writer_loop(self):
        while True:
            item = self.write_queue.get()
            try:
                self.write_pair(*item)
            finally:
                self.write_queue.task_done()

    def write_pair(self, scan_id, segment_id, left, right):
        with self.lock:
            if not self.active or self.active['id'] != scan_id or self.active['segments'][-1]['id'] != segment_id:
                return
            if getattr(self,'pending_write_error',False):return
            try:
                if set(self.info) != set(('left','right')): raise ValueError('CameraInfo not ready')
                if any(not v['K'][0] or not v['P'][0] for v in self.info.values()): raise ValueError('CameraInfo uncalibrated')
                if any((msg.width != self.info[side]['width'] or msg.height != self.info[side]['height']) for msg,side in ((left,'left'),(right,'right'))):
                    raise ValueError('Calibration and image dimensions differ')
                disk = os.statvfs(self.root)
                if disk.f_bavail*disk.f_frsize < 2*1024*1024*1024: raise IOError('Disk space below 2 GiB')
                segment = self.active['segments'][-1]
                folder = os.path.join(self.root,scan_id,segment['path'])
                # 磁盘分块沿用同一轨迹及模型会话。
                chunk = '%06d' % (segment['frames']//self.chunk_frames)
                filename = chunk+'/%06d.png' % segment['frames']
                for side,message in (('left',left),('right',right)):
                    parent = os.path.join(folder,side,chunk)
                    if not os.path.isdir(parent): os.makedirs(parent)
                    image = self.rectified(message,side)
                    temp = os.path.join(folder,side,filename+'.tmp.png')
                    if not cv2.imwrite(temp,image): raise IOError('Failed writing PNG')
                    os.rename(temp,os.path.join(folder,side,filename))
                with open(os.path.join(folder,'frames.jsonl'),'a') as stream:
                    stream.write(json.dumps(dict(index=segment['frames'],left=filename,right=filename,
                        left_stamp_ns=left.header.stamp.to_nsec(),right_stamp_ns=right.header.stamp.to_nsec(),
                        left_frame_id=left.header.frame_id,right_frame_id=right.header.frame_id))+'\n')
                    stream.flush()
                segment['frames'] += 1
                segment['disk_chunks'] = segment['frames']//self.chunk_frames+bool(segment['frames']%self.chunk_frames)
                self.active['frames'] += 1
                if time.time()-self.last_persist >= 2: self.persist()
            except Exception as error:
                self.error = str(error)
                self.accepting = False
                self.pending_write_error = True
                self.active['status'] = 'paused'
                self.active['notice'] = 'write_error: '+str(error)
                self.persist()

    def persist(self):
        self.last_persist = time.time()
        self.active['updated_at'] = stamp()
        atomic_json(os.path.join(self.root, self.active['id'], 'scan.json'), self.active)

    def status(self):
        with self.lock:
            if self.active and self.active['status']=='recording' and time.time()-self.last_pair>3:
                self.active['status']='paused'
                self.accepting=False
                self.active['notice']='stream_lost'
                self.persist()
            rates = {}
            for key, values in self.times.items():
                rates[key] = round((len(values) - 1) / (values[-1] - values[0]), 1) if len(values) > 1 and time.time() - values[-1] < 3 else 0
            valid = (set(self.info) == set(('left', 'right')) and
                     all(v['K'][0] > 0 and v['P'][0] > 0 for v in self.info.values()) and len(self.maps) == 2)
            return dict(connected=time.time() - self.last_pair < 3, counts=self.counts.copy(), rates=rates,
                        pair_age=round(time.time()-self.last_pair, 1) if self.last_pair else None,
                        calibrated=valid, calibration=copy.deepcopy(self.info), active=copy.deepcopy(self.active), error=self.error, writer_pending=self.write_queue.qsize(), writer_dropped=self.write_dropped)

    def command(self, action, payload):
        # 停止入队后释放写入锁，等待队列完成。
        if action in ('start','pause','save'):
            with self.lock:
                if action in ('pause','save') and (not self.active or self.active['id'] != payload['id']):
                    raise ValueError('No matching capture')
                if action == 'start' and self.active and self.active['status']=='recording':
                    raise ValueError('Already recording')
                self.accepting = False
            deadline=time.time()+15
            while self.write_queue.unfinished_tasks:
                if time.time()>deadline: raise ValueError('Writer drain timeout; recording stopped, retry save')
                time.sleep(.02)
        with self.lock:
            if action == 'start':
                scan_id = payload['id']
                if len(scan_id) != 32 or any(c not in '0123456789abcdef' for c in scan_id):
                    raise ValueError('Invalid scan ID')
                if not self.status()['connected'] or not self.status()['calibrated']:
                    raise ValueError('Synchronized images / calibration not ready')
                if self.active and self.active['status'] == 'recording':
                    raise ValueError('Already recording')
                if self.active and self.active['id'] != scan_id and self.active['status'] == 'paused':
                    raise ValueError('Save previous scan first')
                with open(os.path.join(self.root, scan_id, 'scan.json')) as stream:
                    self.active = json.load(stream)
                if self.active['status'] not in ('draft', 'paused', 'interrupted'):
                    raise ValueError('Scan cannot be recorded')
                segment = dict(id=len(self.active['segments']), frames=0,
                               path='segments/%03d' % len(self.active['segments']), created_at=stamp())
                folder = os.path.join(self.root, scan_id, segment['path'])
                for side in ('left', 'right'):
                    os.makedirs(os.path.join(folder, side))
                atomic_json(os.path.join(folder, 'calibration.json'), self.info)
                self.active['segments'].append(segment)
                self.active['status'] = 'recording'
                self.active.pop('notice', None)
                self.last_save = 0
                self.fps = max(1, min(5, int(payload.get('fps', 3))))
                self.chunk_frames = 300
                self.active['capture_format'] = 'continuous-v1'
                self.accepting = True
                self.pending_write_error = False
                self.persist()
            elif action in ('pause', 'save'):
                if not self.active or self.active['id'] != payload['id']:
                    raise ValueError('No matching capture')
                self.active['status'] = 'paused' if action == 'pause' else 'saved'
                if action == 'save': self.active.pop('notice', None)
                self.persist()
                if action == 'save':
                    self.active = None
            else:
                raise ValueError('Unknown command')
            return self.status()


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, data, content_type='application/json'):
        if content_type == 'application/json':
            data = json.dumps(data).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = urlparse(self.path).path
        if route == '/status':
            return self.send(200, bridge.status())
        if route in ('/preview/left', '/preview/right'):
            with bridge.lock:
                data = bridge.latest.get(route.rsplit('/', 1)[-1])
            return self.send(200, data, 'image/jpeg') if data else self.send(404, {'error': 'No image'})
        self.send(404, {})

    def do_POST(self):
        if self.headers.get('Origin'):
            return self.send(403, {'error': 'Use the workbench controller'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size > 4096:
                raise ValueError('Request too large')
            payload = json.loads(self.rfile.read(size))
            result = bridge.command(self.path.strip('/'), payload)
            self.send(200, result)
        except Exception as error:
            self.send(409, {'error': str(error)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--port', type=int, default=8092)
    args = parser.parse_args()
    rospy.init_node('lings_workbench_capture', anonymous=False, disable_signals=True)
    if not os.path.isdir(args.root):
        os.makedirs(args.root)
    # 通过共享目录组权限允许宿主机用户写入。
    os.setgid(os.stat(args.root).st_gid)
    os.umask(0o002)
    bridge = Bridge(args.root)
    print('capture bridge ready')
    Server(('127.0.0.1', args.port), Handler).serve_forever()
