"""Button-triggered D435 capture UI. Run: python app.py [--demo]."""
from __future__ import annotations

import argparse
import copy
import json
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageTk

from camera import D435Camera, DemoCamera
from core import CaptureWriter, depth_preview, normalized_roi, region_metrics, select_profile, write_json

BASE = Path(__file__).resolve().parent


class CameraWorker(threading.Thread):
    def __init__(self, args):
        super().__init__(daemon=True)
        self.args = args
        self.commands = queue.Queue()
        self.events = queue.Queue()
        self.previews = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.camera = None
        self.epoch = 0

    def post(self, kind, value=None):
        self.events.put((kind, value))

    def _preview(self, packet):
        packet.metadata['settings_epoch'] = self.epoch
        try:
            self.previews.get_nowait()
        except queue.Empty:
            pass
        self.previews.put_nowait(packet)

    def _capture(self, payload):
        packet = payload['packet']
        if packet.metadata.get('settings_epoch') != self.epoch:
            raise RuntimeError('表示後に設定が変わりました。新しい映像の表示後に保存してください。')
        if time.monotonic_ns()-packet.metadata['host_monotonic_ns'] > 2_000_000_000:
            raise RuntimeError('表示フレームが古くなっています。接続状態を確認してください。')
        writer = CaptureWriter(payload['root'], payload['label'], payload['note'],
                               payload['frames'], payload['rois'])
        try:
            previous_number = packet.metadata['frames']['depth']['number']
            for index in range(payload['frames']):
                if self.stop_event.is_set():
                    raise RuntimeError('終了操作により撮影を中断しました。')
                if index:
                    packet = self.camera.read()
                    if packet.metadata['frames']['depth']['number'] <= previous_number:
                        raise RuntimeError('撮影中にフレーム番号が更新されませんでした。')
                    previous_number = packet.metadata['frames']['depth']['number']
                    packet.metadata['settings_epoch'] = self.epoch
                snap = self.camera.snapshot(packet)
                snap.metadata['capture_context'] = payload['context']
                writer.add(snap)
                self.post('progress', f'保存中 {index+1}/{payload["frames"]} フレーム')
            path = writer.finish()
            self.post('saved', str(path))
        except Exception as error:
            writer.fail(error)
            raise

    def run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    kind, payload = self.commands.get(timeout=0.01 if self.camera else 0.1)
                except queue.Empty:
                    kind, payload = None, None
                if kind:
                    try:
                        if kind == 'connect':
                            if self.camera:
                                raise RuntimeError('すでに接続しています。')
                            self.post('status', '接続中。Advanced Modeの切替時は再認識を待ちます。')
                            cam = DemoCamera() if self.args.demo else D435Camera(
                                self.args.serial, self.args.width, self.args.height, self.args.fps)
                            self.camera = cam
                            info = cam.start()
                            cam.apply(payload)
                            self.epoch += 1
                            self.post('connected', dict(info=info, signature=cam.signature,
                                                       shift_range=cam.shift_range,
                                                       effective=copy.deepcopy(cam.effective)))
                        elif kind == 'apply':
                            self.camera.apply(payload)
                            self.epoch += 1
                            self.post('applied', copy.deepcopy(self.camera.effective))
                        elif kind == 'capture':
                            self._capture(payload)
                    except Exception as error:
                        if kind == 'connect' and self.camera:
                            self.camera.close()
                            self.camera = None
                        self.post('error', f'{kind}: {error}')
                    finally:
                        if kind in ('apply','connect'):
                            try:self.previews.get_nowait()
                            except queue.Empty:pass
                        self.post('command_done', kind)
                if self.camera and not self.stop_event.is_set():
                    try:
                        self._preview(self.camera.read())
                    except Exception as error:
                        restoration = self.camera.close()
                        self.camera = None
                        self.post('disconnected', str(error)+('; '+str(restoration) if restoration else ''))
        finally:
            if self.camera:
                errors = self.camera.close()
                if errors:
                    self.post('restore_warning', '; '.join(errors))


