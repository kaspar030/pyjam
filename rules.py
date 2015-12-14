import boolparse

# create a default context
default = Context("default")

ctx = default
globalize("ctx")

default.CC='gcc'
default.LINK='gcc'
default.AS='as'
default.AR='ar'
default.CFLAGS=''

global_export(['CC', 'LINK', 'AS', 'AR', 'CFLAGS', 'LINKFLAGS'])

PhonyTarget('all')
PhonyTarget('first')
PhonyTarget('clean')

depends('first', 'all')

def set_context(context):
    global ctx
    ctx = context

class Rule(object):
    def __init__(s, targets, sources, **kwargs):
        global ctx

        s.targets=listify(targets)
        s.sources=listify(sources)

        cname = s.__class__.__name__ + "(" + ", ".join(s.targets) + ")"
        s.context = Context(name=cname, parents=kwargs.get('context', ctx))

        for target in s.targets:
            t = get_unbound_target(target, context=s.context)
            t.actions.append(s)
            _non_source_targets.add(target)

        for source in s.sources:
            get_unbound_target(source, context=s.context)

    def depends(s, targets):
        targets = listify(targets)
        depends(s.targets, targets)
        return s

class Main(Rule):
    def __init__(s, targets, sources=None, **kwargs):
        if not sources:
            sources = glob.glob("*.c") + glob.glob("*.S")

        if not sources:
            raise Exception("Main(): no sources given!")

        super().__init__(locate_bin(targets), locate(sources), **kwargs)


        objects = Compile(s.sources, context=s.context, **kwargs).targets
        Link(s.targets, objects, context=s.context, **kwargs)

        depends(relpath("all"), s.targets, True)
        depends("all", s.targets)

class Module(Rule):
    _map = {}
    _used = set()

    def is_used(name):
        module = Module._map.get(locate_bin(name)[0])
        if module:
            dprint("debug", "USE_IF is_used", module.name, ":", module.used)
            return module.used
        else:
            return False

    bool_parser = boolparse.BoolParser(is_used)

    def __init__(s, targets=None, sources=None, **kwargs):
        if not targets:
            targets = os.path.basename(os.getcwd())

        if not sources and not kwargs.get("pseudomodule"):
            sources = glob.glob("*.c") + glob.glob("*.S")

        if not sources and not kwargs.get("pseudomodule"):
            raise Exception("Module(): no sources given!")

        super().__init__(locate_bin(targets), locate(sources), **kwargs)

        s.name = s.targets[0]
        s.objects = []
        s._uses=listify(kwargs.get('uses'))
        s._uses_hard = set()
        s._uses_all = None
        s.used = False

        kwargs.pop("context", None)
        s.add_sources(s.sources)

        if s.name in Module._map:
            dprint("warning", "Warning: redefining module %s!" % s.name)

        Module._map[s.name]=s

        s.context.defines += s.get_define()

        _targets[s.name].bound = True

        dprint("debug", "new module", s.name)

    def add_defines(s, defines):
        defines = listify(defines)
        for define in defines:
            s.context.defines += define
        return s

    def add_includes(s, includes):
        includes = listify(includes)
        for include in includes:
            s.context.includes += include
        return s

    def get_define(s):
        return "MODULE_" + os.path.basename(s.name).upper().translate(str.maketrans("-", "_"))

    def add_sources(s, sources):
        s.objects.extend(Compile(listify(sources), context=s.context).targets)
        return s

    def get_objects(s, visited=None, unique=False):
        visited = visited or set()
        res = []
        if s in visited:
            return res
        visited.add(s)

        res.extend(s.objects)
        for module in s._uses:
            module = Module._map.get(module)
            if module and module.used:
                res.extend(module.get_objects(visited, False))

        if unique:
            return uniquify(res)
        else:
            return res

    def needs(s, modules, hard=True, locate=True):
        if locate:
            modules = locate_bin(str_list(listify(modules)))
        else:
            modules = str_list(listify(modules))

        for module in modules:
            if not module in s._uses:
                s._uses.append(module)
            if hard:
                s._uses_hard.add(module)
                depends(s.name, module)

        return s

    def uses(s, modules, locate=True):
        return s.needs(modules, False, locate)

    def use_if(s, string):
        if not ctx._use_if_list:
            ctx._use_if_list = []
            _post_parse.append((Module.process_use_if_list, (ctx._use_if_list,)))
        ctx._use_if_list.append((s, string))
        return s

    def process_use_if_list(_use_if_list):
        has_changed = True
        while has_changed:
            new_list = []
            has_changed = False
            for module, condition in _use_if_list:
                if module.used:
                    continue
                if module._process_use_if_hook(condition):
                    has_changed = True
                else:
                    new_list.append((module, condition))
            if has_changed:
                _use_if_list = new_list

    def _process_use_if_hook(s, string):
        if not s.used:
            #dprint("debug", "USE_IF processing", s.name, string)
            res = Module.bool_parser.parseString(string)[0]
            if bool(res):
                s._use()
                return True
            else:
                return False

    def _use(s):
        if s.used:
            return

        dprint("debug", "_USE", s.name)

        s.used = True
#        Module._used.add(s)

        for module_name in s._uses:
            module = Module._map.get(module_name)
            if not module:
                if module_name in s._uses_hard:
                    _err("module", s.name, "needs unknown module", module_name)
                else:
                    dprint("warning", "warning: module", s.name, "uses unknown module", module_name)
                    continue

            if not module.used:
                if module_name in s._uses_hard:
                    module._use()

    def _process_contexts():
        for module_name, module in Module._map.items():
            if module.used:
                for dep in module._uses:
                    dep = Module._map.get(dep)
                    if dep:
                        if dep.used:
                            dprint("debug", "_CTX", module.name, dep.name)
                            module.context._parents.append(dep.context)

