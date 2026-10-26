"""Filesystem UDIM discovery shared by preparation and runtime validation."""
from pathlib import Path
import re


def udim_tiles(pattern):
    path=Path(pattern)
    # Matches UE 5.8's BaseName.####.ext / BaseName_####.ext naming rule.
    if path.name.count("<UDIM>")!=1 or not re.fullmatch(r".+[._]<UDIM>\.[^.]+",path.name):
        raise ValueError(f"UDIM needs BaseName.<UDIM>.ext or BaseName_<UDIM>.ext: {pattern}")
    prefix,suffix=path.name.split("<UDIM>")
    expression=re.compile(re.escape(prefix)+r"(\d{4})"+re.escape(suffix),re.IGNORECASE)
    tiles={}
    if path.parent.is_dir():
        for file in path.parent.iterdir():
            match=expression.fullmatch(file.name)
            if not match or not file.is_file():
                continue
            index=int(match[1])
            if not 1001<=index<=1910:
                raise ValueError(f"UDIM index is outside UE's 1001..1910 range: {file}")
            if index in tiles:
                raise ValueError(f"Duplicate UDIM index {index}: {pattern}")
            tiles[index]=str(file.resolve())
    if not tiles:
        raise ValueError(f"UDIM has no tiles: {pattern}")
    # UE discards offsets when importing a single UDIM image as ordinary 2D.
    if len(tiles)==1 and 1001 not in tiles:
        raise ValueError(f"A single off-origin UDIM tile loses its offset in UE; provide another tile: {pattern}")
    return dict(sorted(tiles.items()))


def validate_texture_sets(sets):
    for record in sets:
        actual={str(k):v for k,v in udim_tiles(record["pattern"]).items()}
        if actual!=record["tiles"]:
            raise ValueError(f"UDIM tile membership changed; prepare/import/cook again: {record['pattern']}")
