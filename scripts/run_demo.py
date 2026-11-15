"""启动独立评审服务，评审数据与主文献库分开保存。"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

if __name__=='__main__':
    config=ROOT/'.env.demo'
    if not config.exists():
        raise SystemExit('Run python scripts/prepare_demo.py first.')
    load_dotenv(config,override=True)
    if not os.getenv('DEMO_PASSWORD'):
        raise SystemExit('DEMO_PASSWORD must be configured before starting the reviewer service.')
    os.environ['DEMO_REQUIRE_LOGIN']='1'
    os.environ.setdefault('APP_HOST','127.0.0.1')
    os.environ.setdefault('APP_PORT','8767')
    os.environ.setdefault('RESEARCHDOCK_DATA',str(ROOT/'data/demo-public'))
    sys.path.insert(0,str(ROOT))
    import uvicorn
    uvicorn.run('backend.app:app',host=os.environ['APP_HOST'],port=int(os.environ['APP_PORT']))
