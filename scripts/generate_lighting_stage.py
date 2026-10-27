# /// script
# requires-python = ">=3.11"
# ///
"""Generate owned HDR environment and local-light validation fixtures."""
import math
from pathlib import Path
from pxr import Gf,Sdf,Usd,UsdGeom,UsdLux,UsdShade

root=Path(__file__).resolve().parents[1]/'samples/lighting'
root.mkdir(parents=True,exist_ok=True)

def hdr(file,width,height,sample):
    def rgbe(rgb):
        peak=max(rgb)
        if peak<1e-32: return bytes(4)
        mantissa,exponent=math.frexp(peak)
        return bytes([min(255,int(v*mantissa*256/peak)) for v in rgb]+[exponent+128])
    with file.open('wb') as stream:
        stream.write(f'#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y {height} +X {width}\n'.encode())
        for y in range(height):
            pixels=[rgbe(sample((x+.5)/width,(y+.5)/height)) for x in range(width)]
            stream.write(bytes((2,2,width>>8,width&255)))
            for channel in range(4):
                data=bytes(pixel[channel] for pixel in pixels)
                for start in range(0,width,127):
                    block=data[start:start+127]
                    stream.write(bytes((len(block),))+block)

def sky(u,v):
    longitude=(.5-u)*2*math.pi
    band=.4+.6*math.sin(math.pi*v)
    # Distinct hemisphere colors and an HDR patch expose rotation, flips and
    # accidental LDR conversion. The map has no external content.
    color=(2+6*max(0,math.sin(longitude)),1+3*max(0,math.cos(longitude)),1+5*max(0,-math.sin(longitude)))
    return tuple(c*band+(24 if .12<u<.18 and .28<v<.36 else 0) for c in color)

hdr(root/'environment.hdr',256,128,sky)
hdr(root/'emitter.hdr',32,16,lambda u,v:(4 if u<.5 else .2,.3,4 if u>.5 else .2))
stage=Usd.Stage.CreateNew(str(root/'lighting.usda'))
world=UsdGeom.Xform.Define(stage,'/World'); stage.SetDefaultPrim(world.GetPrim())
UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.y); UsdGeom.SetStageMetersPerUnit(stage,1.)
def material(path,color,metal=0,rough=.4):
    mat=UsdShade.Material.Define(stage,path); shader=UsdShade.Shader.Define(stage,path+'/Surface')
    shader.CreateIdAttr('UsdPreviewSurface')
    shader.CreateInput('diffuseColor',Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    shader.CreateInput('metallic',Sdf.ValueTypeNames.Float).Set(metal)
    shader.CreateInput('roughness',Sdf.ValueTypeNames.Float).Set(rough)
    shader.CreateOutput('surface',Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(),'surface')
    return mat
paint=material('/World/Looks/Paint',(.5,.5,.5),.6,.2)
ground=material('/World/Looks/Ground',(.5,.5,.5))
cube=UsdGeom.Mesh.Define(stage,'/World/Cube')
points=[Gf.Vec3f(x,y,z) for z in (-.5,.5) for y,x in ((-.5,-.5),(-.5,.5),(.5,.5),(.5,-.5))]
faces=[(0,3,2,1),(4,5,6,7),(0,4,7,3),(1,2,6,5),(0,1,5,4),(3,7,6,2)]
cube.CreatePointsAttr(points); cube.CreateFaceVertexCountsAttr([4]*6)
cube.CreateFaceVertexIndicesAttr([i for face in faces for i in face]); cube.CreateSubdivisionSchemeAttr('none')
normals=[]
for face in faces:
    normal=Gf.Cross(points[face[1]]-points[face[0]],points[face[2]]-points[face[0]]).GetNormalized()
    normals.extend([normal]*4)
cube.CreateNormalsAttr(normals); cube.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
cube.AddTranslateOp().Set((0,.6,0)); UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(paint)
plane=UsdGeom.Mesh.Define(stage,'/World/Ground')
plane.CreatePointsAttr([(-4,0,-4),(-4,0,4),(4,0,4),(4,0,-4)])
plane.CreateFaceVertexCountsAttr([4]); plane.CreateFaceVertexIndicesAttr([0,1,2,3]); plane.CreateSubdivisionSchemeAttr('none')
UsdShade.MaterialBindingAPI.Apply(plane.GetPrim()).Bind(ground)
camera=UsdGeom.Camera.Define(stage,'/Camera')
camera.CreateHorizontalApertureAttr(36); camera.CreateVerticalApertureAttr(24); camera.CreateFocalLengthAttr(40)
camera.CreateClippingRangeAttr(Gf.Vec2f(.01,1000))
camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(3,2.4,4),Gf.Vec3d(0,.7,0),Gf.Vec3d(0,1,0)).GetInverse())
dome=UsdLux.DomeLight.Define(stage,'/World/Lights/Dome')
dome.CreateTextureFileAttr('environment.hdr'); dome.CreateTextureFormatAttr('latlong'); dome.CreateExposureAttr(-2)
dome.AddRotateYOp().Set(0)
for index in range(24):
    light=UsdLux.SphereLight.Define(stage,f'/World/Lights/Point{index:02}')
    angle=index*2*math.pi/24
    light.AddTranslateOp().Set((math.cos(angle)*2,1.5+index%3*.4,math.sin(angle)*2))
    light.CreateRadiusAttr(.08); light.CreateIntensityAttr(50.)
    light.CreateColorAttr(Gf.Vec3f(*((1,.25,.1) if index%3==0 else (.1,.3,1) if index%3==1 else (.3,1,.2))))
def orient(light,eye):
    light.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye),Gf.Vec3d(0,.5,0),Gf.Vec3d(0,1,0)).GetInverse())
rect=UsdLux.RectLight.Define(stage,'/World/Lights/Rect'); orient(rect,(-2,2.5,1))
rect.CreateWidthAttr(.8); rect.CreateHeightAttr(1.2); rect.CreateIntensityAttr(20)
rect.CreateTextureFileAttr('emitter.hdr')
disk=UsdLux.DiskLight.Define(stage,'/World/Lights/Disk'); orient(disk,(1,3,-1))
disk.CreateRadiusAttr(.4); disk.CreateIntensityAttr(8)
tube=UsdLux.CylinderLight.Define(stage,'/World/Lights/Tube'); tube.AddTranslateOp().Set((0,2,-2))
tube.CreateRadiusAttr(.04); tube.CreateLengthAttr(1.5); tube.CreateIntensityAttr(20)
spot=UsdLux.SphereLight.Define(stage,'/World/Lights/Spot'); orient(spot,(1,2,2))
spot.CreateRadiusAttr(.1); spot.CreateIntensityAttr(40)
shaping=UsdLux.ShapingAPI.Apply(spot.GetPrim()); shaping.CreateShapingConeAngleAttr(30); shaping.CreateShapingConeSoftnessAttr(.3)
sun=UsdLux.DistantLight.Define(stage,'/World/Lights/Sun'); sun.AddRotateXYZOp().Set((-35,20,0))
sun.CreateIntensityAttr(1); sun.CreateAngleAttr(1.)
stage.GetRootLayer().Save()
file=root/'lighting.usda'
file.write_text(file.read_text(encoding='utf-8').rstrip()+'\n',encoding='utf-8',newline='\n')
print(root/'lighting.usda')
