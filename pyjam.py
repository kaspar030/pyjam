#!/usr/bin/env python3

import argparse
import copy
import glob
import os
import pprint
import subprocess
import sys
import traceback
import jobserver
import time

from os.path import abspath, dirname, basename
import threading
from threading import Thread
from queue import PriorityQueue, Empty

abspath = os.path.abspath

# global target maps
_targets = {}
_unbound_targets = []
_non_source_targets = set()
_wanted = []
_wanted_names = []

# hooks
_post_parse = []
_post_bind = []
_pre_build = []

# debug options
_debug_levels = { 'error', 'warning', 'default' }
_valid_debug_levels = {'binding', 'include', 'targets', 'depends', 'exports', 'env', 'threads', 'verbose', 'needed', 'context', 'locate', 'cause', 'commands', 'phases', 'warning', 'error', 'debug', 'times'}

# variable export settings
_var_exports = set()
_var_unexports = set()
_global_var_exports = set()
_global_var_unexports = set()

# subshell defaults
_shell_options = ["-e"]

# shell environment
_original_env = os.environ.copy()

# variables for subdirectory/file includes
_included_set = set()
_include_stack = []
_cwd_stack = []
_include_cache = {}

builders={}

_globals = globals()

_thread_local = None
_exit_threads = False

_basedir = None
_bindir = None
_relpath = None

_start_cwd = None

# build queue
_prio = 0
_build_queue = None

# ForkServer
_job_server_pool = None

class StartedInSubdirException(Exception):
    def __init__(s):
        super().__init__()

class BasedirNotFoundException(Exception):
    def __init__(s):
        super().__init__()

def dprint(level, *args, **kwargs):
    if level in _debug_levels:
        print(*args, **kwargs)

def add_target_action(target, rule):
    target = _targets.get(target)
    target.actions.append(rule)

def listify(something):
    if not something:
        return []
    if not type(something)==list:
        return [something]
    return something

def str_list(list):
    res = []
    for x in list or []:
        res.append(str(x))
    return res

def uniquify(seq):
    # Order preserving
    seen = set()
    return [x for x in seq if x not in seen and not seen.add(x)]

def list_remove_all(target, remove):
    if remove:
        result = []
        rset = set(remove)
        for entry in target:
            if not entry in rset:
                result.append(entry)
        target = result

    return target

class Context(object):
    i=0
    def __init__(s, name=None, parents=None):
        s._name = name # "(%i)%s" % (Context.i, name)
        Context.i+=1
        s._fields = {}
        s._parents = copy.copy(listify(parents))
        dprint("context", "initialized new context", s, "with parents", s._parents)

    def __setattr__(s, name, value):
        if name.startswith("_"):
            s.__dict__[name] = value
        else:
            if isinstance(value, Var):
                s._fields[name] = copy.deepcopy(value)
                return

            var = s._fields.get(name)
            if not var:
                var = Var(value)
                var.inherit=False
                s._fields[name] = var
            else:
                var.set(value)

    def __getattr__(s, name, visited=None):
        if name.startswith("_"):
            return s.__dict__.get(name)
        visited = visited or set()
        if s in visited:
            return None
        visited.add(s)

        var = s._fields.get(name, Var())

        var.parents = []
        for parent in s._parents:
            pvar = parent.__getattr__(name, visited)
            if pvar:
                var.parents.append(pvar)

        return var

    def get(s, name):
        return str(s.__getattr__(name)) or None

    def __repr__(s, visited=None, indent="", print_fields=False):
        visited = visited or set()
        if s in visited:
            return ""
        visited.add(s)

        res = "\n%sContext(%s) {\n" % (indent, s._name)
        if print_fields:
            for field in sorted(s._fields.keys()):
                res += ("%s%s=%s\n" % (indent+"  ", field, s.__getattr__(field)))
        if s._parents:
            for parent in s._parents:
                res += parent.__repr__(visited, indent+"  ")
        res += "\n%s}\n" % indent
        return res

    def fields(s, visited=None):
        visited = visited or set()
        if s in visited:
            return set()
        visited.add(s)

        fields = set(s._fields.keys())
        for parent in s._parents:
            fields |= parent.fields(visited)
        return fields

    def pprint(s):
        print(s.__repr__() + " {")
        for field in s.fields():
            print("    %s=%s" % (field, s.__getattr__(field)))
        print("}")

