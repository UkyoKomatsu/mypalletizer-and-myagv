"""D435 acquisition. SDK frames are never hole-filled, smoothed, or aligned."""
from __future__ import annotations

import copy
import importlib.metadata
import json
import time
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from core import Snapshot


@dataclass
class Packet:
    depth: np.ndarray
    ir_left: np.ndarray
    ir_right: np.ndarray
    rgb: np.ndarray
    metadata: dict
    native_depth: object = None


def intrinsics_dict(i):
    return dict(width=i.width, height=i.height, fx=i.fx, fy=i.fy,
                ppx=i.ppx, ppy=i.ppy, model=str(i.model), coeffs=list(i.coeffs))


class D435Camera:
    def __init__(self, serial=None, width=640, height=480, fps=30):
        self.serial, self.width, self.height, self.fps = serial, width, height, fps
        self.pipeline = None
        self.streaming = False
        self.original_json = None
        self.original_options = {}

    def start(self):
        try:
            import pyrealsense2 as rs
        except ImportError as error:
            raise RuntimeError('pyrealsense2がありません。requirements.txtをインストールしてください。') from error
        self.rs = rs
        context = rs.context()
        devices = [d for d in context.query_devices()
                   if d.supports(rs.camera_info.name) and
                   'D435' in d.get_info(rs.camera_info.name)]
        if self.serial:
            devices = [d for d in devices if d.get_info(rs.camera_info.serial_number)==self.serial]
        if len(devices) != 1:
            raise RuntimeError('D435を1台接続してください。複数台ある場合は --serial で指定してください。')
        device = devices[0]
        self.serial = device.get_info(rs.camera_info.serial_number)
        advanced = rs.rs400_advanced_mode(device)
        if not advanced.is_enabled():
            advanced.toggle_advanced_mode(True)
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                time.sleep(0.25)
                matches = [d for d in rs.context().query_devices()
                           if d.get_info(rs.camera_info.serial_number)==self.serial]
                if matches:
                    candidate = rs.rs400_advanced_mode(matches[0])
                    if candidate.is_enabled():
                        device, advanced = matches[0], candidate
                        break
            else:
                raise RuntimeError('Advanced Modeへの切替後にD435を再認識できませんでした。再接続してください。')
        self.advanced = advanced
        self.original_json = advanced.serialize_json()
        config = rs.config()
        config.enable_device(self.serial)
        config.enable_stream(rs.stream.depth, self.width, self.height, rs.format.z16, self.fps)
        config.enable_stream(rs.stream.infrared, 1, self.width, self.height, rs.format.y8, self.fps)
        config.enable_stream(rs.stream.infrared, 2, self.width, self.height, rs.format.y8, self.fps)
        config.enable_stream(rs.stream.color, 640, 480, rs.format.rgb8, self.fps)
        self.pipeline = rs.pipeline()
        profile = self.pipeline.start(config)
        self.streaming = True
        self.device = profile.get_device()
        self.sensor = self.device.first_depth_sensor()
        self.advanced = rs.rs400_advanced_mode(self.device)
        self.pc = rs.pointcloud()
        self.scale = float(self.sensor.get_depth_scale())
        self.profiles = {
            'depth': profile.get_stream(rs.stream.depth).as_video_stream_profile(),
            'ir_left': profile.get_stream(rs.stream.infrared, 1).as_video_stream_profile(),
            'ir_right': profile.get_stream(rs.stream.infrared, 2).as_video_stream_profile(),
            'rgb': profile.get_stream(rs.stream.color).as_video_stream_profile(),
        }
        self.calibration = {k: intrinsics_dict(p.get_intrinsics()) for k,p in self.profiles.items()}
        self.extrinsics = {}
        for k,p in self.profiles.items():
            extr = self.profiles['depth'].get_extrinsics_to(p)
            self.extrinsics[f'depth_to_{k}'] = {
                'rotation_column_major': list(extr.rotation),
                'translation_m': list(extr.translation)}
        self.option_names = ['emitter_enabled', 'laser_power', 'depth_units', 'enable_auto_exposure',
                             'exposure', 'gain', 'visual_preset']
        self.original_options = self.read_options()
        self.info = dict(name=self.device.get_info(rs.camera_info.name), serial=self.serial,
                         firmware=self.device.get_info(rs.camera_info.firmware_version))
        try:
            self.info['pyrealsense2_version'] = importlib.metadata.version('pyrealsense2')
        except importlib.metadata.PackageNotFoundError:
            self.info['pyrealsense2_version'] = 'unknown'
        self.signature = f'{self.serial}:{self.width}x{self.height}@{self.fps}'
        low = self.advanced.get_depth_table(1).disparityShift
        high = self.advanced.get_depth_table(2).disparityShift
        self.shift_range = [int(low), int(high)]
        self.refresh_settings()
        self.settled = False
        return self.info

    def read_options(self):
        values = {}
        for name in self.option_names:
            option = getattr(self.rs.option, name)
            if self.sensor.supports(option):
                values[name] = float(self.sensor.get_option(option))
        return values

    def refresh_settings(self):
        self.scale = float(self.sensor.get_depth_scale())
        self.effective = dict(sensor_options=self.read_options(),
                              sensor_options_semantics='Read at settings apply; with AE enabled use per-frame metadata for actual exposure/gain.',
                              disparity_shift=int(self.advanced.get_depth_table().disparityShift),
                              depth_scale_m=self.scale,
                              advanced_mode=json.loads(self.advanced.serialize_json()))

    def set_option(self, name, value):
        option = getattr(self.rs.option, name)
        if not self.sensor.supports(option):
            raise ValueError(f'このカメラは{name}をサポートしていません。')
        limits = self.sensor.get_option_range(option)
        value = float(value)
        if not np.isfinite(value) or not limits.min <= value <= limits.max:
            raise ValueError(f'{name}は{limits.min}～{limits.max}で指定してください。')
        if limits.step:
            value = limits.min + round((value-limits.min)/limits.step)*limits.step
            value = min(limits.max, max(limits.min, value))
        self.sensor.set_option(option, value)

    def apply(self, settings):
        shift = int(settings['disparity_shift'])
        if not self.shift_range[0] <= shift <= self.shift_range[1]:
            raise ValueError(f'Disparity Shiftの範囲は{self.shift_range}です。')
        units = int(settings['depth_units_um'])
        if units <= 0:
            raise ValueError('Depth Unitsは正の整数µmで指定してください。')
        # Failure rolls back the complete pre-change state.
        previous_json = self.advanced.serialize_json()
        previous_options = self.read_options()
        try:
            self.set_option('emitter_enabled', int(settings['emitter']))
            self.set_option('laser_power', settings['laser_power'])
            self.set_option('enable_auto_exposure', int(settings['auto_exposure']))
            if not settings['auto_exposure']:
                self.set_option('exposure', settings['exposure_us'])
                self.set_option('gain', settings['gain'])
            # Set through the sensor option so SDK depth-scale observers update too.
            self.set_option('depth_units', units*1e-6)
            table = self.advanced.get_depth_table()
            table.disparityShift = shift
            self.advanced.set_depth_table(table)
        except Exception:
            self.advanced.load_json(previous_json)
            self._restore_options(previous_options)
            self.refresh_settings()
            self.settled = False
            raise
        self.refresh_settings()
        self.settled = False
        self.settle()

    def settle(self):
        # Drop transitional frames, without filtering the retained measurements.
        for _ in range(20):
            self.pipeline.wait_for_frames(5000)
        self.settled = True

    def read(self):
        if not self.settled:
            self.settle()
        frames = self.pipeline.wait_for_frames(5000)
        native = {'depth': frames.get_depth_frame(),
                  'ir_left': frames.get_infrared_frame(1),
                  'ir_right': frames.get_infrared_frame(2),
                  'rgb': frames.get_color_frame()}
        if not all(native.values()):
            raise RuntimeError('Depth・左右IR・RGBを含むフレームセットを取得できません。')
        frame_info = {}
        for name, frame in native.items():
            attributes = {}
            for key in self.rs.frame_metadata_value.__members__.values():
                try:
                    if frame.supports_frame_metadata(key):
                        attributes[str(key)] = frame.get_frame_metadata(key)
                except RuntimeError:
                    continue
            frame_info[name] = dict(number=frame.get_frame_number(),
                                    timestamp_ms=frame.get_timestamp(),
                                    timestamp_domain=str(frame.get_frame_timestamp_domain()),
                                    metadata=attributes)
        meta = dict(source='realsense_d435', device=self.info,
                    host_acquired_at_local_iso8601=datetime.now().astimezone().isoformat(),
                    host_monotonic_ns=time.monotonic_ns(), depth_scale_m=self.scale,
                    intrinsics=self.calibration, extrinsics=self.extrinsics,
                    effective_settings=copy.deepcopy(self.effective), frames=frame_info,
                    stream_signature=self.signature,
                    startup_settings=dict(sensor_options=self.original_options,
                                          advanced_mode=json.loads(self.original_json)))
        return Packet(*(np.asanyarray(native[k].get_data()).copy()
                        for k in ('depth','ir_left','ir_right','rgb')),
                      metadata=meta, native_depth=native['depth'])

    def snapshot(self, packet):
        points = self.pc.calculate(packet.native_depth)
        xyz = np.asanyarray(points.get_vertices()).view(np.float32).reshape(
            packet.depth.shape+(3,)).copy()
        xyz[packet.depth==0] = np.nan
        return Snapshot(packet.depth, packet.ir_left, packet.ir_right, packet.rgb,
                        xyz, copy.deepcopy(packet.metadata))

    def _restore_options(self, values):
        # Disable AE before restoring its manual controls, then restore AE state.
        if 'enable_auto_exposure' in values:
            self.set_option('enable_auto_exposure', 0)
        for name in ('depth_units','emitter_enabled','laser_power','exposure','gain'):
            if name in values:
                self.set_option(name, values[name])
        if 'enable_auto_exposure' in values:
            self.set_option('enable_auto_exposure', values['enable_auto_exposure'])

    def close(self):
        errors = []
        if self.streaming:
            try:
                self.pipeline.stop()
            except Exception as error:
                errors.append(str(error))
            self.streaming = False
        if self.original_json is not None and hasattr(self, 'sensor'):
            try:
                self.advanced.load_json(self.original_json)
                self._restore_options(self.original_options)
            except Exception as error:
                errors.append(str(error))
        return errors


