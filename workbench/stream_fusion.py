"""固定米制尺度的增量双目融合。"""
import json
from collections import deque
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from stereo_fusion import (StereoMatcher, align_stereo, estimate_scale, scaled_extrinsics,
                           refine_trajectory, fuse_depths, PARAMETERS)
from stream_storage import safe_image


class StreamingFusion:
    def __init__(self, segment):
        self.segment=Path(segment)
        self.matcher=StereoMatcher(json.loads((self.segment/'calibration.json').read_text()))
        self.scale=None;self.scale_stats={};self.references=deque(maxlen=9)
        self.previous_neural=None;self.previous_pose=None
        self.orb=cv2.ORB_create(nfeatures=1800,fastThreshold=10,edgeThreshold=15)
        self.count=0;self.valid=0;self.pixels=0;self.pnp=0;self.fallbacks=0
        self.source_counts=np.zeros(4,dtype=np.int64)
        self.bad_scale_frames=0

    def stereo(self, row, neural):
        left,right=[np.asarray(Image.open(safe_image(self.segment,side,row[side])).convert('RGB')) for side in ('left','right')]
        depth,weight,disparity=self.matcher.compute(left,right)
        h,w=neural['depth'].shape[:2]
        depth,weight,k=align_stereo(depth,weight,(h,w),self.matcher.k)
        # 按相同像素映射缩放视差图，数值单位保留为原图像素。
        from stereo_fusion import preprocessing_geometry
        _,(nh,nw),top=preprocessing_geometry(left.shape[1],left.shape[0],(h,w),self.matcher.k)
        disparity=cv2.resize(disparity,(nw,nh),interpolation=cv2.INTER_NEAREST_EXACT)[top:top+h,:w]
        return dict(stereo_depth=depth,stereo_confidence=weight,disparity=disparity,intrinsic=k.astype(np.float32))

    def initialize_scale(self, pairs):
        self.scale,self.scale_stats=estimate_scale(
            np.stack([s['stereo_depth'] for _,s in pairs]),
            np.stack([n['depth'][...,0] for n,_ in pairs]),
            np.stack([s['stereo_confidence'] for _,s in pairs]),
            np.stack([n['depth_conf'] for n,_ in pairs]))

    def process(self, neural, stereo):
        if self.scale is None:raise ValueError('双目尺度尚未初始化')
        depth=stereo['stereo_depth'];weight=stereo['stereo_confidence'];k=stereo['intrinsic']
        mono=neural['depth'][...,0];mc=neural['depth_conf']
        scaled=scaled_extrinsics(neural['extrinsic'],self.scale)
        scaled4=np.eye(4);scaled4[:3]=scaled
        feature=self.orb.detectAndCompute(cv2.cvtColor(neural['rgb'],cv2.COLOR_RGB2GRAY),None)
        best=None
        if self.count:
            for gap in (1,3,8):
                ref=next((r for r in self.references if r['index']==self.count-gap),None)
                if ref is None:continue
                estimated,reports=refine_trajectory([None,None],np.stack([ref['depth'],depth]),
                    np.stack([ref['weight'],weight]),k,np.stack([ref['neural'][:3],scaled]),
                    features=[ref['feature'],feature])
                if reports[1]['method']=='stereo_pnp' and (best is None or reports[1]['inliers']>best[0]):
                    relative=np.eye(4);relative[:3]=estimated[1]
                    best=(reports[1]['inliers'],relative@ref['pose'],dict(reports[1],reference=ref['index']))
            if best:pose=best[1];report=best[2];self.pnp+=1
            else:
                pose=scaled4@np.linalg.inv(self.previous_neural)@self.previous_pose
                report=dict(method='scaled_lingbot_fallback',reference=self.count-1,inliers=0)
                self.fallbacks+=1
        else:pose=np.eye(4);report=dict(method='origin',inliers=0)
        # 固定初始全局尺度；持续偏差须报告，避免扭曲已有几何。
        support=(depth>0)&(weight>.15)&np.isfinite(mono)&(mono>0)
        ratio=float(np.median(np.log(depth[support]/mono[support]))) if support.sum()>=150 else None
        self.bad_scale_frames=self.bad_scale_frames+1 if ratio is not None and abs(ratio-np.log(self.scale))>.5 else 0
        if self.bad_scale_frames>=12:
            raise ValueError('连续12帧神经尺度明显偏离初始化，已停止融合；原图和已提交块保留，请缩短运动范围或检查场景')
        fused,confidence,source=fuse_depths(depth,weight,mono,mc,self.scale)
        self.references.append(dict(index=self.count,feature=feature,depth=depth,weight=weight,pose=pose.copy(),neural=scaled4.copy()))
        self.previous_neural=scaled4;self.previous_pose=pose
        self.valid+=int((depth>0).sum());self.pixels+=depth.size
        self.source_counts+=np.bincount(source.ravel(),minlength=4);self.count+=1
        frame=dict(depth=fused[...,None],depth_conf=confidence,extrinsic=pose[:3].astype(np.float32),
                   intrinsic=k,rgb=neural['rgb'],source=source,neural_depth=mono,
                   neural_depth_conf=mc,neural_extrinsic=neural['extrinsic'],neural_intrinsic=neural['intrinsic'],
                   scaled_neural_extrinsic=scaled.astype(np.float32),**{key:value for key,value in stereo.items() if key!='intrinsic'})
        return frame,dict(frame=self.count-1,scale=self.scale,frame_scale_ratio=None if ratio is None else float(np.exp(ratio)),
                          valid_stereo_fraction=float((depth>0).mean()),**report)

    def report(self):
        return dict(baseline_m=self.matcher.baseline,scale=self.scale,scale_policy='fixed_initial_support',
                    valid_stereo_fraction=self.valid/max(1,self.pixels),stereo_pnp_frames=self.pnp,
                    fallback_frames=self.fallbacks,trajectory_frames=self.count,
                    source_counts={str(i):int(v) for i,v in enumerate(self.source_counts)},
                    parameters=PARAMETERS,metric_scale_source='calibrated_stereo',metric_scale_verified=False,
                    **self.scale_stats)


def save_comparison(path, frame, scale):
    def color(depth):
        values=np.nan_to_num(depth,nan=0,posinf=4,neginf=0)
        colors=cv2.applyColorMap(255-np.clip(values/4*255,0,255).astype(np.uint8),cv2.COLORMAP_TURBO)[...,::-1]
        colors[depth<=0]=0
        return colors
    rgb=frame['rgb'];source=np.zeros_like(rgb)
    source[frame['source']>=2]=[35,190,125];source[frame['source']==1]=[235,150,45]
    panels=[rgb,color(frame['stereo_depth']),color(frame['neural_depth']*scale),source]
    canvas=Image.new('RGB',(rgb.shape[1]*4,rgb.shape[0]+30),'#182733');draw=ImageDraw.Draw(canvas)
    for i,(panel,label) in enumerate(zip(panels,['Rectified left','Stereo depth (0.15-4m)','Scaled LingBot depth','Green: stereo / orange: neural'])):
        canvas.paste(Image.fromarray(panel),(i*rgb.shape[1],30));draw.text((i*rgb.shape[1]+5,8),label,fill='white')
    canvas.save(path)
