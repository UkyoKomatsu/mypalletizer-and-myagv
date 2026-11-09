"""Camera-independent research data formats. No depth hole filling or smoothing."""
from __future__ import annotations

import hashlib
import json
import platform
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

SCHEMA_VERSION = 1


@dataclass
class Snapshot:
    depth: np.ndarray
    ir_left: np.ndarray
    ir_right: np.ndarray
    rgb: np.ndarray
    xyz: np.ndarray
    metadata: dict

    def validate(self):
        h, w = self.depth.shape
        if self.depth.dtype != np.uint16:
            raise ValueError('Depth must be uint16 Z16.')
        if self.xyz.shape != (h, w, 3):
            raise ValueError('Organized XYZ and Depth dimensions differ.')
        for image in (self.ir_left, self.ir_right):
            if image.shape != (h, w) or image.dtype != np.uint8:
                raise ValueError('IR profile must match the selected Depth profile.')
        if self.rgb.ndim != 3 or self.rgb.shape[2] != 3 or self.rgb.dtype != np.uint8:
            raise ValueError('Color must be RGB8.')
        scale = self.metadata['depth_scale_m']
        if not np.isfinite(scale) or scale <= 0:
            raise ValueError('A measured, positive depth scale is required.')
        valid = self.depth > 0
        if not np.isfinite(self.xyz[valid]).all() or np.any(self.xyz[valid, 2] <= 0):
            raise ValueError('A valid Depth pixel has invalid XYZ.')
        if not np.allclose(self.xyz[..., 2][valid], self.depth[valid] * scale,
                           rtol=2e-5, atol=1e-6):
            raise ValueError('XYZ Z values disagree with Depth and its scale.')


def normalized_roi(roi, width, height):
    if roi is None:
        return 0, 0, width, height
    x0, y0, x1, y1 = map(int, roi)
    x0, x1 = sorted((max(0, min(width, x0)), max(0, min(width, x1))))
    y0, y1 = sorted((max(0, min(height, y0)), max(0, min(height, y1))))
    if x1 <= x0 or y1 <= y0:
        raise ValueError('ROI has no pixels.')
    return x0, y0, x1, y1


def region_metrics(depth, ir, scale, roi=None):
    h, w = depth.shape
    x0, y0, x1, y1 = normalized_roi(roi, w, h)
    d = depth[y0:y1, x0:x1]
    light = ir[y0:y1, x0:x1]
    valid = d > 0
    values = d[valid].astype(np.float64) * scale
    # Count isolated single zero pixels, without modifying any pixel.
    # Neighbours are checked in the original full image, not padded ROI edges.
    full_valid = depth > 0
    all_neighbours_valid = np.zeros_like(full_valid)
    centre = np.ones((max(h-2, 0), max(w-2, 0)), dtype=bool)
    for dy in range(3):
        for dx in range(3):
            if dy != 1 or dx != 1:
                centre &= full_valid[dy:dy+h-2, dx:dx+w-2]
    all_neighbours_valid[1:-1, 1:-1] = centre
    isolated = (~full_valid) & all_neighbours_valid
    return {
        'roi_xyxy_exclusive': [x0, y0, x1, y1],
        'pixels': int(d.size), 'invalid_pixels': int((~valid).sum()),
        'invalid_fraction': float((~valid).mean()),
        'isolated_single_zero_pixels': int(isolated[y0:y1, x0:x1].sum()),
        'depth_median_m': float(np.median(values)) if values.size else None,
        'depth_p05_m': float(np.percentile(values, 5)) if values.size else None,
        'depth_p95_m': float(np.percentile(values, 95)) if values.size else None,
        'ir_near_black_fraction_le_3': float((light <= 3).mean()),
        'ir_near_saturation_fraction_ge_252': float((light >= 252).mean()),
        'interpretation': 'Descriptive only; not a classification of noise or occlusion.',
    }


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    allow_nan=False), encoding='utf-8')


