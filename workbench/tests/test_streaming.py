import io
import json
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from stream_storage import ChunkWriter,ChunkReader,VoxelMap,geometry_zip,iter_frames,frame_cloud

class StreamingStorageTests(unittest.TestCase):
    def test_geometry_chunks_and_zip_stream_preserve_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            writer=ChunkWriter(directory)
            for i in range(35):writer.append(dict(depth=np.full((2,3),i,dtype=np.float32)))
            writer.close();self.assertEqual([c['count'] for c in writer.chunks],[16,16,3])
            reader=ChunkReader(directory)
            for i in [0,16,34,15]:self.assertEqual(float(reader.frame(i)['depth'][0,0]),i)
            archive=zipfile.ZipFile(io.BytesIO(b''.join(geometry_zip(directory))))
            self.assertEqual(len(archive.namelist()),4)
            self.assertIsNone(archive.testzip())
    def test_voxels_merge_measured_separately_and_bound_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            voxel=VoxelMap(Path(directory)/'map.sqlite')
            for i in range(100):
                cloud=dict(points=np.array([[1,0,2],[1,0,2],[i*.1,0,2]],dtype=np.float32),
                    colors=np.full((3,3),100,dtype=np.uint8),confidence=np.ones(3),
                    source=np.array([1,2,2]),frames=np.full(3,i))
                voxel.add(cloud)
            preview=voxel.preview(limit=20)
            self.assertLessEqual(len(preview['points']),20)
            self.assertEqual(set(preview['source']),{1,2})
            self.assertTrue(np.isfinite(preview['points']).all());voxel.close()
    def test_chunked_image_manifest_rejects_path_escape_and_unsynced(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            row=dict(index=0,left='000001/000300.png',right='000001/000300.png',left_stamp_ns=1,right_stamp_ns=1)
            (root/'frames.jsonl').write_text(json.dumps(row)+'\n')
            self.assertEqual(len(list(iter_frames(root))),1)
            row['left']='../secret.png';(root/'frames.jsonl').write_text(json.dumps(row)+'\n')
            with self.assertRaises(ValueError):list(iter_frames(root))
            row['left']=row['right'];row['right_stamp_ns']=2
            (root/'frames.jsonl').write_text(json.dumps(row)+'\n')
            with self.assertRaises(ValueError):list(iter_frames(root))
    def test_cache_limits_preserve_origin_and_temporal_positions_extend(self):
        import torch
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'third_party/lingbot-map'))
        from stream_runtime import cap_special_cache,configure_long_session
        from lingbot_map.layers.rope import WanRotaryPosEmbed
        class Model(torch.nn.Module):
            def __init__(self):
                super().__init__();self.aggregator=torch.nn.Module();self.camera_head=torch.nn.Module()
                self.aggregator.kv_cache={'k_0_special':torch.ones(1,1,100,6,2)}
                self.camera_head.kv_cache=[{'k_0':torch.arange(100.).reshape(1,1,100,1,1)}]
                self.aggregator.rope3d=WanRotaryPosEmbed(64,(1024,1,1))
        model=Model();cap_special_cache(model,64)
        self.assertEqual(model.aggregator.kv_cache['k_0_special'].shape[2],64)
        self.assertEqual(model.camera_head.kv_cache[0]['k_0'].shape[2],66)
        self.assertEqual(model.camera_head.kv_cache[0]['k_0'][0,0,0,0,0],0)
        configure_long_session(model,1800)
        value=model.aggregator.rope3d(1,1,1,0,torch.device('cpu'),f_start=1799,f_end=1800)
        self.assertTrue(torch.isfinite(value).all())

class ContinuousCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 仅替换 ROS 传输接口，保留实际采集文件读写。
        stubs={name:types.ModuleType(name) for name in ['rospy','message_filters','sensor_msgs','sensor_msgs.msg','cv_bridge']}
        stubs['sensor_msgs.msg'].Image=object;stubs['sensor_msgs.msg'].CameraInfo=object
        stubs['cv_bridge'].CvBridge=object
        with patch.dict(sys.modules,stubs):
            import ros_bridge
            cls.module=ros_bridge
    def test_620_pairs_do_not_pause_and_files_cross_300_boundary(self):
        import threading,queue
        from collections import deque
        bridge=self.module.Bridge.__new__(self.module.Bridge)
        with tempfile.TemporaryDirectory() as directory:
            bridge.root=directory;bridge.lock=threading.RLock();bridge.active=dict(id='a'*32,status='recording',segments=[dict(id=0,path='segments/000',frames=0)],frames=0)
            bridge.chunk_frames=300;bridge.accepting=True;bridge.last_persist=0;bridge.error=''
            bridge.rectified=lambda message,side:np.zeros((8,10,3),dtype=np.uint8)
            bridge.info={side:dict(K=[1],P=[1],width=10,height=8) for side in ['left','right']}
            folder=Path(directory)/('a'*32)/'segments/000';folder.mkdir(parents=True)
            class Stamp:
                def to_nsec(self):return 7
            message=types.SimpleNamespace(width=10,height=8,header=types.SimpleNamespace(stamp=Stamp(),frame_id='camera'))
            for i in range(620):bridge.write_pair('a'*32,0,message,message)
            self.assertEqual(bridge.active['status'],'recording')
            self.assertEqual(bridge.active['frames'],620)
            self.assertEqual(bridge.active['segments'][0]['disk_chunks'],3)
            self.assertTrue((folder/'left/000001/000300.png').is_file())
            self.assertTrue((folder/'right/000002/000619.png').is_file())
            self.assertEqual(len(list(iter_frames(folder))),620)
            bridge.persist()
            # 恢复时从提交日志计算帧数。
            bridge.active['segments'][0]['frames']=600;bridge.active['frames']=600;bridge.persist()
            bridge.recover()
            record=json.loads((Path(directory)/('a'*32)/'scan.json').read_text())
            self.assertEqual(record['frames'],620)
            self.assertEqual(record['status'],'interrupted')

    def test_queue_overload_pauses_without_unbounded_buffer(self):
        import threading,queue,time
        from collections import deque
        bridge=self.module.Bridge.__new__(self.module.Bridge)
        bridge.lock=threading.RLock();bridge.active=dict(id='a'*32,status='recording',segments=[dict(id=0)],frames=0)
        bridge.accepting=True;bridge.fps=3;bridge.last_save=0;bridge.last_pair=0
        bridge.counts={'pairs':0};bridge.times={'pairs':deque(maxlen=90)}
        bridge.write_queue=self.module.queue.Queue(maxsize=1);bridge.write_queue.put('pending')
        bridge.write_dropped=0;bridge.persist=lambda:None
        bridge.pair(None,None)
        self.assertEqual(bridge.active['notice'],'writer_overload')
        self.assertFalse(bridge.accepting)
        self.assertEqual(bridge.write_dropped,1)
        bridge.write_queue.get();bridge.write_queue.task_done()

    def test_capture_recovery_truncates_incomplete_manifest_tail(self):
        from store import recover_capture
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);segment=root/'segments/000'
            for side in ['left','right']:
                (segment/side).mkdir(parents=True)
                (segment/side/'0.png').write_bytes(b'pair')
            row=dict(index=0,left='0.png',right='0.png')
            manifest=segment/'frames.jsonl';manifest.write_text(json.dumps(row)+'\n'+ '{"index":1')
            record=dict(status='recording',frames=0,segments=[dict(path='segments/000',frames=0)])
            recover_capture(root,record)
            self.assertEqual(record['frames'],1)
            self.assertEqual(len(manifest.read_text().splitlines()),1)

if __name__=='__main__':unittest.main()
