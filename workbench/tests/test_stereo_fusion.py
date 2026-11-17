import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from stereo_fusion import (disparity_to_depth,preprocessing_geometry,estimate_scale,
                           scaled_extrinsics,backproject,fuse_depths,StereoMatcher)
from results import result_path

class StereoGeometryTests(unittest.TestCase):
    def test_depth_principal_offset_invalid_and_range(self):
        d=np.array([[-1.,0.,1.,6.,11.]],dtype=np.float32)
        depth,valid=disparity_to_depth(d,100.,.1,1.,max_depth=4.)
        np.testing.assert_allclose(depth,[[0,0,0,2,1]])
        self.assertEqual(valid.tolist(),[[False,False,False,True,True]])
    def test_crop_geometry_and_backprojection_known_plane(self):
        k=np.array([[200.,0,100],[0,200,150],[0,0,1.]])
        actual,shape,top=preprocessing_geometry(200,400,(350,350),k)
        self.assertEqual(shape,(700,350));self.assertEqual(top,175)
        self.assertAlmostEqual(actual[0,2],175.375)
        self.assertAlmostEqual(actual[1,2],87.875)
        points=backproject(np.ones((2,2),dtype=np.float32)*2,np.eye(3))
        np.testing.assert_allclose(points[1,1],[2,2,2])
    def test_scale_rejects_missing_support_and_resists_outliers(self):
        mono=np.ones((3,40,40),dtype=np.float32)
        stereo=mono*2;stereo[:,:2]=20
        scale,_=estimate_scale(stereo,mono,np.ones_like(mono),np.ones_like(mono))
        self.assertAlmostEqual(scale,2.)
        with self.assertRaises(ValueError):estimate_scale(stereo*0,mono,mono,mono)
    def test_pose_translation_scales_with_depth(self):
        e=np.array([[[1,0,0,-1],[0,1,0,0],[0,0,1,0]]],dtype=float)
        scaled=scaled_extrinsics(e,2)
        np.testing.assert_allclose(scaled[0,:,3],[-2,0,0])
        np.testing.assert_allclose(scaled[0,:,:3],np.eye(3))
        # 相机中心 x=2、相机坐标点 x=2，对应世界坐标 x=4。
        self.assertEqual((np.array([2,0,2])-scaled[0,:,3])[0],4)
    def test_fusion_disagreement_uses_stereo_and_labels_neural_fill(self):
        stereo=np.array([[[2.,2.,0.]]],dtype=np.float32)
        mono=np.array([[[2.,8.,3.]]],dtype=np.float32)
        fused,confidence,source=fuse_depths(stereo,np.ones_like(stereo),mono,mono,1.)
        np.testing.assert_allclose(fused,[[[2,2,3]]])
        self.assertEqual(source.tolist(),[[[3,2,1]]])
        self.assertLess(confidence[0,0,2],confidence[0,0,1])
    def test_synthetic_rectified_pair_disparity(self):
        w,h=320,160;k=[160,0,160,0,160,80,0,0,1]
        def camera(tx):return dict(width=w,height=h,distortion_model='plumb_bob',K=k,D=[0]*5,R=np.eye(3).reshape(-1).tolist(),P=[160,0,160,tx,0,160,80,0,0,0,1,0])
        matcher=StereoMatcher(dict(left=camera(0),right=camera(-16)))
        rng=np.random.default_rng(7)
        left=rng.integers(15,240,(h,w,3),dtype=np.uint8)
        right=np.zeros_like(left);right[:,:-8]=left[:,8:]
        depth,confidence,d=matcher.compute(left,right)
        valid=depth[:,80:230]>0
        self.assertGreater(valid.mean(),.7)
        self.assertAlmostEqual(float(np.median(d[:,80:230][valid])),8.,delta=.15)
        self.assertAlmostEqual(float(np.median(depth[:,80:230][valid])),2.,delta=.05)
    def test_modes_do_not_overwrite_existing_monocular_result(self):
        segment={'path':'segments/000'}
        self.assertEqual(result_path('/scan',segment,'mono'),Path('/scan/segments/000/result'))
        self.assertEqual(result_path('/scan',segment,'stereo'),Path('/scan/segments/000/result_stereo'))
        with self.assertRaises(ValueError):result_path('/scan',segment,'../x')

class StereoPoseTests(unittest.TestCase):
    def test_textureless_sequence_reports_fallback_and_preserves_metric_motion(self):
        from stereo_fusion import refine_trajectory
        rgb=np.zeros((3,64,80,3),dtype=np.uint8)
        depth=np.ones((3,64,80),dtype=np.float32)
        k=np.array([[80.,0,40],[0,80,32],[0,0,1]])
        poses=np.repeat(np.eye(4)[None,:3],3,axis=0)
        poses[:,0,3]=[0,-.1,-.2]
        result,reports=refine_trajectory(rgb,depth,depth,k,poses)
        np.testing.assert_allclose(result,poses,atol=1e-6)
        self.assertEqual([x['method'] for x in reports[1:]],['scaled_lingbot_fallback']*2)
    def test_negative_baseline_is_rejected(self):
        from stereo_fusion import calibration_geometry
        p=[100,0,40,0,0,100,30,0,0,0,1,0]
        left=dict(width=80,height=60,P=p)
        right=dict(width=80,height=60,P=[100,0,40,10,0,100,30,0,0,0,1,0])
        with self.assertRaises(ValueError):calibration_geometry(dict(left=left,right=right))

if __name__=='__main__':unittest.main()
