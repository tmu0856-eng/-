"""Run the local workspace: python run.py"""
import os
from pathlib import Path
from dotenv import load_dotenv
import uvicorn

if __name__ == '__main__':
    load_dotenv(Path(__file__).parent / '.env')
    uvicorn.run('backend.app:app', host=os.getenv('APP_HOST', '127.0.0.1'), port=int(os.getenv('APP_PORT', '8765')))
