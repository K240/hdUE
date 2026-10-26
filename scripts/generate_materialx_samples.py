# /// script
# requires-python = ">=3.11"
# ///
"""Create owned MaterialX fixtures for the real UE import/render checks."""
from pathlib import Path
import struct
import zlib
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent / "samples/materialx"
ROOT.mkdir(parents=True, exist_ok=True)
(ROOT / "textures").mkdir(exist_ok=True)


def png(name, colors):
    size = 32
    raw = b"".join(b"\x00" + bytes(v for x in range(size) for v in colors[((x//8)+(y//8)) % 2]) for y in range(size))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag+data))
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    (ROOT / "textures" / name).write_bytes(data + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


png("warm.png", ((220, 60, 25), (230, 190, 70)))
png("cool.png", ((25, 70, 220), (40, 190, 180)))
for family, node_type in (("SS", "standard_surface"), ("OP", "open_pbr_surface")):
    doc = ET.Element("materialx", version="1.39", colorspace="lin_rec709")
    for kind in ("Metal", "Coat", "Anisotropy", "Glass", "Subsurface"):
        shader = ET.SubElement(doc, node_type, name=f"{family}_{kind}_surface", type="surfaceshader")
        def field(name, value, value_type="float", **attributes):
            if isinstance(value, tuple):
                value_type, value = "color3", ", ".join(map(str, value))
            return ET.SubElement(shader, "input", name=name, type=value_type, value=str(value), **attributes)
        # Keep the base lobe active when testing the transmission blend.
        field("base" if family == "SS" else "base_weight", 0.9)
        field("base_color", (0.7, 0.27, 0.07) if kind in ("Metal", "Anisotropy") else (0.35, 0.08, 0.04))
        field("metalness" if family == "SS" else "base_metalness", 0.95 if kind in ("Metal", "Anisotropy") else 0.02)
        field("specular_roughness", 0.07 if kind == "Glass" else 0.32)
        if kind == "Coat":
            field("coat" if family == "SS" else "coat_weight", 0.9)
            field("coat_roughness", 0.07)
            image = ET.SubElement(doc, "image", name=f"{family}_checker", type="color3")
            ET.SubElement(image, "input", name="file", type="filename", value="textures/warm.png" if family == "SS" else "textures/cool.png", colorspace="srgb_texture")
            base = shader.find("./input[@name='base_color']")
            del base.attrib["value"]
            base.set("nodename", image.attrib["name"])
        if kind == "Anisotropy":
            field("specular_anisotropy" if family == "SS" else "specular_roughness_anisotropy", 0.8)
        if kind == "Glass":
            field("transmission" if family == "SS" else "transmission_weight", 0.98)
            field("transmission_color", (0.45, 0.8, 0.95))
            field("transmission_depth", 20)
            field("specular_IOR" if family == "SS" else "specular_ior", 1.45)
        if kind == "Subsurface":
            field("subsurface" if family == "SS" else "subsurface_weight", 0.85)
            field("subsurface_color", (0.85, 0.25, 0.08))
            field("subsurface_radius", (1.0, 0.4, 0.15) if family == "SS" else 3.0)
            if family == "SS":
                field("subsurface_scale", 3.0)
        material = ET.SubElement(doc, "surfacematerial", name=f"{family}_{kind}", type="material")
        ET.SubElement(material, "input", name="surfaceshader", type="surfaceshader", nodename=shader.attrib["name"])
    ET.indent(doc, space="  ")
    ET.ElementTree(doc).write(ROOT / f"{node_type}.mtlx", encoding="utf-8", xml_declaration=True)
print(ROOT)
