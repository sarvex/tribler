# Written by Egbert Bouman

'''
The Libtorrent package contains code to manage the torrent library.
'''

def checkHandleAndSynchronize(default=None):
    def wrap(f):
        def invoke_func(*args, **kwargs):
            download = args[0]
            with download.dllock:
                if download.handle and download.handle.is_valid():
                    return f(*args, **kwargs)
            return default
        return invoke_func
    return wrap


def waitForHandleAndSynchronize(default=None):
    def wrap(f):
        def invoke_func(*args, **kwargs):
            download = args[0]
            with download.dllock:
                if download.handle and download.handle.is_valid():
                    return f(*args, **kwargs)
                lambda_f = lambda a = args, kwa = kwargs: invoke_func(*a, **kwa)
                download.session.lm.threadpool.add_task(lambda_f, 1)
                return default

        return invoke_func

    return wrap
