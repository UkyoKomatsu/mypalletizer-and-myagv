"""Lightweight viewer for arrays.npz, with a display-only point subsample."""
from __future__ import annotations
import argparse
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk
import numpy as np
from PIL import Image, ImageTk


class CloudViewer:
    def __init__(self,root,path):
        with np.load(path,allow_pickle=False) as data:
            points=data['xyz_m'].reshape(-1,3)
            good=data['valid_mask'].reshape(-1)&np.isfinite(points).all(axis=1)
            points=points[good]
        if not len(points):raise ValueError('No valid points in this capture.')
        self.total=len(points)
        step=max(1,int(np.ceil(len(points)/40000)))
        self.points=points[::step].copy()
        self.points-=np.median(self.points,axis=0)
        self.root=root;self.yaw=0.;self.pitch=0.;self.zoom=1.
        self.extent=max(float(np.percentile(np.linalg.norm(self.points,axis=1),95))*2,0.02)
        self.canvas=tk.Canvas(root,width=900,height=650,bg='#101820',highlightthickness=0)
        self.canvas.pack(fill='both',expand=True)
        ttk.Label(root,text=f'{Path(path).name}: {self.total:,} points (metres). '
                  f'Display: {len(self.points):,}. Drag to rotate, wheel to zoom. Files are unchanged.').pack()
        self.canvas.bind('<ButtonPress-1>',self.start_drag)
        self.canvas.bind('<B1-Motion>',self.drag)
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<Button-4>',lambda e:self.wheel(e,1))
        self.canvas.bind('<Button-5>',lambda e:self.wheel(e,-1))
        self.canvas.bind('<Configure>',lambda e:self.render())
        root.title('Saved point cloud - orthographic view')
        root.after(20,self.render)

    def start_drag(self,event):self.last=(event.x,event.y)

    def drag(self,event):
        if not hasattr(self,'last'):return
        self.yaw+=(event.x-self.last[0])*0.01
        self.pitch+=(event.y-self.last[1])*0.01
        self.last=(event.x,event.y);self.render()

    def wheel(self,event,direction=None):
        direction=direction if direction is not None else np.sign(event.delta)
        self.zoom=float(np.clip(self.zoom*(1.15 if direction>0 else 1/1.15),0.05,50))
        self.render()

    def render(self):
        width=max(self.canvas.winfo_width(),10);height=max(self.canvas.winfo_height(),10)
        cy,sy=np.cos(self.yaw),np.sin(self.yaw)
        cp,sp=np.cos(self.pitch),np.sin(self.pitch)
        ry=np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]])
        rx=np.array([[1,0,0],[0,cp,-sp],[0,sp,cp]])
        points=self.points@(rx@ry).T
        factor=min(width,height)*0.85/self.extent*self.zoom
        x=np.rint(points[:,0]*factor+width/2).astype(int)
        y=np.rint(points[:,1]*factor+height/2).astype(int)
        keep=(x>=0)&(x<width)&(y>=0)&(y<height)
        x,y,z=x[keep],y[keep],points[keep,2]
        depth_buffer=np.full(width*height,np.inf,dtype=float)
        indices=y*width+x
        np.minimum.at(depth_buffer,indices,z)
        visible=z<=depth_buffer[indices]
        t=np.clip((self.points[keep,2]/self.extent)+0.5,0,1)
        colors=np.stack((60+180*t,210-80*t,240-170*t),axis=1).astype(np.uint8)
        raster=np.full((height,width,3),(16,24,32),dtype=np.uint8)
        raster[y[visible],x[visible]]=colors[visible]
        self.photo=ImageTk.PhotoImage(Image.fromarray(raster))
        self.canvas.delete('all');self.canvas.create_image(0,0,anchor='nw',image=self.photo)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path',nargs='?',type=Path,help='arrays.npz file')
    args=parser.parse_args()
    root=tk.Tk();root.withdraw()
    path=args.path or filedialog.askopenfilename(title='保存したarrays.npzを選択',filetypes=[('Point arrays','*.npz')])
    if not path:root.destroy();return
    CloudViewer(root,path);root.deiconify();root.mainloop()


if __name__=='__main__':main()
