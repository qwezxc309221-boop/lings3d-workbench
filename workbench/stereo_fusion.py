"""标定双目深度与 LingBot 几何融合。

输入为已校正的左右 PNG 及 CameraInfo 快照，物理量使用米。
"""
import json
from pathlib import Path
import cv2
import numpy as np
from PIL import Image

PARAMETERS = dict(min_depth_m=0.15, max_depth_m=4.0, disparities=64,
                  block_size=5, lr_tolerance_px=1.0, agreement_log_tolerance=0.25)


def calibration_geometry(calibration):
    left, right = calibration['left'], calibration['right']
    pl = np.asarray(left['P'], dtype=np.float64).reshape(3, 4)
    pr = np.asarray(right['P'], dtype=np.float64).reshape(3, 4)
    for p in (pl, pr):
        if not np.isfinite(p).all() or p[0, 0] <= 0 or p[1, 1] <= 0:
            raise ValueError('相机投影标定无效')
    if (left['width'], left['height']) != (right['width'], right['height']):
        raise ValueError('左右标定尺寸不同')
    # 仅支持水平校正双目。
    if not np.allclose(pl[:2, :2], pr[:2, :2], atol=1e-3) or abs(pl[1, 2]-pr[1, 2]) > 0.1:
        raise ValueError('当前仅支持相同焦距、水平极线的校正双目')
    if abs(pl[1, 3]) > 1e-5 or abs(pr[1, 3]) > 1e-5:
        raise ValueError('当前不支持竖直基线')
    baseline = pl[0, 3]/pl[0, 0] - pr[0, 3]/pr[0, 0]
    if not 0.001 < baseline < 2:
        raise ValueError('双目基线方向或单位异常，请检查P矩阵与左右顺序')
    return pl[:, :3].copy(), float(baseline), float(pl[0, 2]-pr[0, 2])


def rectification_mask(info):
    """根据标定计算有效区域，保存图像不再重复校正。"""
    w, h = int(info['width']), int(info['height'])
    k = np.asarray(info['K']).reshape(3, 3)
    r = np.asarray(info['R']).reshape(3, 3)
    p = np.asarray(info['P']).reshape(3, 4)[:, :3]
    if info['distortion_model'] not in ('plumb_bob', 'rational_polynomial'):
        raise ValueError('不支持的畸变模型')
    mx, my = cv2.initUndistortRectifyMap(k, np.asarray(info['D']), r, p, (w, h), cv2.CV_32FC1)
    return (mx >= 2) & (my >= 2) & (mx < w-3) & (my < h-3)


def disparity_to_depth(disparity, focal, baseline, principal_offset=0., min_depth=0.15, max_depth=4.):
    corrected = np.asarray(disparity, dtype=np.float32)-principal_offset
    depth = np.zeros_like(corrected)
    positive = np.isfinite(corrected) & (corrected > 0)
    depth[positive] = focal*baseline/corrected[positive]
    valid = positive & (depth >= min_depth) & (depth <= max_depth)
    depth[~valid] = 0
    return depth, valid


