import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from store import Store

class ScanStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name)
    def test_recording_not_deleted_and_restore_preserves_data(self):
        record=self.store.create('扫描甲')
        image=self.store.path(record['id'])/'existing.png';image.write_bytes(b'original')
        self.store.update(record['id'],status='recording')
        with self.assertRaises(ValueError):self.store.move(record['id'])
        self.store.update(record['id'],status='saved')
        self.store.move(record['id'])
        self.assertEqual(self.store.list(),[])
        self.store.move(record['id'],restore=True)
        self.assertEqual(image.read_bytes(),b'original')
    def test_path_escape_and_symlink_rejected(self):
        with self.assertRaises(ValueError):self.store.path('../etc')
        identifier='a'*32
        (Path(self.tmp.name)/identifier).symlink_to('/tmp',target_is_directory=True)
        with self.assertRaises(ValueError):self.store.path(identifier)

class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        os.environ['LINGS_SCAN_ROOT']=cls.tmp.name
        import server
        cls.server=server
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def test_version_conflict_and_websocket_shared_state(self):
        from fastapi.testclient import TestClient
        server=self.server
        with TestClient(server.app) as client:
            state=client.get('/api/state').json()
            with client.websocket_connect('/ws') as a,client.websocket_connect('/ws') as b:
                a.receive_json();b.receive_json()
                response=client.post('/api/command',json=dict(action='new',version=state['version'],name='同步测试'))
                self.assertEqual(response.status_code,200)
                identifier=response.json()['selected']
                stale=client.post('/api/command',json=dict(action='new',version=state['version']))
                self.assertEqual(stale.status_code,409)
                self.assertEqual(a.receive_json()['selected'],identifier)
                self.assertEqual(b.receive_json()['selected'],identifier)
            forbidden=client.post('/api/command',headers={'Origin':'https://example.com'},json=dict(action='new',version=999))
            self.assertEqual(forbidden.status_code,403)
    def test_saved_empty_scan_cannot_infer(self):
        from fastapi.testclient import TestClient
        server=self.server
        with TestClient(server.app) as client:
            record=server.store.create('空扫描');server.store.update(record['id'],status='saved')
            with patch.object(server,'bridge',return_value={'active':None}):
                res=client.post('/api/command',json=dict(action='infer',version=server.version,id=record['id']))
            self.assertEqual(res.status_code,409)
            self.assertIn('拍摄段',res.json()['detail'])

    def test_downloads_separate_modes_and_hide_incomplete_result(self):
        from fastapi.testclient import TestClient
        from store import write_json
        server=self.server
        record=server.store.create('模式下载')
        server.store.update(record['id'],status='saved',segments=[dict(path='segments/000',frames=2)])
        segment=server.store.path(record['id'])/'segments/000'
        for folder,content in [('result',b'original-mono'),('result_stereo',b'new-stereo')]:
            root=segment/folder;root.mkdir(parents=True)
            write_json(root/'result.json',dict(mode=folder))
            (root/'geometry.npz').write_bytes(content)
        url='/api/download/'+record['id']+'/0/geometry.npz'
        with TestClient(server.app) as client:
            self.assertEqual(client.get(url+'?mode=mono').content,b'original-mono')
            self.assertEqual(client.get(url+'?mode=stereo').content,b'new-stereo')
            self.assertEqual(client.get(url+'?mode=wrong').status_code,400)
            (segment/'result_stereo/result.json').unlink()
            self.assertEqual(client.get(url+'?mode=stereo').status_code,404)
            self.assertEqual(client.get(url+'?mode=mono').content,b'original-mono')

    def test_continuous_capture_request_has_no_60_or_segment_10_cap(self):
        from fastapi.testclient import TestClient
        server=self.server
        record=server.store.create('长录制')
        with TestClient(server.app) as client,patch.object(server,'bridge',return_value={'active':None}) as bridge:
            response=client.post('/api/command',json=dict(action='start',version=server.version,id=record['id']))
            self.assertEqual(response.status_code,200)
            bridge.assert_any_call('/start',{'id':record['id'],'fps':3},timeout=20)
        command=server.Command(action='segment',version=0,id=record['id'],segment=12)
        self.assertEqual(command.segment,12)

    def test_geometry_archive_download_streams_new_chunks(self):
        from fastapi.testclient import TestClient
        from stream_storage import ChunkWriter
        from store import write_json
        import numpy as np,io,zipfile
        server=self.server
        record=server.store.create('流式下载')
        server.store.update(record['id'],status='saved',segments=[dict(path='segments/000',frames=2)])
        result=server.store.path(record['id'])/'segments/000/result_stereo'
        writer=ChunkWriter(result/'geometry');writer.append(dict(depth=np.ones((2,3))));writer.close()
        write_json(result/'result.json',dict(format='chunked-v1'))
        with TestClient(server.app) as client:
            response=client.get('/api/download/'+record['id']+'/0/geometry.npz?mode=stereo')
            self.assertEqual(response.status_code,200)
            self.assertIn('geometry.zip',response.headers['content-disposition'])
            archive=zipfile.ZipFile(io.BytesIO(response.content))
            self.assertIn('index.json',archive.namelist())

if __name__=='__main__':unittest.main()
