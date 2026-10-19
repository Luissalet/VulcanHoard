import argparse
import os
import threading
import webbrowser
import uvicorn
from vulcan.hoard_link.tokens import write_url
from .api import create_app

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=int(os.environ.get('DAEDALUS_PORT','8830')))
    parser.add_argument('--data-dir')
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    os.environ['DAEDALUS_PORT']=str(args.port)
    app=create_app(args.data_dir)
    write_url(app.state.config.data/'url',f'http://127.0.0.1:{args.port}')
    if not args.no_browser:
        threading.Timer(1.5,lambda:webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='info')

if __name__=='__main__':
    main()
