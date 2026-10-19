import threading

from vulcan.folder_listings import DraftWorker
from vulcan import watcher as watcher_module


def test_draft_stop_keeps_completed_work_and_does_not_start_the_next_target():
    entered,release,finished=threading.Event(),threading.Event(),threading.Event()
    targets=[]
    class Store:
        def draft_one(self,root_id,rel,link,overwrite):
            targets.append((root_id,rel))
            entered.set()
            release.wait()
            return {'ok':True}
    worker=DraftWorker(Store(),lambda:None)
    worker.start([(1,'first'),(1,'second')],False)
    assert entered.wait(2)
    stopper=threading.Thread(target=lambda:(worker.stop(),finished.set()))
    stopper.start()
    try:
        assert worker._stop.wait(2)
        assert not finished.is_set()
        release.set()
        stopper.join(2)
        assert finished.is_set() and not worker.running()
        assert targets==[(1,'first')]
        assert worker.status()['done']==1 and worker.status()['phase']=='cancelled'
    finally:
        release.set()
        stopper.join()


def test_watcher_stop_joins_a_callback_already_removed_from_pending_timers(monkeypatch):
    entered,release,joined=threading.Event(),threading.Event(),threading.Event()
    monkeypatch.setattr(watcher_module,'DEBOUNCE_S',0)
    def enqueue(identity):
        entered.set()
        release.wait()
    watcher=watcher_module.Watcher(enqueue)
    watcher._bump(1)
    assert entered.wait(2)
    with watcher._lock:
        timer=next(iter(watcher._timer_threads))
        assert not watcher._timers
    original_join=timer.join
    def join(*args,**kwargs):
        joined.set()
        return original_join(*args,**kwargs)
    monkeypatch.setattr(timer,'join',join)
    stopper=threading.Thread(target=watcher.stop)
    stopper.start()
    try:
        assert joined.wait(2)
        assert stopper.is_alive()
        release.set()
        stopper.join(2)
        assert not stopper.is_alive() and not timer.is_alive()
        assert not watcher._timer_threads
    finally:
        release.set()
        stopper.join()
