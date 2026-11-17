"""单目与双目结果分别存储和导出。"""
from pathlib import Path

def result_path(scan_path, segment, mode='stereo'):
    if mode not in ('mono','stereo'):
        raise ValueError('重建方式无效')
    return Path(scan_path)/segment['path']/('result_stereo' if mode=='stereo' else 'result')

def result_status(segment,mode):
    if mode=='mono':
        return segment.get('results',{}).get('mono',segment.get('result_status','idle'))
    return segment.get('results',{}).get('stereo','idle')