class Var(object):
    def __init__(s, initial=None, joiner=None, start=None):
        if isinstance(initial, Var):
            s.list = initial.list
            s.remove = initial.remove
        else:
            s.list = listify(initial)
            s.remove = []

        s.joiner=joiner or " "
        s.start=start or None
        s.parents = []
        s.inherit = True

    def combined(s, already_joined=None):
        already_joined = already_joined or set()
        combined = []
        if s.inherit:
            for entry in s.parents:
                if not entry in already_joined:
                    already_joined.add(entry)
                    combined.extend(entry.combined(already_joined))

        combined.extend(s.list)

        list_remove_all(combined, s.remove)
        return combined

    def join(s, joiner=None):
        combined = s.combined()

        if not combined:
            return ""

        joiner = joiner or s.joiner

        return (joiner + joiner.join(combined)).lstrip()

    def shell_join(s, joiner=None):
        combined = s.combined()

        if not combined:
            return ""

        joiner = joiner or s.joiner

        res = ""
        for entry in combined:
            res += "'%s%s'" % (joiner, entry)

        return res

    def prefix(s, prefix):
        combined = s.combined()

        if not combined:
            return []

        res = []

        for entry in combined:
            res.append("'%s%s'" % (prefix, entry))

        return res

    def __repr__(s):
        return s.join()

    def append(s, whatever):
        if whatever:
            s.list.extend(listify(whatever))
        return s

    def set(s, whatever):
        s.list = listify(whatever)
        s.inherit = False
        return s

    def unset(s):
        s.list = []
        s.remove = []
        s.inherit = False
        return s

    def reset(s):
        s.unset()
        s.inherit=True
        return s

    def __iadd__(s, other):
        if hasattr(other, 'list'):
            s.list.extend(other.list)
        else:
            s.list.extend(listify(other))
            for entry in other:
                try:
                    s.remove.remove(entry)
                except ValueError:
                    pass
        return s

    def __isub__(s, other):
        other = listify(other)
        for entry in other:
            try:
                s.list.remove(other)
            except ValueError:
                pass
            if not entry in s.remove:
                s.remove.append(entry)
        return s

def depends(targets, deps, bind=False):
    targets = listify(targets)
    deps = listify(deps)

    for target in targets:
        for dep in deps:
            dprint("depends", "Depends: \"%s\" : \"%s\"" % (target, dep))
        if target in deps:
            dprint("depends", "warning: %s depends on itself!" % target)
            deps.remove(target)
        target_obj = get_unbound_target(target)
        target_obj.depends(deps)
        target_obj.bound=bind

class UnknownTargetException(Exception):
    def __init__(s, name):
        s.name = name
        super().__init__(s)

    def __str__(s):
        return "UnknownTargetException: target \"%s\"." % s.name


def get_target(name):
    target =_targets.get(name)
    if not target:
        raise UnknownTargetException(name)

    return _targets.get(name)

def get_unbound_target(name, context=None):
    try:
        target = get_target(name)
        if context and not target.context:
            target.context=context
        return target

    except UnknownTargetException:
        dprint("targets", "new unbound target", name)
        target = Target(name, context)
        _unbound_targets.append(target)
        return target

class Target(object):
    _updated = 0

    def get(name):
        return _targets.get(name)

    def __init__(s, name, context=None, **kwargs):
        s.name=name
        _targets[name] = s

        s.deps=[]
        s.needed_for=[]
        s.missing=[]

        s.bound=False
        s.wanted=False
        s.rebuild=False
        s.stable=False
        s.always=False
        s.queued=False
        s.done=False
        s.not_file= kwargs.get('no_file') or True

        s.actions = []
        s.ndeps = 0
        s.lock = threading.Lock()

        s.prio = -1
        s.mtime=sys.maxsize

        s.env = {}

        s.context = context

    def prepare(s):
        dprint("debug", "... preparing target", s.name)
        with s.lock:
            if not s.stable:
                s.stable = True

                if args.all and (s.name in _non_source_targets):
                    s.rebuild=True
                else:
                    mtime = s.update_mtime()

        s.update_deps(True)

    def update_deps(s, stable=False):
        new_deps = []
        unknown_deps = False
        with s.lock:
            s.ndeps = 0
            for dep in s.deps:
                try:
                    if type(dep)==str:
                        dep_obj = _targets[dep]
                    else:
                        dep_obj = dep

                    if not dep_obj.done:
                        if stable:
                            dep_obj.prepare()
                        else:
                            dep_obj.update_deps()

                        new_deps.append(dep_obj)
                        dep_obj.needed_for.append(s)
                        s.ndeps += 1
                except KeyError:
                    dprint("default", "... unknown dependency %s on target %s." % (dep, target_name))
                    unknown_deps = True

            s.deps = new_deps

        if unknown_deps:
            clean_exit(1)

    def is_needed(s):