class ModuleDir(Module):
    def __init__(s, name, dir=None):
        dir = dir or name
        sources = glob.glob(os.path.join(dir, "*.c")) + glob.glob(os.path.join(dir, "*.S"))
        super().__init__(name, sources)

class PseudoModule(Module):
    def __init__(s, name):
        super().__init__(name, pseudomodule=True)

class Compile(Rule):
    def __init__(s, sources, **kwargs):
        super().__init__([], sources, **kwargs)
        for source in s.sources:
            n, ext = os.path.splitext(source)

            try:
                builder = builders[ext]
                b=builder(source, **kwargs)
                s.targets.extend(b.targets)
            except KeyError:
                dprint("default", "Don't know how to build %s!" % source)

class Tool(Rule):
    name="TOOL"
    actions="echo unconfigured Tool class: target=%target, sources=%sources"
    clean=True
    depends_on_sources=True
    message="[%name] %target %sources"

    def __init__(s, target, sources=[], **kwargs):
        super().__init__(target, sources, **kwargs)
        if s.depends_on_sources and s.targets and s.sources:
            depends(s.targets, s.sources)
        if s.clean:
            Clean(s.targets, **kwargs)

    def build(s, target):
        dprint("context", "building", target.name, "with context", target.context)

        sources = " ".join(s.sources)
        output = s.message.replace("%name", s.name).replace("%target", target.name).replace("%sources", "from " + sources)

        dprint("default", output)

        extra_args = " ".join(s.extra_args(target))
        actions = s.actions.replace("%target", target.name).replace("%sources", sources).replace("%args", extra_args)

        my_env = _env(target.context)

        return shell(actions, env=my_env)==0

    def extra_args(s, target):
        return []

class ObjectCompiler(Tool):
    def __init__(s, sources, **kwargs):
        sources = listify(sources)
        for source in sources:
            obj=locate_bin(subst_ext(source, '.o'))

            super().__init__(obj, source, **kwargs)

    def extra_args(s, target):
        includes = target.context.includes

        return super().extra_args(target) + includes.prefix("-I")

class CompileC(ObjectCompiler):
    actions="${CCACHE} ${CC} ${CFLAGS} %args -c %sources -o %target"
    name='CC'

    def extra_args(s, target):
        defines = target.context.defines
        return super().extra_args(target) + defines.prefix("-D")

class CompileAsm(ObjectCompiler):
    actions="${AS} ${ASFLAGS} %args -c %sources -o %target"
    name='AS'

class Clean(Tool):
    actions="rm -f -- %sources"
    name="CLEAN"
    depends_on_sources=False
    clean=False
    message="[%name] cleaning %sources"

    def __init__(s, sources, **kwargs):
        clean_target = relpath("clean")
        super().__init__(clean_target, sources, **kwargs)
        depends("clean", clean_target)

        _targets[clean_target].rebuild = True

    def build(s, target):
        for source in s.sources:
            dprint("default", "[CLEAN]", source)
            try:
                os.remove(source)
            except FileNotFoundError:
                pass

        return True

builders['.c'] = CompileC
builders['.S'] = CompileAsm

class Link(Tool):
    name="LINK"
    actions="${LINK} ${LINKFLAGS} -Wl,--start-group %sources %args -Wl,--end-group -o %target"

    def extra_args(s, target):
        return super().extra_args(target) + list(reversed(target.context.objects.prefix("")))

class LinkModule(Tool):
    name="LINK"
    actions="${LINK} -Wl,--start-group %sources -Wl,--end-group %args ${LINKFLAGS} -o %target"
    message="[%name] %target"

    def __init__(s, target, sources, **kwargs):
        s.modules = listify(sources)
        super().__init__(target, **kwargs)

        entry = (s.post_bind, ())
        if not entry in _post_bind:
            _post_bind.append(entry)

    def post_bind(s):
        objects = []
        for module in s.modules:
            module = Module._map.get(module)
            if not module:
                continue
            objects.extend(module.get_objects())
            if not module.context in s.context._parents:
                s.context._parents.append(module.context)

        s.sources = objects
        depends(s.targets, s.sources)

    def extra_args(s, target):
        return super().extra_args(target) + list(reversed(target.context.libs.prefix("-l")))

class Archive(Tool):
    name="AR"
    actions="${AR} rcs %target %sources"

class Toolcheck(Rule):
    def __init__(s, targets, **kwargs):
        super().__init__(targets, [], **kwargs)

        s.options=kwargs or {}

    def build(s, target):
        name = s.options.get('name') or target.name
        command = s.options.get('command') or name

        dprint("default", "[TOOL] %s" % name)

        return shell(command, env=_env(target.context))==0

class Print(Rule):
    def __init__(s, targets, message, **kwargs):
        super().__init__(targets, [], **kwargs)
        s.message=message

    def build(s, target):
        dprint("default", s.message)
        return True

class Mkdir(Tool):
    name="MKDIR"
    actions="mkdir -p -- %target"
    clean=False # rm -f on dir is dangerous!

class Touch(Tool):
    name="TOUCH"
    actions="touch -- %target"

class NoOp(Tool):
    name="NOOP"
    actions=""

    def build(s, target):
        return True

class NoOpShell(Tool):
    name="NOOPSHELL"
    actions="true"

class DebugEnv(Tool):
    name="DebugEnv"
    clean=True
    actions = "set > %target"

class Fail(Rule):
    def __init__(s, target):
        super().__init__(target, [])

    def build(s, target):
        return False

def setup_rule_hooks():
    _post_bind.append((Module._process_contexts, ()))
