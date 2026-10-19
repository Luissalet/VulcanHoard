import argparse
import os
import uvicorn
from .api import create_app

parser=argparse.ArgumentParser()
parser.add_argument('--port',type=int,default=int(os.getenv('HERON_PORT','5204')))
parser.add_argument('--data-dir',default=None)
parser.add_argument('--no-browser',action='store_true')
args=parser.parse_args()
# Family Hub owns launch/navigation; no browser or GPU is started implicitly.
uvicorn.run(create_app(args.data_dir,args.port),host='127.0.0.1',port=args.port,log_level='warning')