#        dprint("debug", "... is_needed()", s.name, s.wanted, s.always, s.check_parents())
        return s.wanted or s.always or s.check_parents()

    def check_parents(s):
        for target in s.needed_for:
            if target.is_needed():
                dprint("needed", '... need', s.name, "for", target.name)
                return True
        return False

    def can_make(s):
        if s.missing:
            for target in s.missing:
                dprint("default", "...skipped %s for lack of %s..." % (s.name, target))
            return False

        return True

    def update_mtime(s):
        s.mtime=sys.maxsize

    def do_build(s):
        res = s.can_make() and s.build()
        Target._updated += 1
        return res

    def check_update(s):
        if s.rebuild:
            pass
        else:
            for dep in s.deps:
                if dep.check_update():
                    dprint('cause', "rebuilding %s because dependency %s has to be rebuilt." % (s.name, dep.name))
                elif dep.mtime > s.mtime:
                    dprint('cause', "%s is older than %s (%s < %s). Rebuilding." %( s.name, dep.name, s.mtime, dep.mtime))
                else:
                    continue
                s.rebuild=True
                break
        return s.rebuild

    def build(s):
            try:
                actions = s.actions
            except KeyError:
                dprint("default", "don't know how to build %s." % s.name)
                return False

            result = True
            for action in actions:
                build = None
                try:
                    build = getattr(action, 'build')
                except AttributeError:
                    pass
                if build:
                    result = build(s)
                    if result==False:
                        return result
                    s.done = True

            return result

    def ready_for_building(s, check_deps=False):
        if (not s.queued) and (not s.ndeps) and s.stable:
            if not s.is_needed():
                dprint("verbose", s.name, "not needed.")
            else:
                return True
        return False

    def set_stable(name):
        _pre_build.append((Target._set_stable_hook, (name,)))

    def _set_stable_hook(name):
        Target.get(name).prepare()

    def depends(s, targets):
        targets = listify(targets)
        for target in targets:
            if not target in s.deps:
                s.deps.append(target)
        return s

    def set_always(s, always=True):
        s.always=always
        return s

    def depends_on(s, other):
        for dep in s.deps:
            if dep.name==other.name or dep.depends_on(other):
                return True

        return False

    def check_circular_dep(s, stack):
        stack.append(s)

        for dep in s.deps:
            if dep in stack or dep.check_circular_dep(stack):
                return True

        stack.pop()
        return False

    def __str__(s):
        return s.name

    def _yield_if(s, stable=None, queued=None):
        if stable!=None:
            if s.stable != stable:
                return False
        if queued != None:
            if s.queued != queued:
                return False
        return True

    def iterate_dependencies(s, stable=None, queued=None):
        for dep in s.deps:
            if type(dep)==str:
                dep = _targets[dep]
            for _dep in dep.iterate_dependencies(stable, queued):
                if _dep._yield_if(stable, queued):
                    yield _dep
            if dep._yield_if(stable, queued):
                yield(dep)

def PhonyTarget(name):
    tmp = Target(name)
    tmp.bound=True
    return tmp

class FileTarget(Target):
    def __init__(s, name, context=None):
        dprint("targets", "New file target %s" % name)
        super().__init__(name, context)
        s.not_file=False
        s.stat=None

    def update_stat(s):
        try:
            s.stat = os.stat(s.name)
            return True
        except OSError:
            return False
        except FileNotFoundError:
            return False

    def update_mtime(s):
        if not s.stat and not s.update_stat():
            return False
        else:
            s.mtime=s.stat.st_mtime
            return True

    def check_update(s):
    #    print("check_update", s.name, s.deps)
        if s.rebuild==True:
    #        print("already_true", s.name)
            pass
        elif not s.update_mtime():
    #        print("non_existant", s.name)
            s.rebuild=True
        else:
            s.rebuild = super().check_update()

        return s.rebuild