class CaptureApp:
    preview_size = (400, 300)

    def __init__(self, root, args):
        self.root, self.args = root, args
        self.worker = CameraWorker(args)
        self.worker.start()
        self.packet = None
        self.connected = self.busy = self.wait_new_preview = self.closing = False
        self.signature = None
        self.rois = {'reference_surface':None, 'under_obstacle':None}
        self.profiles_path = BASE/'distance_profiles.json'
        self.profiles = []
        self.profile_load_error = None
        if self.profiles_path.exists():
            try:
                loaded = json.loads(self.profiles_path.read_text(encoding='utf8'))
                if not isinstance(loaded, list):
                    raise ValueError('設定の形式はリストである必要があります。')
                self.profiles = loaded
            except (ValueError, OSError) as error:
                self.profile_load_error = str(error)
        self._build()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.bind('<Control-s>', lambda event:self.capture())
        self.root.after(50, self.poll)

    def _build(self):
        self.root.title('D435 研究用データ収集'+(' [合成デモ]' if self.args.demo else ''))
        self.root.geometry('1200x850')
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill='both', expand=True)
        view = ttk.Frame(main)
        view.grid(row=0,column=0,sticky='nw')
        self.canvases, self.images = {}, {}
        titles = {'depth':'Depth（0は黒／表示範囲0.10～0.60m）',
                  'rgb':'RGB（Depthへ未整列）', 'ir_left':'左IR（取得値のまま）',
                  'ir_right':'右IR（取得値のまま）'}
        for index,(key,title) in enumerate(titles.items()):
            tile=ttk.Frame(view,padding=3)
            tile.grid(row=index//2,column=index%2)
            ttk.Label(tile,text=title).pack(anchor='w')
            c=tk.Canvas(tile,width=400,height=300,bg='#101a23',highlightthickness=0)
            c.pack();self.canvases[key]=c
        c=self.canvases['depth']
        c.bind('<ButtonPress-1>',self.roi_start)
        c.bind('<B1-Motion>',self.roi_drag)
        c.bind('<ButtonRelease-1>',self.roi_end)
        self.metrics=tk.StringVar(value='接続すると、欠損率と距離中央値を表示します。')
        ttk.Label(view,textvariable=self.metrics,wraplength=800).grid(row=2,column=0,columnspan=2,sticky='w',pady=8)
        self.roi_mode=tk.StringVar(value='reference_surface')
        roi_box=ttk.Frame(view);roi_box.grid(row=3,column=0,columnspan=2,sticky='w')
        for text,value in [('参照面ROIを指定','reference_surface'),('障害物下ROIを指定','under_obstacle')]:
            ttk.Radiobutton(roi_box,text=text,variable=self.roi_mode,value=value).pack(side='left')
        ttk.Button(roi_box,text='ROIを解除',command=self.clear_rois).pack(side='left',padx=10)
        ttk.Label(view,text='Depth画像をドラッグしてROIを指定。ROIは集計用で、保存範囲を切り取りません。').grid(row=4,column=0,columnspan=2,sticky='w')

        main.rowconfigure(0,weight=1)
        side_scroll=tk.Canvas(main,width=330,height=780,highlightthickness=0)
        side_scroll.grid(row=0,column=1,sticky='nsew')
        scrollbar=ttk.Scrollbar(main,orient='vertical',command=side_scroll.yview)
        scrollbar.grid(row=0,column=2,sticky='ns')
        side_scroll.configure(yscrollcommand=scrollbar.set)
        side=ttk.Frame(side_scroll,padding=(12,0))
        side_scroll.create_window((0,0),window=side,anchor='nw')
        side.bind('<Configure>',lambda event:side_scroll.configure(scrollregion=side_scroll.bbox('all')))
        self.connect_button=ttk.Button(side,text='D435に接続' if not self.args.demo else 'デモ開始',command=self.connect)
        self.connect_button.pack(fill='x',pady=4)
        self.status=tk.StringVar(value='未接続。保存ボタンを押すまで撮影データを書き込みません。')
        ttk.Label(side,textvariable=self.status,wraplength=300).pack(fill='x',pady=5)
        ttk.Label(side,text='保存先').pack(anchor='w')
        self.output=tk.StringVar(value=str(Path(self.args.output).expanduser().resolve()))
        ttk.Entry(side,textvariable=self.output,width=35).pack(fill='x')
        ttk.Button(side,text='保存先を選ぶ',command=self.choose_output).pack(fill='x',pady=3)
        self.label=tk.StringVar(value='SYNTHETIC_DEMO' if self.args.demo else 'table_20cm')
        self.note=tk.StringVar(value='')
        ttk.Label(side,text='試料・条件名').pack(anchor='w')
        ttk.Entry(side,textvariable=self.label).pack(fill='x')
        ttk.Label(side,text='メモ').pack(anchor='w')
        ttk.Entry(side,textvariable=self.note).pack(fill='x')
        self.frames=tk.IntVar(value=1)
        count=ttk.Frame(side);count.pack(fill='x',pady=5)
        ttk.Label(count,text='1回の保存枚数').pack(side='left')
        ttk.Spinbox(count,from_=1,to=60,textvariable=self.frames,width=5).pack(side='right')
        self.save_button=ttk.Button(side,text='保存（表示フレーム＋指定枚数）',command=self.capture,state='disabled')
        self.save_button.pack(fill='x',pady=6)
        ttk.Button(side,text='保存した点群を見る',command=self.view_saved_cloud).pack(fill='x',pady=2)
        ttk.Label(side,text='通常は1枚。時間変動を調べる場合は30枚などを指定。',wraplength=300).pack(anchor='w')

        box=ttk.LabelFrame(side,text='撮影条件（変更後に適用）',padding=6);box.pack(fill='x',pady=8)
        self.emitter=tk.BooleanVar(value=True)
        self.auto=tk.BooleanVar(value=True)
        ttk.Checkbutton(box,text='IRプロジェクタを使う',variable=self.emitter).grid(row=0,column=0,columnspan=2,sticky='w')
        self.laser=tk.DoubleVar(value=60)
        self.exposure=tk.DoubleVar(value=5000)
        self.gain=tk.DoubleVar(value=16)
        self.shift=tk.IntVar(value=0)
        self.depth_units=tk.IntVar(value=100)
        for row,(label,var) in enumerate([('レーザー出力 [mW]',self.laser),('露光 [us]（自動OFF時）',self.exposure),
                                          ('ゲイン（自動OFF時）',self.gain),('Disparity Shift',self.shift),
                                          ('Depth Units [µm]',self.depth_units)],start=1):
            ttk.Label(box,text=label).grid(row=row,column=0,sticky='w')
            ttk.Entry(box,textvariable=var,width=9).grid(row=row,column=1)
        ttk.Checkbutton(box,text='自動露光',variable=self.auto).grid(row=6,column=0,columnspan=2,sticky='w')
        self.apply_button=ttk.Button(box,text='設定を適用',command=self.apply,state='disabled')
        self.apply_button.grid(row=7,column=0,columnspan=2,sticky='ew',pady=4)

        distance=ttk.LabelFrame(side,text='距離に応じたShift切替',padding=6);distance.pack(fill='x')
        self.target=tk.DoubleVar(value=20)
        self.min_cm=tk.DoubleVar(value=18)
        self.max_cm=tk.DoubleVar(value=22)
        for row,(label,var) in enumerate([('対象距離 [cm]',self.target),('登録する最小距離 [cm]',self.min_cm),
                                          ('登録する最大距離 [cm]',self.max_cm)]):
            ttk.Label(distance,text=label).grid(row=row,column=0,sticky='w')
            ttk.Entry(distance,textvariable=var,width=8).grid(row=row,column=1)
        self.profile_button=ttk.Button(distance,text='現在の適用済みShiftを登録',command=self.register_profile,state='disabled')
        self.profile_button.grid(row=3,column=0,columnspan=2,sticky='ew',pady=3)
        self.distance_button=ttk.Button(distance,text='対象距離の登録Shiftを適用',command=self.apply_distance,state='disabled')
        self.distance_button.grid(row=4,column=0,columnspan=2,sticky='ew')
        ttk.Label(side,text='距離だけからShiftの最適値は推測しません。未登録の距離は手動で確認してください。',wraplength=300).pack(pady=6)
        ttk.Button(side,text='終了',command=self.close).pack(fill='x')

    def settings(self):
        return dict(emitter=self.emitter.get(),laser_power=self.laser.get(),
                    auto_exposure=self.auto.get(),exposure_us=self.exposure.get(),
                    gain=self.gain.get(),disparity_shift=self.shift.get(),depth_units_um=self.depth_units.get())

    def controls(self):
        ready=self.connected and not self.busy and not self.wait_new_preview and not self.closing
        self.save_button.configure(state='normal' if ready and self.packet else 'disabled')
        for widget in (self.apply_button,self.profile_button,self.distance_button):
            widget.configure(state='normal' if ready else 'disabled')
        self.connect_button.configure(state='disabled' if self.connected or self.busy or self.closing else 'normal')

    def connect(self):
        if self.busy or self.connected:return
        try:settings=self.settings()
        except (ValueError,tk.TclError) as error:
            messagebox.showerror('入力値',str(error));return
        self.busy=True;self.controls()
        self.worker.commands.put(('connect',settings))

    def apply(self):
        if not self.connected or self.busy:return
        try:settings=self.settings()
        except (ValueError,tk.TclError) as error:
            messagebox.showerror('入力値',str(error));return
        self.busy=True;self.wait_new_preview=True;self.packet=None
        self.controls();self.status.set('設定を変更し、移行中のフレームを破棄しています。')
        self.worker.commands.put(('apply',settings))

    def capture(self):
        if not self.connected or self.busy or self.wait_new_preview or self.packet is None:return
        try:
            count=self.frames.get();target=self.target.get()
            if not 1<=count<=60 or not np.isfinite(target) or target<=0:
                raise ValueError('枚数は1～60、対象距離は正の数で指定してください。')
            if not self.output.get().strip():raise ValueError('保存先を指定してください。')
        except (ValueError,tk.TclError) as error:
            messagebox.showerror('入力値',str(error));return
        self.busy=True;self.controls();self.status.set('保存を開始します。')
        context=dict(target_distance_cm_user_entered=target,
                     button_pressed_at_local_iso8601=datetime.now().astimezone().isoformat(),
                     first_frame='The frame displayed at the time of the click',
                     distance_profiles=copy.deepcopy(self.profiles))
        self.worker.commands.put(('capture',dict(packet=self.packet,frames=count,
            root=self.output.get(),label=self.label.get(),note=self.note.get(),
            rois=copy.deepcopy(self.rois),context=context)))

    def choose_output(self):
        value=filedialog.askdirectory(title='保存先を選択')
        if value:self.output.set(value)

    def view_saved_cloud(self):
        from view_cloud import CloudViewer
        path=filedialog.askopenfilename(title='点群のarrays.npzを選択',
            initialdir=self.output.get(),filetypes=[('Point arrays','arrays.npz')])
        if not path:return
        top=tk.Toplevel(self.root)
        try:CloudViewer(top,path)
        except Exception as error:
            top.destroy();messagebox.showerror('点群表示',str(error))

    def register_profile(self):
        if self.busy or self.packet is None:return
        if self.profile_load_error:
            messagebox.showerror('登録設定',f'既存設定を読み取れないため上書きしません。{self.profile_load_error}');return
        try:
            lo,hi=self.min_cm.get(),self.max_cm.get()
            if not np.isfinite([lo,hi]).all() or not 0<lo<=hi:
                raise ValueError('距離範囲は0より大きく、最小≦最大にしてください。')
            effective=self.packet.metadata['effective_settings']
            shift=int(effective.get('disparity_shift',0))
            for p in self.profiles:
                if p['signature']==self.signature and max(lo,p['min_cm'])<=min(hi,p['max_cm']):
                    raise ValueError('登録済みの距離範囲と重複しています。distance_profiles.jsonを編集してください。')
            new_profile=dict(signature=self.signature,min_cm=lo,max_cm=hi,disparity_shift=shift,
                             source=self.packet.metadata['source'],
                             registered_at=datetime.now().astimezone().isoformat(),
                             note='Operator-selected profile; not an automatic accuracy validation.')
            updated=self.profiles+[new_profile]
            temporary=self.profiles_path.with_suffix('.json.tmp')
            write_json(temporary,updated);temporary.replace(self.profiles_path)
            self.profiles=updated
            self.status.set(f'{lo:g}～{hi:g}cm の Shift={shift} を登録しました。')
        except (ValueError,OSError,KeyError,tk.TclError) as error:
            messagebox.showerror('登録設定',str(error))

    def apply_distance(self):
        if self.busy or not self.connected:return
        try:
            profile=select_profile(self.profiles,self.target.get(),self.signature)
            self.shift.set(profile['disparity_shift'])
            self.apply()
        except (ValueError,KeyError,tk.TclError) as error:
            messagebox.showerror('距離設定',str(error))

    def roi_start(self,event):
        if self.packet is None or self.busy:return
        self.drag_begin=(event.x,event.y)

    def roi_drag(self,event):
        if not hasattr(self,'drag_begin'):return
        c=self.canvases['depth'];c.delete('drag')
        c.create_rectangle(*self.drag_begin,event.x,event.y,outline='#ff8e32',width=2,tags='drag')

    def roi_end(self,event):
        if not hasattr(self,'drag_begin') or self.packet is None:return
        x,y=self.drag_begin;del self.drag_begin
        h,w=self.packet.depth.shape
        roi=(int(x*w/400),int(y*h/300),int(event.x*w/400),int(event.y*h/300))
        try:self.rois[self.roi_mode.get()]=normalized_roi(roi,w,h)
        except ValueError:pass
        self.canvases['depth'].delete('drag')
        self.render(self.packet)

    def clear_rois(self):
        self.rois={k:None for k in self.rois}
        if self.packet:self.render(self.packet)

    def render(self,packet):
        scale=packet.metadata['depth_scale_m']
        arrays=dict(depth=depth_preview(packet.depth,scale),rgb=packet.rgb,
                    ir_left=packet.ir_left,ir_right=packet.ir_right)
        for key,array in arrays.items():
            photo=ImageTk.PhotoImage(Image.fromarray(array).resize(self.preview_size,Image.Resampling.NEAREST))
            self.images[key]=photo
            c=self.canvases[key];c.delete('image');c.create_image(0,0,anchor='nw',image=photo,tags='image')
        c=self.canvases['depth'];c.delete('roi')
        h,w=packet.depth.shape
        for name,color in [('reference_surface','#00e5ff'),('under_obstacle','#ffae42')]:
            if self.rois[name]:
                x0,y0,x1,y1=self.rois[name]
                c.create_rectangle(x0*400/w,y0*300/h,x1*400/w,y1*300/h,outline=color,width=2,tags='roi')
        parts=[]
        for name,label in [('whole_frame','全体'),('reference_surface','参照面'),('under_obstacle','障害物下')]:
            if name!='whole_frame' and self.rois[name] is None:continue
            roi=None if name=='whole_frame' else self.rois[name]
            m=region_metrics(packet.depth,packet.ir_left,scale,roi)
            median='未取得' if m['depth_median_m'] is None else f'{m["depth_median_m"]*100:.1f}cm'
            parts.append(f'{label}: 欠損 {m["invalid_fraction"]:.1%}／中央値 {median}／孤立0 {m["isolated_single_zero_pixels"]}')
        shift=packet.metadata['effective_settings'].get('disparity_shift',0)
        parts.append(f'適用済みShift={shift}　Depth単位={scale:g} m/保存値')
        self.metrics.set('\n'.join(parts))

    def poll(self):
        if self.closing:return
        try:
            while True:
                kind,value=self.worker.events.get_nowait()
                if kind in ('status','progress'):self.status.set(value)
                elif kind=='connected':
                    self.connected=True;self.signature=value['signature'];self.wait_new_preview=True
                    self.status.set(f'{value["info"]["name"]}\nShift範囲: {value["shift_range"]}')
                elif kind=='applied':self.status.set(f'設定を適用しました。Shift={value.get("disparity_shift")}')
                elif kind=='saved':self.status.set('保存完了:\n'+value)
                elif kind=='disconnected':
                    self.connected=False;self.packet=None;self.wait_new_preview=False
                    self.status.set('切断: '+value);messagebox.showerror('カメラ接続',value)
                elif kind=='error':
                    self.status.set(value);self.wait_new_preview=False
                    messagebox.showerror('処理できませんでした',value)
                elif kind=='command_done':self.busy=False
        except queue.Empty:pass
        try:
            packet=self.worker.previews.get_nowait()
            self.packet=packet;self.wait_new_preview=False;self.render(packet)
        except queue.Empty:pass
        self.controls()
        self.root.after(80,self.poll)

    def close(self):
        if self.closing:return
        self.closing=True;self.controls();self.worker.stop_event.set()
        self.status.set('カメラを停止し、設定を復元しています。')
        self.root.after(100,self._finish_close)

    def _finish_close(self):
        if self.worker.is_alive():self.root.after(100,self._finish_close)
        else:
            warnings=[]
            while not self.worker.events.empty():
                kind,value=self.worker.events.get_nowait()
                if kind=='restore_warning':warnings.append(value)
            if warnings:messagebox.showwarning('設定の復元', '\n'.join(warnings))
            self.root.destroy()


def main():
    parser=argparse.ArgumentParser(description='D435: button-triggered unfiltered research captures')
    parser.add_argument('--demo',action='store_true',help='Synthetic frames; no camera required')
    parser.add_argument('--serial',help='D435 serial number when multiple devices are connected')
    parser.add_argument('--width',type=int,default=640)
    parser.add_argument('--height',type=int,default=480)
    parser.add_argument('--fps',type=int,default=30)
    parser.add_argument('--output',help='Capture destination; default captures or captures_demo')
    args=parser.parse_args()
    if args.output is None:args.output=str(BASE/('captures_demo' if args.demo else 'captures'))
    root=tk.Tk();CaptureApp(root,args);root.mainloop()


if __name__=='__main__':main()
