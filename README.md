# pyjam
PyJam - the pythonic build tool

## Introduction

PyJam was born through growing frustration with too complex Makefiles that even
became slow because of the ugly hacks needed to accomplish things, and
definitely were no fun to work with, maintain and extend.

PyJam uses many concepts of Perforce's jam, but combines them with the power of
Python as language for the build files.

PyJam has primarily been developed for the RIOT OS (riot-os.org), which needs
to build ~100 examples for around 40 platforms, selected from ~300kLOC, with
dozens of modules (think libraries) with source-level interdependencies. While
it's make-based build system worked fairly well, with time it got harder to
maintain, and a full CI build would take several hours, badly utilizing caching
& parallel building.  The goal of PyJam was to get CI responses in under 5
minutes while keeping the actual build files as readable and maintainable as
possible.

Some alternatives didn't suit my needs:

- CMake's idea of creating makefiles from another type of syntax didn't please
  me at all, combined with my impression of CMake's syntax being everything but
  intuitive.

- SCons is still stuck at python 2.x, which, while practically probably not
  much of a problem, made me feel outdated even when just starting to use it.
  But PyJam's Object-based syntax looks similar.

- ninja needs something to create it's actual build files. It might be possible
  to use PyJam to express complex builds and then use ninja as build engine,
  but PyJam's integrated build scheduler is quite fast, so there's not much to
  gain.

- tup has many nice ideas (and serves as inspiration), but it's syntax seems
  foreign, and didn't encourage me to even try to implement a complex module
  system with it. But if you're interested in planned features for PyJam,
  read Mike Shal's Build System Rules and Algorithms.
  (http://gittup.org/tup/build_system_rules_and_algorithms.pdf)

## Features

- buildfiles are essentially Python files
- parallel building
- powerful "module" system (think libraries) with dependencies
- express module dependencies using boolean syntax
- override variables per target, executable, module
- extensive debugging
- automatically clean-up old files
- automatic C header file dependencies
- GPLv2 licensed

## Requirements

PyJam's dependencies:

- python >= 3.2
- pyparsing

## Quickstart

Download, put somewhere, then link pyjam.py somewhere into your PATH. I use
"/home/myname/bin/pyj".  (pip packaging patch welcome).

Check out the supplied example/hello-world. Just cd into that directory and
take a look at "project.py":

```
$ cat project.py
# grab all *.c files and compile them into "bin/hello-world"
Main("hello-world")
```

Now start the build:

```
$ pyj
[CC] bin/hello-world.o from hello-world.c
[LINK] bin/hello-world from bin/hello-world.o
... updated 3 target(s) ...
$
```

You might wonder, "Why did it update 3 targets?". That's because "all" counts as target,
depending on the two files that actually got created.

## Building a project with PyJam

PyJam expects a file called "project.py" in a project's root folder.

Before executing that file, PyJam will automatically include the file
"rules.py" that came with it. This file contains basic rule definitions
that can already build many applications.

## PyJam syntax

PyJam's build files are *mostly* standard python files. Beware one difference,
though: If a file is included, it's global variables will be a shallow copy of
the globals() of the calling script.

That means:

(in file project.py:)
```
variable = "A"
include("other.py")
print(variable)
```

(in file other.py):
```
print(variable)
variable = "B"
```

... will print
```
A
A
```

If you need to hand down a variable from within "other.py", use globalize():

```
variable = "A"
include("other.py")
print(variable)
```

(in file other.py):

```
print(variable)
variable = "B"
globalize(variable)
```

... will print

```
A
B
```
