"""Summarize completed captures; no SDK or camera is needed."""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import numpy as np
from core import normalized_roi


def analyze(root, output):
    root=Path(root).resolve();output=Path(output).resolve()
    captures=([root/'capture.json'] if (root/'capture.json').is_file()
              else sorted(root.rglob('capture.json')))
    rows=[]
    for manifest in captures:
        if any(p.endswith('.partial') for p in manifest.parts):continue
        capture=json.loads(manifest.read_text(encoding='utf8'))
        temporal_path=manifest.parent/'temporal_statistics.npz'
        temporal=np.load(temporal_path,allow_pickle=False) if temporal_path.exists() else None
        try:
            for record in capture['records']:
                meta=json.loads((manifest.parent/record['frame_folder']/'metadata.json').read_text(encoding='utf8'))
                for region,metrics in record['metrics'].items():
                    effective=meta.get('effective_settings',{})
                    sensor=effective.get('sensor_options',effective)
                    row=dict(capture=manifest.parent.name,frame=record['frame_folder'],
                             source=meta['source'],region=region,
                             disparity_shift=effective.get('disparity_shift'),
                             laser_power=sensor.get('laser_power'),
                             emitter_enabled=sensor.get('emitter_enabled',sensor.get('emitter')),
                             auto_exposure=sensor.get('enable_auto_exposure',sensor.get('auto_exposure')),
                             depth_scale_m=meta['depth_scale_m'],
                             depth_frame_number=meta['frames']['depth']['number'],
                             depth_timestamp_ms=meta['frames']['depth']['timestamp_ms'])
                    row.update({k:v for k,v in metrics.items() if k not in ('interpretation','roi_xyxy_exclusive')})
                    row['roi_xyxy_exclusive']=json.dumps(metrics['roi_xyxy_exclusive'])
                    row.update(temporal_median_std_m=None,temporal_invalid_fraction=None,
                               fraction_with_at_least_two_valid_samples=None)
                    if temporal is not None:
                        h,w=temporal['valid_samples'].shape
                        x0,y0,x1,y1=normalized_roi(metrics['roi_xyxy_exclusive'],w,h)
                        std=temporal['std_depth_m'][y0:y1,x0:x1]
                        finite=std[np.isfinite(std)]
                        row['temporal_median_std_m']=float(np.median(finite)) if finite.size else None
                        row['temporal_invalid_fraction']=float(temporal['invalid_frequency'][y0:y1,x0:x1].mean())
                        row['fraction_with_at_least_two_valid_samples']=float((temporal['valid_samples'][y0:y1,x0:x1]>=2).mean())
                    rows.append(row)
        finally:
            if temporal is not None:temporal.close()
    if not rows:raise ValueError('Completed captures were not found. Specify a captures folder or one capture folder.')
    output.mkdir(parents=True,exist_ok=True)
    path=output/'analysis_summary.csv'
    if path.exists():raise FileExistsError(f'Refusing to overwrite {path}. Choose another --out folder.')
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)
    return path,len(rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('captures',type=Path)
    parser.add_argument('--out',type=Path,required=True,help='New folder for analysis_summary.csv')
    args=parser.parse_args()
    path,count=analyze(args.captures,args.out)
    print(f'{count} rows: {path}')


if __name__=='__main__':main()