class DemoCamera:
    """Explicitly synthetic data, for installation/export testing only."""
    width, height, fps, scale = 640, 480, 30, 0.001
    signature = 'SYNTHETIC_DEMO:640x480@30'
    shift_range = [0, 255]

    def start(self):
        self.info = {'name':'SYNTHETIC DEMO - not camera measurements'}
        self.rng = np.random.default_rng(23)
        self.number = 0
        self.effective = {'disparity_shift':0, 'sensor_options':{}}
        return self.info

    def apply(self, settings):
        if not 0 <= int(settings['disparity_shift']) <= 255:
            raise ValueError('Demo shift must be 0-255.')
        self.effective = copy.deepcopy(settings)
        self.scale = float(settings.get('depth_units_um',1000))*1e-6
        if self.scale <= 0:raise ValueError('Depth Units must be positive.')

    def read(self):
        time.sleep(1/30)
        self.number += 1
        y,x = np.indices((self.height,self.width))
        depth = np.full((480,640),round(0.30/self.scale),dtype=np.uint16)
        depth[30:130,100:550] = round(0.20/self.scale)
        depth[130:160,130:520] = 0
        depth[self.rng.random(depth.shape)<0.002] = 0
        ir = ((x//8+y//8)%2*65+100).astype(np.uint8)
        rgb = np.stack((ir,ir,np.full_like(ir,170)),axis=-1)
        meta = dict(source='synthetic_demo', depth_scale_m=self.scale,
                    host_acquired_at_local_iso8601=datetime.now().astimezone().isoformat(),
                    host_monotonic_ns=time.monotonic_ns(), device=self.info,
                    effective_settings=copy.deepcopy(self.effective),
                    stream_signature=self.signature,
                    intrinsics={'depth':dict(width=640,height=480,fx=385.,fy=385.,ppx=320.,ppy=240.,
                                             model='none',coeffs=[0.,0.,0.,0.,0.])},
                    frames={k:dict(number=self.number,timestamp_ms=time.monotonic()*1000,
                                   timestamp_domain='synthetic_monotonic')
                            for k in ('depth','ir_left','ir_right','rgb')})
        return Packet(depth,ir,ir.copy(),rgb,meta)

    def snapshot(self, packet):
        y,x = np.indices(packet.depth.shape)
        z = packet.depth.astype(np.float32)*self.scale
        xyz = np.stack(((x-320)/385*z,(y-240)/385*z,z),axis=-1).astype(np.float32)
        xyz[packet.depth==0] = np.nan
        return Snapshot(packet.depth,packet.ir_left,packet.ir_right,packet.rgb,xyz,copy.deepcopy(packet.metadata))

    def close(self):
        return []
