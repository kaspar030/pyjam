from multiprocessing import Process, Queue
from subprocess import check_call, CalledProcessError
from collections import deque
import os

# python's subprocess creation speed depends on the main processes memory consumption
# (due to mmap'ing magic). So with a million targets, pyjam's overhead for calling "true"
# goes from 3ms to 20ms. Using this jobserver class, the speed stays constant at 3.7ms.
# (measured on my thinkpad x220 core i5).

class JobServer(object):
    def __init__(s):
        s.inQueue = Queue()
        s.outQueue = Queue()
        s.cmdHostProcess = Process(target=JobServer.cmdloop, args=(s, s.inQueue, s.outQueue))
        s.cmdHostProcess.start()

    def cmdloop(s, inQueue, outQueue):
        while True:
            (command, env) = inQueue.get()
            try:
                output = check_call(command, env=env, shell=True)
                result = 0
            except CalledProcessError as e:
                output = None
                result = e.returncode

            outQueue.put((output, result))

    def callCommand(s, command, env):
        s.inQueue.put((command, env))
        return s.outQueue.get()

    def killCmdHostProcess(s):
        s.cmdHostProcess.terminate()

class JobServerPool(object):
    def __init__(s, n):
        s.pool = deque()

        for i in range(0, n):
            s.pool.append(JobServer())

    def destroy(s):
        while s.pool:
            s.pool.pop().killCmdHostProcess()

    def callCommand(s, command, env):
        server = s.pool.pop()
        result = server.callCommand(command, env)
        s.pool.append(server)
        return result