def depth_preview(depth, scale, near_m=0.10, far_m=0.60):
    """Fixed display limits, independent of point-cloud export."""
    t = np.clip((depth.astype(float)*scale-near_m)/(far_m-near_m), 0, 1)
    anchors = np.array([[49, 54, 149], [69, 117, 180], [116, 173, 209],
                        [171, 217, 233], [254, 224, 144], [244, 109, 67],
                        [165, 0, 38]], dtype=float)
    q = t*(len(anchors)-1)
    i = np.minimum(q.astype(int), len(anchors)-2)
    f = (q-i)[..., None]
    image = (anchors[i]*(1-f)+anchors[i+1]*f).astype(np.uint8)
    image[depth == 0] = 0
    return image


def write_ply(path, xyz, valid):
    """Metres in the Depth optical frame. No fabricated points for zeros."""
    points = np.asarray(xyz[valid], dtype='<f4')
    header = ('ply\nformat binary_little_endian 1.0\n'
              'comment units metres; depth optical coordinates +X right +Y down +Z forward\n'
              f'element vertex {len(points)}\n'
              'property float x\nproperty float y\nproperty float z\nend_header\n')
    with Path(path).open('wb') as stream:
        stream.write(header.encode('ascii'))
        stream.write(points.tobytes(order='C'))