class StereoMatcher:
    def __init__(self, calibration):
        self.calibration = calibration
        self.k, self.baseline, self.offset = calibration_geometry(calibration)
        self.left_mask = rectification_mask(calibration['left'])
        self.right_mask = rectification_mask(calibration['right'])
        self.nd = PARAMETERS['disparities']
        params = dict(numDisparities=self.nd, blockSize=5, P1=8*25, P2=32*25,
                      disp12MaxDiff=1, preFilterCap=31, uniquenessRatio=12,
                      speckleWindowSize=60, speckleRange=2, mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
        self.left_matcher = cv2.StereoSGBM_create(minDisparity=0, **params)
        self.right_matcher = cv2.StereoSGBM_create(minDisparity=1-self.nd, **params)

    def compute(self, left, right):
        if left.shape != right.shape or left.shape[:2] != self.left_mask.shape:
            raise ValueError('左右图像大小与标定不一致')
        a = cv2.cvtColor(left, cv2.COLOR_RGB2GRAY)
        b = cv2.cvtColor(right, cv2.COLOR_RGB2GRAY)
        disparity = self.left_matcher.compute(a, b).astype(np.float32)/16.
        reverse = self.right_matcher.compute(b, a).astype(np.float32)/16.
        yy, xx = np.indices(a.shape)
        xr = np.rint(xx-disparity).astype(np.int32)
        inside = (xr >= 0) & (xr < a.shape[1])
        sample = np.clip(xr, 0, a.shape[1]-1)
        reverse_at = reverse[yy, sample]
        error = np.abs(disparity+reverse_at)
        depth, valid = disparity_to_depth(disparity, self.k[0,0], self.baseline, self.offset)
        mean = cv2.boxFilter(a.astype(np.float32), -1, (5,5))
        variance = cv2.boxFilter(a.astype(np.float32)**2, -1, (5,5))-mean**2
        valid &= (inside & self.left_mask & self.right_mask[yy, sample] &
                  (reverse_at > -self.nd) & (reverse_at < 0) &
                  (error <= PARAMETERS['lr_tolerance_px']) & (variance > 12))
        depth[~valid] = 0
        # 按视差构造深度权重；权重不代表准确率。
        confidence = (np.exp(-error)*np.minimum(np.maximum(disparity-self.offset, 0)/12., 1.)).astype(np.float32)
        confidence[~valid] = 0
        return depth, confidence, np.where(valid, disparity, 0).astype(np.float32)


def preprocessing_geometry(width, height, output_hw, k):
    """与 load_fn 保持相同裁剪及像素中心映射。"""
    oh, ow = output_hw
    new_h = round(height*(ow/width)/14)*14
    crop_y = (new_h-ow)//2 if new_h > ow else 0
    if oh != min(new_h, ow):
        raise ValueError('神经结果尺寸与当前预处理不一致')
    sx, sy = ow/width, new_h/height
    transform = np.array([[sx,0,(sx-1)/2], [0,sy,(sy-1)/2-crop_y], [0,0,1.]])
    return transform @ k, (new_h, ow), crop_y


def align_stereo(depth, confidence, output_hw, k):
    aligned_k, (nh,nw), top = preprocessing_geometry(depth.shape[1], depth.shape[0], output_hw, k)
    h, w = output_hw
    # 最近邻采样保留无效区和前后景深度边界。
    d = cv2.resize(depth, (nw,nh), interpolation=cv2.INTER_NEAREST_EXACT)[top:top+h, :w]
    c = cv2.resize(confidence, (nw,nh), interpolation=cv2.INTER_NEAREST_EXACT)[top:top+h, :w]
    return d, c, aligned_k


def estimate_scale(stereo_depth, mono_depth, stereo_conf, mono_conf):
    ratios, counts = [], []
    for stereo, mono, sc, mc in zip(stereo_depth, mono_depth, stereo_conf, mono_conf):
        valid = (stereo > 0) & (mono > 0) & np.isfinite(mono) & np.isfinite(mc) & (sc > .15)
        finite = mc[np.isfinite(mc)]
        if finite.size:
            valid &= mc >= np.percentile(finite, 50)
        count = int(valid.sum())
        counts.append(count)
        if count >= 150:
            ratios.append(float(np.median(np.log(stereo[valid]/mono[valid]))))
    if len(ratios) < min(3, len(stereo_depth)) or sum(counts) < 800:
        raise ValueError('可靠双目/神经深度对应不足，拒绝伪装为已完成融合；请拍摄有纹理且重叠充分的近景')
    median = float(np.median(ratios))
    mad = float(np.median(np.abs(np.asarray(ratios)-median)))
    if mad > .30:
        raise ValueError('各帧尺度差异过大，无法用同一尺度融合此段；请缩短片段并检查标定/运动')
    return float(np.exp(median)), dict(scale_correspondences=sum(counts), scale_frames=len(ratios),
                                      scale_log_mad=round(mad,5), frame_scale_ratios=[round(float(np.exp(x)),5) for x in ratios])


def scaled_extrinsics(extrinsics, scale):
    result = np.asarray(extrinsics, dtype=np.float64).copy()
    # W2C：x_c=R*x_w+t，尺度变化时平移 t 同步缩放。
    result[...,3] *= scale
    return result


def backproject(depth, k):
    y,x = np.indices(depth.shape)
    ray = np.stack([x,y,np.ones_like(x)],axis=-1) @ np.linalg.inv(k).T
    return (ray*depth[...,None]).astype(np.float32)


def refine_trajectory(rgb, depth, confidence, k, fallback, features=None):
    """以首帧为原点，用双向匹配和 RGB-D PnP 校正轨迹。

    失败帧回退到已缩放的神经预测，不依赖设备 frame_id 或 TF。
    """
    count = len(rgb)
    transforms = np.repeat(np.eye(4)[None], count, axis=0)
    fallback4 = np.repeat(np.eye(4)[None], count, axis=0)
    fallback4[:,:3] = fallback
    if features is None:
        orb = cv2.ORB_create(nfeatures=1800, fastThreshold=10, edgeThreshold=15)
        features = [orb.detectAndCompute(cv2.cvtColor(image,cv2.COLOR_RGB2GRAY),None) for image in rgb]
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    reports = [dict(method='origin',inliers=0)]
    for i in range(1,count):
        # 使用多个历史参考，降低单帧弱匹配的影响。
        best = None
        for ref in sorted(set([i-1,max(0,i-3),max(0,i-8)]),reverse=True):
            kp0,des0=features[ref]; kp1,des1=features[i]
            if des0 is None or des1 is None or len(des0)<10 or len(des1)<10: continue
            matches=matcher.knnMatch(des0,des1,k=2)
            back=matcher.match(des1,des0)
            reverse={m.queryIdx:m.trainIdx for m in back}
            good=[pair[0] for pair in matches if len(pair)==2 and pair[0].distance<.72*pair[1].distance
                  and reverse.get(pair[0].trainIdx)==pair[0].queryIdx]
            obj=[];img=[]
            for m in good:
                x,y=kp0[m.queryIdx].pt; ix,iy=int(round(x)),int(round(y))
                if not (0<=iy<depth.shape[1] and 0<=ix<depth.shape[2]): continue
                z=depth[ref,iy,ix]
                if z<=0 or confidence[ref,iy,ix]<.15: continue
                obj.append(np.linalg.solve(k,np.array([x,y,1.]))*z)
                img.append(kp1[m.trainIdx].pt)
            if len(obj)<16: continue
            obj=np.asarray(obj,dtype=np.float32); img=np.asarray(img,dtype=np.float32)
            ok,rotation,translation,inliers=cv2.solvePnPRansac(obj,img,k,None,iterationsCount=150,
                reprojectionError=2.,confidence=.999,flags=cv2.SOLVEPNP_EPNP)
            if not ok or inliers is None or len(inliers)<12 or len(inliers)/len(obj)<.35: continue
            indices=inliers[:,0]
            ok,rotation,translation=cv2.solvePnP(obj[indices],img[indices],k,None,rotation,translation,True,
                                               flags=cv2.SOLVEPNP_ITERATIVE)
            r=cv2.Rodrigues(rotation)[0]; t=translation[:,0]
            projected=cv2.projectPoints(obj[indices],rotation,translation,k,None)[0][:,0]
            error=float(np.median(np.linalg.norm(projected-img[indices],axis=1)))
            span=np.ptp(img[indices],axis=0)
            if (not ok or error>1.5 or np.linalg.norm(t)>1.0 or
                np.trace(r)<1.0 or np.any(span<30) or not np.isfinite(t).all()): continue
            # 用当前帧有效双目深度检查运动一致性。
            pixel=np.rint(img[indices]).astype(int)
            inside=(pixel[:,0]>=0)&(pixel[:,0]<depth.shape[2])&(pixel[:,1]>=0)&(pixel[:,1]<depth.shape[1])
            pixel=pixel[inside]; xyz=(obj[indices][inside]@r.T+t)
            current=depth[i,pixel[:,1],pixel[:,0]]
            overlap=(current>0)&(xyz[:,2]>0)
            if overlap.sum()>=10 and np.median(np.abs(np.log(xyz[overlap,2]/current[overlap])))>.20: continue
            report=dict(method='stereo_pnp',reference=ref,inliers=len(indices),matches=len(obj),median_reprojection_px=round(error,3))
            if best is None or len(indices)>best[0]:
                relative=np.eye(4);relative[:3,:3]=r;relative[:3,3]=t
                best=(len(indices),relative@transforms[ref],report)
        if best:
            transforms[i]=best[1];reports.append(best[2])
        else:
            relative=fallback4[i]@np.linalg.inv(fallback4[i-1])
            transforms[i]=relative@transforms[i-1]
            reports.append(dict(method='scaled_lingbot_fallback',reference=i-1,inliers=0))
    return transforms[:,:3].astype(np.float32), reports


def fuse_depths(stereo, stereo_conf, mono, mono_conf, scale):
    neural=mono*scale
    valid=stereo>0
    neural_valid=(neural>0)&np.isfinite(neural)
    agreement=valid&neural_valid&(np.abs(np.log(np.maximum(neural,1e-8)/np.maximum(stereo,1e-8)))<.25)
    fused=np.where(neural_valid,neural,0).astype(np.float32)
    source=np.where(neural_valid,1,0).astype(np.uint8)  # 1=unmeasured neural fill; 2=stereo; 3=agreement blend.
    fused[valid]=stereo[valid];source[valid]=2
    # 一致区域按逆深度融合，双目权重不低于 80%。
    weight=.8+.15*stereo_conf[agreement]
    fused[agreement]=1/(weight/stereo[agreement]+(1-weight)/neural[agreement])
    source[agreement]=3
    confidence=np.where(source==1,.08,stereo_conf).astype(np.float32)
    confidence[source==0]=0
    return fused,confidence,source


def run_fusion(segment, arrays, rgb, progress=None):
    segment=Path(segment)
    calibration=json.loads((segment/'calibration.json').read_text())
    rows=[json.loads(line) for line in (segment/'frames.jsonl').read_text().splitlines() if line]
    n,h,w=arrays['depth'].shape[:3]
    if len(rows)!=n: raise ValueError('预测帧数与双目清单不同')
    matcher=StereoMatcher(calibration)
    stereo=[];weights=[];disparities=[];fractions=[]
    for i,row in enumerate(rows):
        if row['left_stamp_ns']!=row['right_stamp_ns']: raise ValueError('存在不同步的左右图，拒绝融合')
        paths=[]
        for side in ('left','right'):
            name=row[side]
            if Path(name).name!=name or not name.endswith('.png'):raise ValueError('双目清单路径无效')
            paths.append(segment/side/name)
        a,b=(np.asarray(Image.open(path).convert('RGB')) for path in paths)
        d,c,disparity=matcher.compute(a,b)
        fractions.append(float((d>0).mean()))
        d,c,k=align_stereo(d,c,(h,w),matcher.k)
        stereo.append(d);weights.append(c)
        # 视差图采样到模型尺寸，数值仍以原图像素为单位。
        _,(nh,nw),top=preprocessing_geometry(a.shape[1],a.shape[0],(h,w),matcher.k)
        disparities.append(cv2.resize(disparity,(nw,nh),interpolation=cv2.INTER_NEAREST_EXACT)[top:top+h,:w])
        if progress: progress('stereo',i+1,n)
    stereo=np.stack(stereo);weights=np.stack(weights)
    mono=arrays['depth'][...,0];mc=arrays['depth_conf']
    scale,scale_stats=estimate_scale(stereo,mono,weights,mc)
    fallback=scaled_extrinsics(arrays['extrinsic'],scale)
    if progress:progress('fusion',0,n)
    extrinsic,trajectory=refine_trajectory(rgb,stereo,weights,k,fallback)
    fused,confidence,source=fuse_depths(stereo,weights,mono,mc,scale)
    paired=(stereo>0)&(mono>0)&np.isfinite(mono)
    residual=np.abs(np.log((mono[paired]*scale)/stereo[paired]))
    stats=dict(mode='stereo_lingbot_fusion',baseline_m=round(matcher.baseline,8),scale=scale,
               valid_stereo_fraction=round(float((stereo>0).mean()),4),
               median_relative_depth_disagreement=round(float(np.median(np.abs(mono[paired]*scale-stereo[paired])/stereo[paired])),4),
               agreement_fraction=round(float((residual<.25).mean()),4),
               frame_valid_stereo_fraction=[round(x,4) for x in fractions],
               source_counts={str(i):int((source==i).sum()) for i in range(4)},
               trajectory=trajectory,stereo_pnp_frames=sum(x['method']=='stereo_pnp' for x in trajectory),
               parameters=PARAMETERS,metric_scale_verified=False,metric_scale_source='calibrated_stereo',
               **scale_stats)
    numeric=dict(depth=fused[...,None],depth_conf=confidence,extrinsic=extrinsic,
                 intrinsic=np.repeat(k[None],n,axis=0).astype(np.float32),
                 neural_depth=mono.astype(np.float32),neural_depth_conf=mc.astype(np.float32),
                 neural_extrinsic=arrays['extrinsic'].astype(np.float32),
                 neural_intrinsic=arrays['intrinsic'].astype(np.float32),
                 stereo_depth=stereo,stereo_confidence=weights,disparity=np.stack(disparities),
                 source=source,metric_scale=np.array(scale),
                 scaled_neural_extrinsic=fallback.astype(np.float32))
    return numeric,stats
