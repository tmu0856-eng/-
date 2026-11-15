"""将固定版本的 BGE 向量或精排权重下载至本地忽略目录。"""
import json
import hashlib
import argparse
import time
import httpx
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path

os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
os.environ.setdefault('HF_HUB_ETAG_TIMEOUT', '20')
os.environ.setdefault('HF_HUB_DOWNLOAD_TIMEOUT', '60')
from huggingface_hub import HfApi, snapshot_download

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--model', choices=['BAAI/bge-m3','BAAI/bge-reranker-v2-m3'], default='BAAI/bge-m3')
parser.add_argument('--download-endpoint', default=None, help='Optional download mirror; revision and SHA-256 still come from the official API.')
parser.add_argument('--connections', type=int, choices=range(1, 17), default=6, help='Concurrent ranged transfers for a mirror (1-16).')
args = parser.parse_args()


def parallel_weight_download(item, revision, path):
    """通过受限分段传输避免单个大文件连接过慢，并支持按分片续传。"""
    shard_size = 16 * 1024 * 1024
    directory = path.parent / '.parts' / path.name
    directory.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + '.part')
    ranges = [(start, min(item.size-1, start+shard_size-1)) for start in range(0, item.size, shard_size)]
    if partial.exists():
        available = partial.stat().st_size
        with partial.open('rb') as source:
            for start, end in ranges:
                if end >= available:
                    break
                shard = directory / str(start)
                if not shard.exists():
                    source.seek(start)
                    shard.write_bytes(source.read(end-start+1))

    def transfer(bounds):
        start, end = bounds
        shard = directory / str(start)
        if shard.exists() and shard.stat().st_size == end-start+1:
            return
        for attempt in range(3):
            try:
                url = args.download_endpoint.rstrip('/') + '/' + args.model + '/resolve/' + revision + '/' + item.rfilename + '?download=true'
                with httpx.Client(trust_env=False, follow_redirects=True, timeout=httpx.Timeout(45, connect=15)) as client:
                    with client.stream('GET', url, headers={'Range': f'bytes={start}-{end}'}) as response:
                        response.raise_for_status()
                        if response.headers.get('content-range') != f'bytes {start}-{end}/{item.size}':
                            raise ValueError('Unexpected byte range')
                        with shard.open('wb') as handle:
                            for block in response.iter_bytes(1024*1024):
                                handle.write(block)
                if shard.stat().st_size != end-start+1:
                    raise ValueError('Incomplete shard')
                return
            except (httpx.HTTPError, ValueError):
                if attempt == 2:
                    raise RuntimeError('Model range download failed at byte ' + str(start)) from None
                time.sleep(2)
    print(json.dumps({'file':item.rfilename,'expected_bytes':item.size,'connections':args.connections}), flush=True)
    with ThreadPoolExecutor(max_workers=args.connections) as pool:
        for count, future in enumerate(as_completed([pool.submit(transfer, bounds) for bounds in ranges]), 1):
            future.result()
            if count % 8 == 0 or count == len(ranges):
                print(json.dumps({'downloaded_shards':count,'total_shards':len(ranges)}), flush=True)
    with partial.open('wb') as destination:
        for start, _ in ranges:
            with (directory / str(start)).open('rb') as source:
                shutil.copyfileobj(source, destination)
    partial.replace(path)
target = ROOT / '.local/models' / args.model.split('/')[-1]
for attempt in range(3):
    try:
        info = HfApi().model_info(args.model, files_metadata=True, timeout=20)
        break
    except Exception:
        if attempt == 2:
            raise
        time.sleep(2)
print('Downloading ' + args.model + ' revision ' + info.sha, flush=True)
weight_name = 'model.safetensors' if any(f.rfilename == 'model.safetensors' for f in info.siblings) else 'pytorch_model.bin'
if not args.download_endpoint:
    snapshot_download(args.model, revision=info.sha, local_dir=target,
                      allow_patterns=['*.json', weight_name, 'sentencepiece.bpe.model'],
                      ignore_patterns=['onnx/*'], max_workers=3)
else:
    # 部分镜像缺少 SDK 所需的 HEAD 元数据，因此通过 HTTP 下载公开数据，
    # 随后核对官方大小和哈希，不从镜像加载代码。
    for item in info.siblings:
        name = item.rfilename
        if name.startswith('onnx/') or not (name.endswith('.json') or name in (weight_name, 'sentencepiece.bpe.model')):
            continue
        path = target / name
        if path.is_file() and path.stat().st_size == item.size:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if item.size > 64 * 1024 * 1024:
            parallel_weight_download(item, info.sha, path)
            continue
        partial = path.with_name(path.name + '.part')
        for attempt in range(4):
            offset = partial.stat().st_size if partial.exists() else 0
            try:
                with httpx.Client(trust_env=False, follow_redirects=True, timeout=httpx.Timeout(60, connect=15)) as client:
                    url = args.download_endpoint.rstrip('/') + '/' + args.model + '/resolve/' + info.sha + '/' + name + '?download=true'
                    with client.stream('GET', url, headers={'Range': f'bytes={offset}-'}) as response:
                        response.raise_for_status()
                        if offset and response.status_code != 206:
                            offset = 0
                        if response.status_code == 206 and not response.headers.get('content-range', '').startswith(f'bytes {offset}-'):
                            raise ValueError('Invalid download byte range')
                        print(json.dumps({'file': name, 'resume_bytes': offset, 'expected_bytes': item.size}), flush=True)
                        last_report = time.monotonic()
                        with partial.open('ab' if offset else 'wb') as handle:
                            for block in response.iter_bytes(1024*1024):
                                handle.write(block)
                                if time.monotonic()-last_report > 15:
                                    print(json.dumps({'downloaded_MB': round(handle.tell()/1e6)}), flush=True)
                                    last_report = time.monotonic()
                if partial.stat().st_size != item.size:
                    raise ValueError('Incomplete download')
                partial.replace(path)
                break
            except (httpx.HTTPError, ValueError):
                if attempt == 3:
                    raise RuntimeError('Model download failed after bounded retries: ' + name) from None
                time.sleep(2)
manifest = {'model': args.model, 'revision': info.sha, 'source': 'https://huggingface.co/' + args.model,
            'files': {f.rfilename: f.lfs.sha256 for f in info.siblings if f.lfs and (target / f.rfilename).is_file()}}
for name, expected in manifest['files'].items():
    digest = hashlib.sha256()
    with (target / name).open('rb') as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError('Downloaded model checksum mismatch: ' + name)
for item in info.siblings:
    path = target / item.rfilename
    if path.is_file() and not item.lfs:
        blob = hashlib.sha1(b'blob ' + str(path.stat().st_size).encode() + b'\0' + path.read_bytes()).hexdigest()
        if blob != item.blob_id:
            raise ValueError('Downloaded model config checksum mismatch: ' + item.rfilename)
if not any((target / name).is_file() for name in ('model.safetensors', 'pytorch_model.bin')):
    raise ValueError('No dense model weights downloaded')
(target / 'researchdock-model.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
for name in manifest['files']:
    shard_dir = target / '.parts' / name
    if shard_dir.is_dir():
        for shard in shard_dir.iterdir():
            if shard.is_file() and shard.name.isdigit():
                shard.unlink()
        shard_dir.rmdir()
print(json.dumps({'path': str(target), 'revision': info.sha}), flush=True)