class CaptureWriter:
    """A unique capture folder is committed only after all requested frames exist."""
    def __init__(self, root, label, note, expected_frames, rois=None):
        if not 1 <= expected_frames <= 60:
            raise ValueError('Frames per capture must be between 1 and 60.')
        safe = re.sub(r'[^\w-]+', '_', label, flags=re.UNICODE).strip('_')[:60] or 'capture'
        stamp = datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f')
        self.final = Path(root).expanduser().resolve()/f'{stamp}_{safe}_{uuid.uuid4().hex[:8]}'
        self.partial = self.final.with_name(self.final.name+'.partial')
        self.partial.mkdir(parents=True, exist_ok=False)
        self.expected, self.count = expected_frames, 0
        self.rois = rois or {}
        self.label, self.note = label, note
        self.mean = self.m2 = self.samples = None
        self.records = []
        self.identity = None

    def add(self, frame: Snapshot):
        frame.validate()
        if self.count >= self.expected:
            raise ValueError('More frames received than requested.')
        identity = (frame.depth.shape, frame.metadata['depth_scale_m'],
                    json.dumps(frame.metadata.get('effective_settings'), sort_keys=True))
        if self.identity is not None and identity != self.identity:
            raise ValueError('Camera settings or Depth profile changed within the capture.')
        self.identity = identity
        folder = self.partial/f'frame_{self.count:04d}'
        folder.mkdir()
        valid = frame.depth != 0
        xyz = frame.xyz.astype(np.float32, copy=True)
        xyz[~valid] = np.nan
        depth_m = frame.depth.astype(np.float64)*frame.metadata['depth_scale_m']
        if self.mean is None:
            self.mean = np.zeros(frame.depth.shape, dtype=np.float64)
            self.m2 = self.mean.copy()
            self.samples = np.zeros(frame.depth.shape, dtype=np.uint32)
        self.samples[valid] += 1
        delta = depth_m[valid]-self.mean[valid]
        self.mean[valid] += delta/self.samples[valid]
        self.m2[valid] += delta*(depth_m[valid]-self.mean[valid])
        Image.fromarray(frame.depth).save(folder/'depth_z16.png')
        frame.depth.astype('<u2').tofile(folder/'depth_z16.raw')
        Image.fromarray(frame.ir_left).save(folder/'ir_left.png')
        Image.fromarray(frame.ir_right).save(folder/'ir_right.png')
        Image.fromarray(frame.rgb).save(folder/'rgb_unaligned.png')
        Image.fromarray((~valid).astype(np.uint8)*255).save(folder/'invalid_mask.png')
        Image.fromarray(depth_preview(frame.depth, frame.metadata['depth_scale_m'])).save(folder/'depth_preview.png')
        np.savez_compressed(folder/'arrays.npz', depth_z16=frame.depth,
                            xyz_m=xyz, valid_mask=valid, ir_left=frame.ir_left,
                            ir_right=frame.ir_right, rgb_unaligned=frame.rgb)
        write_ply(folder/'points.ply', xyz, valid)
        metrics = {'whole_frame': region_metrics(frame.depth, frame.ir_left,
                                                frame.metadata['depth_scale_m'])}
        for name, roi in self.rois.items():
            if roi is not None:
                metrics[name] = region_metrics(frame.depth, frame.ir_left,
                                              frame.metadata['depth_scale_m'], roi)
        meta = dict(frame.metadata, schema_version=SCHEMA_VERSION,
                    software=dict(capture_program_version='1.0.0',
                                  python=platform.python_version(),numpy=np.__version__),
                    host_filters=[], points_units='metres',
                    coordinates='+X right, +Y down, +Z forward; depth optical frame',
                    rgb_alignment='not aligned; calibration is stored separately',
                    invalid_xyz='NaN in arrays.npz; invalid points omitted in PLY',
                    roi_metrics=metrics)
        write_json(folder/'metadata.json', meta)
        self.records.append({'frame_folder': folder.name,
                             'frames': meta.get('frames', {}), 'metrics': metrics})
        self.count += 1

    def finish(self):
        if self.count != self.expected:
            raise ValueError(f'Only {self.count}/{self.expected} frames were saved.')
        if self.count > 1:
            variance = np.divide(self.m2, self.samples-1,
                                 out=np.full_like(self.mean, np.nan), where=self.samples>1)
            mean = np.where(self.samples>0, self.mean, np.nan)
            invalid = 1-self.samples.astype(float)/self.count
            np.savez_compressed(self.partial/'temporal_statistics.npz',
                                valid_samples=self.samples, mean_depth_m=mean,
                                std_depth_m=np.sqrt(variance), invalid_frequency=invalid)
            Image.fromarray(np.rint(invalid*255).astype(np.uint8)).save(
                self.partial/'invalid_frequency.png')
        write_json(self.partial/'capture.json', {
            'schema_version': SCHEMA_VERSION, 'label': self.label, 'note': self.note,
            'frames_requested': self.expected, 'frames_saved': self.count,
            'completed_at_local_iso8601': datetime.now().astimezone().isoformat(),
            'rois': self.rois, 'records': self.records,
            'sampling': 'Triggered only. Burst frame intervals may include disk-writing delays; use timestamps.',
            'temporal_statistics': 'Sample std of valid readings only; NaN if fewer than 2. No hole filling.',
        })
        with (self.partial/'checksums.sha256').open('w', encoding='utf8') as stream:
            for file in sorted(self.partial.rglob('*')):
                if file.is_file() and file.name != 'checksums.sha256':
                    digest = hashlib.sha256(file.read_bytes()).hexdigest()
                    stream.write(f'{digest}  {file.relative_to(self.partial).as_posix()}\n')
        self.partial.rename(self.final)
        return self.final

    def fail(self, error):
        write_json(self.partial/'INCOMPLETE.json', {'error': str(error), 'saved_frames': self.count})


def select_profile(profiles, target_cm, signature):
    """Explicit calibrated distance intervals, never a guessed disparity formula."""
    if not np.isfinite(target_cm) or target_cm <= 0:
        raise ValueError('Target distance must be positive.')
    matches = [p for p in profiles if p['signature'] == signature and
               p['min_cm'] <= target_cm <= p['max_cm']]
    if len(matches) != 1:
        raise ValueError('この距離に対応する登録設定がありません（または範囲が重複しています）。手動で確認・登録してください。')
    return matches[0]
