"""Materialize embedded Houdini images without changing their source assets."""
import hashlib
from pathlib import Path


def embedded_image(uri, directory, reader=None):
    if not uri.startswith('opdef:') or '?' not in uri:
        raise ValueError('Expected an opdef: image resource: ' + uri)
    extension = Path(uri.rsplit('?', 1)[1]).suffix.lower()
    if extension not in {'.jpg', '.jpeg', '.png', '.exr', '.tif', '.tiff', '.bmp', '.hdr', '.tga'}:
        raise ValueError('Unsupported embedded image format: ' + uri)
    if reader is None:
        import hou
        reader = hou.readBinaryFile
    content = reader(uri)
    if not content:
        raise ValueError('Empty embedded image: ' + uri)
    digest = hashlib.sha256(content).hexdigest()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    file = directory / (digest + extension)
    if not file.exists():
        pending = file.with_suffix(extension + '.tmp')
        pending.write_bytes(content)
        pending.replace(file)
    elif hashlib.sha256(file.read_bytes()).hexdigest() != digest:
        raise ValueError('Embedded image cache is corrupt: ' + str(file))
    return {'uri': uri, 'path': str(file.resolve()), 'sha256': digest}