def touch(path):
    with open(path, 'a'):
        os.utime(path, None)

def subst_ext(f, new_ext):
    n, ext = os.path.splitext(f)
    return "%s%s" % (n, new_ext)

def bind_target(target):
    if not target.bound:
        dprint("binding", "Binding %s to file." % target.name)
        bound_target = FileTarget(target.name, target.context)
        bound_target.deps = target.deps
        bound_target.actions = target.actions
        bound_target.env = target.env
        target = bound_target
        target.bound=True

    return target

def bind_targets():
    global _unbound_targets
    for utarget in _unbound_targets:
        bind_target(utarget)
    _unbound_targets = []

def call_hooks(list):
    for hook, params in list:
        hook(*params)

def post_parse():
    global _post_parse
    call_hooks(_post_parse)
    _post_parse = []

def post_bind():
    global _post_bind
    call_hooks(_post_bind)
    _post_bind = []

def pre_build():
    global _pre_build
    call_hooks(_pre_build)
    _pre_build = []

def post_prepare():
    global _post_prepare
    call_hooks(_post_prepare)
    _post_prepare = []

def start_workers():
    global _build_queue
    _build_queue = PriorityQueue()

    global _thread_local
    _thread_local = threading.local()

    if args.jobs:
        for i in range(0, args.jobs):
            t = Thread(target=worker, args=(_build_queue, True, i), daemon=True)
            t.daemon = True
            t.start()

def build_targets(all=True):
    global _prio
    global _build_queue

    for target in _wanted:
        with target.lock:
            if (target.prio==-1):
                target.prio = _prio
                _prio += 1

        if all:
            stable = None
        else:
            stable = True

        for dep in target.iterate_dependencies(stable=stable, queued=False):
            dprint("verbose", "... build_targets() considering", dep, dep.ndeps)
            with dep.lock:
                if (dep.prio==-1):
                    dep.prio = _prio
                    _prio += 1

                if dep.ready_for_building(all):
                    dprint("verbose", "... queueing target", dep)
                    dep.queued=True
                    _build_queue.put((dep.prio, dep))
                else:
                    dprint("verbose", "... target", dep, "not ready for building")

def worker(queue, block=False, n=0):
    _thread_local.n = n
    global _exit_threads

    dprint("threads", "%2i: Worker thread started." % n)
    while not _exit_threads:
        try:
            prio, target = queue.get(block=block)
        except Empty:
            return

        dprint("threads", "%2i: building target %s (prio=%s)" % (n, target.name, prio))

        target.check_update()
        success = target.rebuild==False or target.do_build()
        if not success and args.quit:
            queue.task_done()
            _exit_threads = True
            try:
                while queue.get(block=False):
                    queue.task_done()
            except Empty:
                pass
            return

        target.done = True
        dprint("threads", "%2i: done building target %s (prio=%s)" % (n, target.name, prio))

        for needed_for in target.needed_for:
            with needed_for.lock:
                if not success:
                    needed_for.missing.append(target.name)
                else:
                    needed_for.ndeps -= 1
                    if needed_for.prio != -1:
                        if needed_for.ready_for_building():
                            dprint("verbose", "%2i: queuing target" % n, needed_for, "(prio=%s)" % needed_for.prio)
                            needed_for.queued = True
                            queue.put((needed_for.prio, needed_for))

        queue.task_done()

def want_targets(targets):
    targets = listify(targets)
    for target_name in targets:
        if _start_cwd != os.getcwd():
            print(_start_cwd, _basedir)
            target_name = os.path.join(os.path.relpath(_start_cwd, _basedir), target_name)

        target_name = os.path.normpath(target_name)
        _wanted_names.append(target_name)

def select_wanted(set_stable=False):
    for target_name in _wanted_names:
        try:
            target=_targets[target_name]
            if target.stable:
                continue
            if set_stable:
                dprint("debug", "stabilizing", target)
                target.prepare()
            else:
                target.update_deps()

            if target.wanted:
                print("wanted skipping already processed", target_name)
                continue
            target.wanted=True
            dprint("verbose", "... want target", target_name)
            if not target in _wanted:
                _wanted.append(target)
        except KeyError:
            print("unkown target", target_name)

