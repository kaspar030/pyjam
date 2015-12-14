# pyjam
PyJam - the pythonic build tool

## Introduction

PyJam was born through growing frustration with too complex Makefiles that even
became slow because of the ugly hacks needed to accomplish things, and
definitely were no fun to work with, maintain and extend.

PyJam uses many concepts of Perforce's jam, but combines them with the power of
Python as language for the build files.

Some alternatives didn't suit my needs:

- CMake's idea of creating makefiles from another type of syntax didn't please
  me at all, combined with my impression of CMake's syntax being everything but
  intuitive.

- SCons is still stuck at python 2.x, which, while practically probably not
  much of a problem, made me feel outdated even when just starting to use it.
  But PyJam's Object-based syntax looks similar.

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
