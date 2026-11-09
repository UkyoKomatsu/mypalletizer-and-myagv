"""Integrity tests run without a camera, SDK, or a visible GUI."""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import queue
import tempfile
import time
import unittest

import numpy as np
from PIL import Image

from analyze import analyze
from app import CameraWorker
from core import CaptureWriter, Snapshot, region_metrics, select_profile


def sample(all_invalid=False):
    depth=np.array([[200,201,202],[203,0,205],[206,207,208]],dtype=np.uint16)
    if all_invalid:depth[:]=0
    y,x=np.indices(depth.shape)
    z=depth.astype(np.float32)*0.001
    xyz=np.stack(((x-1)*z/100,(y-1)*z/100,z),axis=-1).astype(np.float32)
    xyz[depth==0]=np.nan
    ir=np.full(depth.shape,120,dtype=np.uint8)
    return Snapshot(depth,ir,ir.copy(),np.zeros((3,3,3),dtype=np.uint8),xyz,
                    dict(source='synthetic_test',depth_scale_m=0.001,
                         effective_settings={'disparity_shift':0},
                         frames={'depth':dict(number=1,timestamp_ms=100.)}))


class CaptureTests(unittest.TestCase):
    def test_raw_zero_mask_and_ply_roundtrip(self):
        frame=sample()
        with tempfile.TemporaryDirectory() as directory:
            writer=CaptureWriter(directory,'test','',1)
            writer.add(frame);folder=writer.finish();saved=folder/'frame_0000'
            raw=np.fromfile(saved/'depth_z16.raw',dtype='<u2').reshape(3,3)
            np.testing.assert_array_equal(raw,frame.depth)
            np.testing.assert_array_equal(np.asarray(Image.open(saved/'depth_z16.png')),frame.depth)
            with np.load(saved/'arrays.npz') as data:
                np.testing.assert_array_equal(data['valid_mask'],raw>0)
                self.assertTrue(np.isnan(data['xyz_m'][1,1]).all())
            ply=(saved/'points.ply').read_bytes()
            header,body=ply.split(b'end_header\n',1)
            self.assertIn(b'element vertex 8',header)
            points=np.frombuffer(body,dtype='<f4').reshape(-1,3)
            np.testing.assert_allclose(points,frame.xyz[raw>0])
            for line in (folder/'checksums.sha256').read_text().splitlines():
                digest,name=line.split('  ',1)
                self.assertEqual(digest,hashlib.sha256((folder/name).read_bytes()).hexdigest())
            self.assertFalse(writer.partial.exists())

    def test_temporal_statistics_do_not_turn_missing_into_zero_distance(self):
        one=sample();two=sample()
        two.depth[0,0]=202;two.xyz[0,0]=np.array([-0.00202,-0.00202,0.202])
        two.depth[0,1]=0;two.xyz[0,1]=np.nan
        with tempfile.TemporaryDirectory() as directory:
            writer=CaptureWriter(directory,'time','',2)
            writer.add(one);writer.add(two);folder=writer.finish()
            with np.load(folder/'temporal_statistics.npz') as data:
                self.assertAlmostEqual(data['mean_depth_m'][0,0],0.201)
                self.assertAlmostEqual(data['std_depth_m'][0,0],np.sqrt(2)*0.001)
                self.assertEqual(data['valid_samples'][0,1],1)
                self.assertTrue(np.isnan(data['std_depth_m'][0,1]))
                self.assertTrue(np.isnan(data['mean_depth_m'][1,1]))
                self.assertEqual(data['invalid_frequency'][1,1],1)
                self.assertEqual(data['invalid_frequency'][0,1],0.5)
            path,count=analyze(folder,Path(directory)/'report')
            with path.open(encoding='utf-8-sig') as stream:rows=list(csv.DictReader(stream))
            self.assertEqual(count,2);self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['source'],'synthetic_test')

    def test_interruption_never_commits_incomplete_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            writer=CaptureWriter(directory,'failure','',2)
            writer.add(sample())
            with self.assertRaises(ValueError):writer.finish()
            writer.fail('test interrupted')
            self.assertFalse(writer.final.exists())
            self.assertTrue((writer.partial/'INCOMPLETE.json').exists())

    def test_settings_change_and_wrong_unit_scale_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            writer=CaptureWriter(directory,'settings','',2)
            writer.add(sample());other=sample()
            other.metadata['effective_settings']['disparity_shift']=20
            with self.assertRaises(ValueError):writer.add(other)
            other=sample();other.metadata['depth_scale_m']=0.01
            with self.assertRaises(ValueError):other.validate()

    def test_empty_valid_region_is_unknown_not_zero_metres(self):
        empty=sample(all_invalid=True)
        result=region_metrics(empty.depth,empty.ir_left,0.001)
        self.assertIsNone(result['depth_median_m'])
        self.assertEqual(result['invalid_fraction'],1.)
        result=region_metrics(sample().depth,sample().ir_left,0.001)
        self.assertEqual(result['isolated_single_zero_pixels'],1)

    def test_profile_requires_matching_device_and_nonambiguous_distance(self):
        p=dict(signature='cameraA:640x480@30',min_cm=18,max_cm=22,disparity_shift=12)
        self.assertEqual(select_profile([p],20,p['signature'])['disparity_shift'],12)
        with self.assertRaises(ValueError):select_profile([p],25,p['signature'])
        with self.assertRaises(ValueError):select_profile([p],20,'other_camera')
        with self.assertRaises(ValueError):select_profile([p,copy.deepcopy(p)],20,p['signature'])

    def test_preview_never_saves_until_explicit_capture(self):
        args=argparse.Namespace(demo=True)
        worker=CameraWorker(args);worker.start()
        settings=dict(emitter=True,laser_power=60,auto_exposure=True,exposure_us=5000,gain=16,disparity_shift=0)
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'captures'
            try:
                worker.commands.put(('connect',settings))
                packet=worker.previews.get(timeout=10)
                self.assertFalse(target.exists())
                worker.commands.put(('capture',dict(packet=packet,frames=1,root=target,
                    label='SYNTHETIC_TEST',note='',rois={},context={})))
                deadline=time.monotonic()+20
                saved=None
                while time.monotonic()<deadline:
                    kind,value=worker.events.get(timeout=10)
                    if kind=='error':self.fail(value)
                    if kind=='saved':saved=Path(value);break
                self.assertIsNotNone(saved)
                self.assertEqual(len(list(target.glob('*/capture.json'))),1)
                meta=json.loads((saved/'frame_0000'/'metadata.json').read_text())
                self.assertEqual(meta['frames']['depth']['number'],packet.metadata['frames']['depth']['number'])
                self.assertEqual(meta['source'],'synthetic_demo')
                self.assertEqual(meta['host_filters'],[])
            finally:
                worker.stop_event.set();worker.join(timeout=10)
                self.assertFalse(worker.is_alive())


if __name__=='__main__':unittest.main()
