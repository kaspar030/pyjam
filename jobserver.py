# python's subprocess creation speed depends on the main processes memory consumption
# (due to mmap'ing magic). So with a million targets, pyjam's overhead for calling "true"
# goes from 3ms to 20ms. Using this jobserver class, the speed stays constant at 3.7ms.
# (measured on my thinkpad x220 core i5).

from multiprocessing import Process, Queue
from subprocess import Popen, PIPE, STDOUT
import signal
from collections import deque
import os

class JobHandle(object):
    def __init__(s, queue, pid, pool, server):
        s.queue = queue
        s.pid = pid
        s.pool = pool
        s.server = server

    def wait(s):
        res = s.queue.get()
        s.pool.append(s.server)
        return res

    def kill(s, signal=signal.SIGKILL):
        os.kill(-s.pid, signal)
        return s.wait()

    def killpg(s, signal=signal.SIGKILL):
        os.killpg(os.getpgid(s.pid), signal)
        return s.wait()

class JobServer(object):
    def __init__(s, pool):
        s.inQueue = Queue()
        s.outQueue = Queue()
        s.cmdHostProcess = Process(target=JobServer.cmdloop, args=(s, s.inQueue, s.outQueue, pool), daemon=True)
        s.cmdHostProcess.start()

    def cmdloop(s, inQueue, outQueue, pool):
        while True:
            args, kwargs = inQueue.get()
            kwargs["stderr"] = STDOUT
            kwargs["stdout"] = PIPE
            process = Popen(*args, **kwargs)
            outQueue.put(process.pid)
            output = ""
            with process:
                output += str(process.stdout.read())
            outQueue.put((output, process.returncode))

    def runcmd(s, *args, **kwargs):
        s.inQueue.put((args, kwargs))
        pid = s.outQueue.get()
        return s.outQueue, pid

    def killCmdHostProcess(s):
        s.cmdHostProcess.terminate()

class JobServerPool(object):
    def __init__(s, n):
        s.pool = deque()
        for i in range(0, n):
            s.pool.append(JobServer(s.pool))

    def destroy(s):
        while s.pool:
            s.pool.pop().killCmdHostProcess()

    def runcmd(s, *args, **kwargs):
        server = s.pool.pop()
        queue, pid = server.runcmd(*args, **kwargs)
        return JobHandle(queue, pid, s.pool, server)
