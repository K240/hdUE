# /// script
# requires-python = ">=3.11,<3.12"
# ///
"""Generate owned 4 x 4K color/normal UDIM data with fine checker detail."""
import argparse
from pathlib import Path
import struct
import zlib
from generate_normal_udim_stage import generate

def image(path,size,color,detail):
    # Build repeated rows without a per-pixel Python loop over a 4K image.
    def row(y):
        blocks=[]
        for x in range(0,size,8):
            factor=1 if not detail or ((x//8+y//8)&1)==0 else 0.45
            blocks.append(bytes(round(c*factor) for c in color)*min(8,size-x))
        return b"\0"+b"".join(blocks)
    rows=[row(y) for y in range(16)]
    data=b"".join(rows[y%16] for y in range(size))
    def chunk(tag,value):
        return struct.pack(">I",len(value))+tag+value+struct.pack(">I",zlib.crc32(tag+value))
    path.write_bytes(b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",size,size,8,2,0,0,0))+chunk(b"IDAT",zlib.compress(data))+chunk(b"IEND",b""))

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("output",type=Path); parser.add_argument("--size",type=int,default=4096)
    args=parser.parse_args()
    if args.size<1024 or args.size&(args.size-1): parser.error("size must be a power of two >= 1024")
    generate(args.output)
    for index,color in ((1001,(210,25,25)),(1002,(25,210,25)),(1011,(25,25,210)),(1012,(210,190,25))):
        image(args.output/f"color.{index}.png",args.size,color,True)
    for index,color in ((1001,(128,128,255)),(1002,(204,128,230)),(1011,(128,204,230)),(1012,(76,179,233))):
        image(args.output/f"normal_tiles.{index}.png",args.size,color,False)
    print(args.output/"normal_udim.usda")
if __name__=="__main__": main()