def parse_args():
    parser = argparse.ArgumentParser(prog='pyjam', description='A pythonic build system.')

    parser.add_argument('targets', metavar='target', type=str, nargs='*',
            help='targets to build (default: all)', default="all")
    parser.add_argument('--version', action='version', version='%(prog)s 0.1')

    parser.add_argument('-a', "--all", help='Build all targets, even if they are current.', action="store_true", default=False )
    parser.add_argument('-j', '--jobs', type=int, action='store',
            help='number of concurrent jobs (default: 1)')
    parser.add_argument('-q', "--quit", help='stop on first error', action="store_true" )
    parser.add_argument('-d', "--debug", help='enable specific debug output', action="append", choices=_valid_debug_levels, metavar="{x}" )
    parser.add_argument('-Q', "--quiet", help='disable default output', action="store_true" )

    return parser.parse_args()

def subinclude(dirname):
    return include(os.path.join(dirname, 'build.py'))

def subdir():
    if not _basedir:
        _included_set.pop()
        _include_stack.pop()
        _cwd_stack.pop()
        raise StartedInSubdirException()

def include(filename):
    global _relpath, _included_set, _include_stack, _cwd_stack, _include_cache
    fullpath = os.path.abspath(filename)
    if False and fullpath in _included_set:
        dprint("include", "Already included", fullpath)
        return

    _included_set.add(fullpath)
    _include_stack.append(fullpath)
    _cwd_stack.append(os.getcwd())
    dirname = os.path.dirname(fullpath)
    try:
        dprint("include", "Including \"%s\"." % filename)
        os.chdir(dirname)
        global _var_exports
        _saved_exports = _var_exports
        _var_exports = _saved_exports.copy()

        code = _include_cache.get(fullpath)
        if not code:
            with open(fullpath) as f:
                code = compile(f.read(), fullpath, 'exec')
                _include_cache[fullpath] = code

        saved_globals = globals().copy()
        globals()['_relpath'] = os.path.relpath(dirname, _basedir)

        try:
            del saved_globals["_globalize"]
        except KeyError:
            pass

        exec(code, globals(), globals())

        if "_globalize" in globals():
            for var in _globalize:
                if var in globals():
                    saved_globals[var]=globals()[var]

        globals().update(saved_globals)
        _var_exports = _saved_exports
        dprint("include", "Including \"%s\" done." % filename)
    except FileNotFoundError:
        raise Exception("Error: include(): Cannot find \"%s\"! (tried: \"%s\")" % (filename, fullpath))

    last_cwd = _cwd_stack.pop()
    os.chdir(last_cwd)
    _relpath = os.path.relpath(last_cwd, _basedir)
    _include_stack.pop()

def export(variables):
    _export("Locally", _var_exports, variables)

def global_export(variables):
    globalize(variables)
    _export("Globally", _global_var_exports, variables)

def unexport(*args):
    _export("Locally", _var_unexports, variables)

def global_unexport(*args):
    _export("Globally", _global_var_unexports, variables)

def _export(text, container, variables):
    _export_unexport(text, True, container, variables)

def _unexport(text, container, variables):
    _export_unexport(text, False, container, variables)

def _export_unexport(text, mode, container, variables):
    if export:
        modetext="exporting"
    else:
        modetext="unexporting"

    variables = listify(variables)
    for var in variables:
        dprint("exports", text, modetext, var)
        if export:
            container.add(var)
        else:
            container.delete(var)

def shell(commands, env=None):
    commands = listify(commands)
    commands = " ".join(commands)

    if not env:
        env = _env()

    output, result = _job_server_pool.callCommand(_shell_options + [commands], env)

    return result

def globalize(fields):
    fields = listify(fields)

    _globalize = globals().get('_globalize')
    if not _globalize:
        _globalize = set()
        globals()['_globalize'] = _globalize

    for field in fields:
        #print("Globalizing", field)
        _globalize.add(field)

def unglobalize(fields):
    fields = listify(fields)
    _globalize = globals().get('_globalize')
    if _globalize:
        for field in fields:
            #print("Unlobalizing", field)
            _globalize.remove(field)

def dict_diff(A,B):
    return {x:A[x] for x in A if x not in B or A[x]!=B[x]}

def _env(context=None):
    if not context:
        context = ctx

    my_env = os.environ.copy()
    for env in (_global_var_exports | _var_exports)-(_global_var_unexports|_var_unexports):
        val = context.get(env) or locals().get(env) or globals().get(env)
        if val:
            dprint("exports", "Exporting %s=%s" % (env, val))
            my_env[env]=str(val)

    return my_env

