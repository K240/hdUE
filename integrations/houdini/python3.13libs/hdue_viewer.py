"""Houdini-owned image window for the active Solaris hdUE color AOV."""
import math
from pathlib import Path

import hou
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets


class Canvas(QtWidgets.QWidget):
    def __init__(self,parent):
        super().__init__(parent)
        self.image=QtGui.QImage()
        self.setMinimumSize(480,270)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.position=None

    def paintEvent(self,event):
        painter=QtGui.QPainter(self)
        painter.fillRect(self.rect(),QtGui.QColor('#181818'))
        if not self.image.isNull():
            size=self.image.size().scaled(self.size(),QtCore.Qt.KeepAspectRatio)
            target=QtCore.QRect(QtCore.QPoint((self.width()-size.width())//2,(self.height()-size.height())//2),size)
            painter.drawImage(target,self.image)

    def navigate(self,action,dx,dy):
        import hdue_houdini as integration
        viewport=integration.scene_viewer().curViewport()
        camera=viewport.defaultCamera()
        position=hou.Vector3(camera.translation())
        if action=='orbit':
            camera.setRotation(hou.hmath.buildRotate(hou.Vector3(-dy*.3,0,0)).extractRotationMatrix3()*
                               camera.rotation()*hou.hmath.buildRotate(hou.Vector3(0,-dx*.3,0)).extractRotationMatrix3())
        elif action=='pan':
            scale=max(abs(position[2]),.01)/max(self.height(),1)
            camera.setTranslation(position+hou.Vector3(-dx*scale,dy*scale,0))
        elif action=='dolly':
            camera.setTranslation(hou.Vector3(position[0],position[1],max(.001,position[2]*math.exp(dy*.008))))
        else: raise ValueError(action)
        viewport.draw()

    def mousePressEvent(self,event):
        self.setFocus(); self.position=event.position()

    def mouseReleaseEvent(self,event):
        self.position=None

    def mouseMoveEvent(self,event):
        if self.position is None: return
        delta=event.position()-self.position; self.position=event.position()
        buttons=event.buttons()
        action='pan' if buttons & QtCore.Qt.MiddleButton else 'dolly' if buttons & QtCore.Qt.RightButton else 'orbit'
        self.navigate(action,delta.x(),delta.y())

    def wheelEvent(self,event):
        self.navigate('dolly',0,-event.angleDelta().y()/12)
        event.accept()


class Viewer(QtWidgets.QDialog):
    def __init__(self,host):
        super().__init__(hou.qt.mainWindow())
        self.host=host
        self.setWindowTitle('hdUE — Houdini 22.0.466')
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.resize(1000,680)
        layout=QtWidgets.QVBoxLayout(self)
        controls=QtWidgets.QHBoxLayout()
        self.mode=QtWidgets.QComboBox(); self.mode.addItems(['Lumen','Path Tracer'])
        self.mode.currentIndexChanged.connect(self.change_mode)
        self.samples=QtWidgets.QSpinBox(); self.samples.setRange(1,4096); self.samples.setValue(64)
        self.samples.valueChanged.connect(self.change_samples)
        frame=QtWidgets.QPushButton('Frame all'); frame.clicked.connect(self.frame_all)
        save=QtWidgets.QPushButton('Save HDR…'); save.clicked.connect(self.save_hdr)
        for widget in (self.mode,QtWidgets.QLabel('Samples'),self.samples,frame,save): controls.addWidget(widget)
        controls.addStretch(); layout.addLayout(controls)
        self.canvas=Canvas(self); layout.addWidget(self.canvas,1)
        self.status=QtWidgets.QLabel('Waiting for a Solaris hdUE image…'); layout.addWidget(self.status)
        layout.addWidget(QtWidgets.QLabel('Drag: left = orbit · middle = pan · right / wheel = dolly'))
        self.last_frame=None
        self.linear=None
        self.updates=0

    def change_mode(self,index):
        import hdue_houdini as integration
        integration.set_mode('pathtraced' if index else 'realtime')

    def change_samples(self,value):
        import hdue_houdini as integration
        node=integration.current_settings_node()
        if node:
            node.parm('hdue_samples').set(value); node.cook(force=True)
            integration.scene_viewer().curViewport().draw()

    def frame_all(self):
        import hdefereval,hdue_houdini as integration
        hdefereval.execute_deferred(integration.scene_viewer().curViewport().frameAll)

    def present(self,status,frame,pixels):
        # copy() detaches QImage from the temporary NumPy allocation. The backend
        # owns neither this array nor the displayed bytes across the C boundary.
        linear=np.ctypeslib.as_array(pixels).reshape(frame.height,frame.width,4).copy()
        rgb=np.clip(np.nan_to_num(linear[:,:,:3],nan=0,posinf=1,neginf=0),0,1)
        srgb=np.where(rgb<=.0031308,12.92*rgb,1.055*np.power(rgb,1/2.4)-.055)
        display=np.ascontiguousarray(np.rint(srgb*255).astype(np.uint8))
        self.canvas.image=QtGui.QImage(display.data,frame.width,frame.height,display.strides[0],QtGui.QImage.Format_RGB888).copy()
        self.canvas.update()
        self.linear=linear; self.last_frame=(status['session'],frame.generation,frame.frame_id); self.updates+=1
        name='Path Tracer' if status['mode'] else 'Lumen'
        progress=f'{frame.completed_samples} samples' if status['mode'] else 'ready' if frame.converged else 'warming up'
        self.status.setText(f'{name} · {frame.width} × {frame.height} · {progress} · {frame.request_to_frame_ms:.1f} ms')
        for widget,value in ((self.mode,status['mode']),):
            blocker=QtCore.QSignalBlocker(widget); widget.setCurrentIndex(value); del blocker

    def write_hdr(self,path):
        if self.linear is None: raise RuntimeError('No completed image')
        height,width,_=self.linear.shape
        with Path(path).open('wb') as output:
            output.write(f'PF\n{width} {height}\n-1.0\n'.encode('ascii'))
            output.write(np.ascontiguousarray(self.linear[::-1,:,:3],dtype='<f4').tobytes())

    def save_hdr(self):
        if self.linear is None: return
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,'Save linear HDR image','','Portable float image (*.pfm)')
        if path: self.write_hdr(path)
