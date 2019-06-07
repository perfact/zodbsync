# PerFact ZODBSync

This package provides tools to serialize Zope objects and store them in a file
system structure as well as to restore Zope objects from this structure.

An additional feature that goes beyond the main scope of the package is the
possibility to create automatic git snapshots of the object tree, which
requires the package perfact.pfcodechg.

## Maintainers:
Ján Jockusch <jan.jockusch@perfact.de>

Viktor Dick <viktor.dick@perfact.de>

## Repository:

    git clone https://github.com/perfact/zodbsync.git

## Installation

The package should be installed using `pip` in the same virt-env as `zope`, p.e.

    zope/bin/pip install git+https://github.com/perfact/zodbsync

On PerFact systems, it should automatically be pulled by the `requirements.txt`
of the package `perfact-dbutils-zope2` and included there. If installing on an
older system, run

    sudo -H /opt/zope/zope2.13/bin/pip install git+https://github.com/perfact/zodbsync

## Configuration

Use the `config.py` as a starting point for your configuration. At the moment,
it is not automatically installed. The canonical path for the configuration is
`/etc/perfact/modsync/config.py`, so if you do not want to supply the path to
the configuration when calling the scripts, copy the configuration file there
(this will change in a future version).

The most important settings are:
### `conf_path` or `wsgi_conf_path`
If using ZServer (only Zope 2), set `conf_path` to the `zope.conf` of your Zope
instance, which must be configured to connect to a ZEO server (no standalone
instance). If using a WSGI server (Zope 2 or Zope 4), set `wsgi_conf_path`
accordingly.

### `base_dir`
Inside this folder (actually, in a subfolder named `__root__`), the serialized
objects are placed. 

### `manager_user`
The name of a user that must be defined in the top-level UserFolder (`acl_users`)
and which has Manager permissions. This user is considered to be the default
owner of objects if no other information is stored in the object and the
transaction that is used to upload objects to the ZODB is done by this user.

### `datafs_path`
The path to the location of the Data.fs file. This is needed for the watcher
mode of `perfact-zoperecord`.

## Usage

The two main tools at this moment are `perfact-zoperecord` and `perfact-zopeplayback`.

### Recording objects

If calling `perfact-zoperecord` with no other options, it reads the complete
object tree and stores the objects to the file system. Each object is mapped to
a file named `__meta__` and further subfolders (if it is an object that has
contents, like a `Folder`) or a file whose name starts with `__source` (if it is
an object with some sort of source, like a `Page Template`, a `Script (Python)`, a
`File` etc.).
Objects whose type is not yet supported are created with a minimal `__meta__` file,
containing only the `id`, `title`, `type` and an `unsupported` marker.

At the end of the recording, the last transaction ID is stored in a file called
`__last_txn__` and a git commit is performed.

If `perfact-zoperecord` is called with the `--lasttxn` option, it reads this
transaction ID, gets a list of all transactions after that and trys to record
only those paths that were affected by these. The paths are extracted from the
transaction note, which works well if editing an object in the management
interface of Zope, but not necessarily if an object is changed from within a
script, if it is transferred by the ZSyncer or if objects are cut and pasted
(in the latter case, only the target of the move operation is recognized).

After running, the largest transaction ID of the list that was obtained at the
beginning is again stored in `__last_txn__`.

If a specific path is required to be recorded, it can also be passed using the
`--path` argument.

The argument `--watch` provides the watcher mode, a new mode of operation that
aims to bypass the shortcomings of `--lasttxn`. In watcher mode, the recorder
stays alive and builds an object tree of all objects in the ZODB. Each time it
wakes up, it scans for new transactions, opens the Data.FS directly (in
read-only mode) to obtain all affected object IDs, updates its object tree and
uses it to obtain the physical paths of all affected objects. After finishing,
it sleeps for 10 seconds before waking again. This should provide an almost
live recording that does not miss any changes.


### Playing objects back

`perfact-zopeplayback` is the other side of the coin, able to create and modify
objects in the ZODB from a file system structure as it is created by
`perfact-zoperecord`. 

By default, `perfact-zoperecord` recurses into the objects below a given
object, removing any superfluous objects and updating existing objects so they
match their file system representation. An exception are objects that are
marked as `unsupported`, which are ignored if found in the ZODB. If only a
given object itself should be updated (properties, security settings etc.),
`--no-recurse` can be used.

There are two other modes to use with `perfact-zopeplayback`, selected by
passing `--pick` or `--apply`. These assume the file system representation is
stored in a git repository and provide wrappers for `git cherry-pick` and `git
am`, respectively. They also change the interpretation of the positional `path`
arguments.

If using `--pick`, the given paths are interpreted as git commits. This is
useful if some development has been done in a branch or on a remote system that
has to be deployed to the current system. It then becomes possible to do
something like

    git fetch origin
    perfact-zopeplayback --pick origin/master

to pull the latest commit, apply it to the current repo and upload the affected
paths to the Data.FS. It can also be used to pull multiple commits - allowing,
for example, to pull all commits where the commit message starts with T12345:

    perfact-zopeplayback --pick $(git log origin/master --reverse --pretty=%H --grep="^T12345" )


Similarly, `--apply` allows to pass patch files that are to be applied, playing
back all objects that are changed by these patches.

## Compatibility
This package aims to replace similar functionality that was previously found in
python-perfact and perfact-dbutils-zope2. For backwards compatibility, those
packages were changed so the corresponding calls try to import
`perfact.zodbsync` and use the functionality there, falling back to the
previous implementation if that fails. Corresponding deprecation warnings are
included.

## Caveats

Zope allows `External Method`s to be present in the ZODB even if the
corresponding modules are no longer present as extensions. It does not,
however, allow saving such an object. This gives errors if object trees
containing broken `External Method`s are recorded and played back. The same
holds for `Z SQL Method`s which have `class_name` and `class_file` set to a no
longer existing extension.

## To Do / Roadmap

* At some point, it might become necessary to easily allow playback to read
  from a different path, to avoid changes that are collected to be played back 
  being overwritten by a running `zoperecord` in watcher mode.

* These tools can serve as a basis for replacing and improving ZSyncer
  functionality, offering diffs between development, testing and production
  systems that include metadata and do not depend on timestamps as well as the
  possibility to pull objects from a different system.