def locate(targets, context=None):
    result = []
    for target in listify(targets):
        result.append(relpath(target))
        dprint("locate", "Locating source \"%s\" to \"%s\". (relpath=%s)" % (target, relpath(target), _relpath))

    return result

def locate_bin(targets, context=None):
    context = context or ctx
    result = []
    bindir = context.get('bindir') or relbase(os.path.join(_basedir, "bin"))

    for target in listify(targets):
        bin_path = os.path.join(bindir, target)
        result.append(bin_path)
        try:
            dirname = os.path.join(_basedir, os.path.dirname(bin_path))
            if dirname:
                os.makedirs(dirname, exist_ok=True)
        except FileExistsError:
            pass
        except FileNotFoundError as e:
            print(e)

        dprint("locate", "Locating target \"%s\" to \"%s\". (relpath=%s)" % (target, bin_path, _relpath))

    return result

def locate_basedir():
    dprint("include", "Searching for project.py...")
    while True:
        cwd = os.getcwd()
        if os.path.isfile("project.py"):
            dprint("include", "Found project.py in %s" % cwd)
            return True
        else:
            if cwd == "/":
                raise BasedirNotFoundException
            else:
                os.chdir("..")

def set_basedir(dir=None):
    global _basedir

    if dir:
        _basedir = abspath(dir)
    else:
        _basedir = os.getcwd()
    globalize('_basedir')
    dprint("verbose", "Basedir set to", _basedir)

def relpath(file):
    return os.path.normpath(os.path.join(_relpath, file))

def relbase(path):
    return os.path.normpath(os.path.relpath(path, _basedir))

def check_depends():
    for target, obj in sorted(_targets.items()):
        stack = []
        if obj.check_circular_dep(stack):
            dprint('error', "... error: circular dependency:", " -> ".join(str_list(stack + [obj])))
            clean_exit(1)

def clean_exit(code=0):
    os.chdir(_start_cwd)
    if _job_server_pool:
        _job_server_pool.destroy()
    sys.exit(code)

def _err(*args):
    dprint("error", "error:", *args)
    clean_exit(1)

def start_building(all=False):
    dprint("debug", "... start building ... (all=%s)" % all)
    a = time.time()
    post_parse()
    b = time.time()
    bind_targets()
    c = time.time()
    post_bind()
    d = time.time()
    select_wanted(all)
    e = time.time()
    build_targets(all)
    f = time.time()

    dprint("times", "... times: post_parse: %.3fs binding: %.3fs, post_bind: %.3fs select_wanted: %.3fs building: %.3fs" %
            (b-a, c-b, d-c, e-d, f-e))

if __name__ == '__main__':
    args = parse_args()

    if args.quiet:
        _debug_levels.discard("default")

    if args.debug:
        for debug in args.debug:
            _debug_levels.add(debug)
            if debug=="commands":
                _shell_options.append("-x")

    _start_cwd = os.getcwd()

    _relpath = ""

    pp = pprint.PrettyPrinter(indent=4)

    # locate project base buildfile
    if not (os.path.isfile("project.py") or locate_basedir()):
        print("pyjam: Error: project.py not found (searched in current path and every paths up to \"/\")")
        clean_exit(1)

    # instantiate jobserver subprocess
    _job_server_pool = jobserver.JobServerPool(args.jobs or 1)

    globalize([ "_prio", "_unbound_targets", "_build_queue", "_targets", "_post_parse", "_post_bind", "_pre_build"])

    want_targets(args.targets)
    start_workers()

    dprint("phases", "... entering parsing phase ...")
    before = time.time()
    try:
        set_basedir()
        include("project.py")
    except Exception as e:
        # this is the exception handler where we end up
        # on any uncatched exceptions within the buildfiles
        traceback.print_exc()
        clean_exit(1)

    after = time.time()
    dprint("times", "... parsing took %.3fs" % (after - before))

    start_building(True)

    before = time.time()
    if not args.jobs:
        worker(_build_queue)

    _build_queue.join()
    after = time.time()
    dprint("times", "... building took %.3fs" % (after - before))

    dprint("default", "... updated", Target._updated, "target(s) ...")
    clean_exit(0)
