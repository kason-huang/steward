import base64
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request

import google_crc32c

root = Path(__file__).resolve().parent
prefix = 'checkpoints/pi05_libero/'
query = urllib.parse.urlencode({'prefix': prefix, 'fields': 'items(name,size,md5Hash,crc32c,generation),nextPageToken'})
with urllib.request.urlopen('https://storage.googleapis.com/storage/v1/b/openpi-assets/o?' + query, timeout=30) as response:
    inventory = json.load(response)
assert not inventory.get('nextPageToken')
(root / 'evidence/gcs-inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')

def verify(path, item):
    assert path.stat().st_size == int(item["size"]), path
    crc32c = google_crc32c.Checksum()
    sha256 = hashlib.sha256()
    md5 = hashlib.md5() if "md5Hash" in item else None
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            crc32c.update(block)
            sha256.update(block)
            if md5 is not None:
                md5.update(block)
    assert base64.b64encode(crc32c.digest()).decode() == item["crc32c"], path
    if md5 is not None:
        assert base64.b64encode(md5.digest()).decode() == item["md5Hash"], path
    return sha256.hexdigest()

def download(item):
    path = root / item['name']
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.part')
    if not path.exists():
        url = 'https://storage.googleapis.com/openpi-assets/' + urllib.parse.quote(item['name']) + '?generation=' + item['generation']
        if not temporary.exists() or temporary.stat().st_size != int(item['size']):
            subprocess.run(['curl', '-fsSL', '--connect-timeout', '20', '--speed-limit', '1024', '--speed-time', '90', '--retry', '8', '-C', '-', '-o', str(temporary), url], check=True)
        checksum = verify(temporary, item)
        temporary.rename(path)
    else:
        checksum = verify(path, item)
    print("verified", path.relative_to(root), flush=True)
    return f"{checksum}  {path.relative_to(root)}"


with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    manifest = list(pool.map(download, inventory['items']))
(root / 'evidence/checkpoint.sha256').write_text('\n'.join(manifest) + '\n')
