# PerFact ZODBSync

This package provides tools to serialize Zope objects and store them in a file
system structure as well as to restore Zope objects from this structure.

Some features require that the file system structure is governed by `git` as
version control system.

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
instance. If it is a standalone instance exclusively accessing its ZODB, it
must be powered down if used by zodbsync. So usually it is advisable that it is
configured to connect to a ZEO server. If using a WSGI server (Zope 2 or Zope
4), set `wsgi_conf_path` accordingly.

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

The executable `zodbsync` provides several subcommands

### `zodbsync record`

The `record` subcommand is used to record objects from the ZODB to the file
system.

Each object is mapped to a folder that contains at least the file
`__meta__` which holds the meta data of the object (properties, permissions etc.). 
If the object contains other objects (like `Folder`s), they are represented as
subfolders. If the object has some sort of source (like `Page Template`s, `DTML
Method`s etc.), it is stored in an additional file. The filename suffix is
taken from the object type and possibly content type, while the base is either
`__source__` or `__source-utf8__`

Only a specific list of object types is supported by `zodbsync`. Objects whose
type is not yet supported are created with a minimal `__meta__` file,
containing only the `title`, `type` and an `unsupported` marker.

If the package `perfact.pfcodechg` is available, an additional option
`--commit` allows to create a `git` commit after the recording, sending a
summary mail containing all changed files to an address specified in the
configuration. This can be used as automated reminder fallback if changes are
not commited timely.

If `zodbsync record` is called with the `--lasttxn` option, it tries to do an
incremental recording, reading all transactions that occured since the last
call (the transaction ID is stored in a file `__last_txn__` in the repository).
The paths to be recorded are extracted from the transaction note, which works
well if editing an object in the management interface of Zope, but not
necessarily if an object is changed from within a script, if it is transferred
by the ZSyncer or if objects are cut and pasted (in the latter case, only the
target of the move operation is recognized).


### `zodbsync watch`

This subcommand starts a process that aims to bypass the shortcomings of
`zodbsync record --lasttxn`.  The process stays alive and builds an object tree
of all objects in the ZODB. Each time it wakes up, it scans for new
transactions, opens the Data.FS directly (in read-only mode) to obtain all
affected object IDs, updates its object tree and uses it to obtain the physical
paths of all affected objects. After finishing, it sleeps for 10 seconds before
waking again. This should provide an almost live recording that does not miss
any changes.


### `zodbsync playback`

The opposite operation to `record` is `playback`, which is able to create and
modify objects in the ZODB from a file system structure as it is created by
`record`. 

By default, `playback` recurses into the subtree below a given
path, removing any superfluous objects and updating existing objects so they
match their file system representation. An exception are objects that are
marked as `unsupported`, which are ignored if found in the ZODB. If only a
given object itself should be updated (properties, security settings etc.),
`--no-recurse` can be used.

### `zodbsync pick`

`pick` requires the base directory to be a git repository and provides a
wrapper for `git cherry-pick`, taking git commits to be applied as arguments.
This is useful if some development has been done in a branch or on a remote
system that has to be deployed to the current system. It then becomes possible
to do something like

    git fetch origin
    zodbsync pick origin/master

to pull the latest commit, apply it to the current repository and upload the
affected paths to the Data.FS. It can also be used to pull multiple commits -
allowing, for example, to pull all commits where the commit message starts with
T12345:

    zodbsync pick $(git log origin/master --reverse --format=%H --grep="^T12345" )

Commit ranges in the form of `COMMIT1..COMMIT2` can also be picked, but be
aware that there is no check that the commit range is actually a straight
forward succession - internally, `git log` is used and therefore any commits
that are reachable from `COMMIT2` but not from `COMMIT1` are picked. In
practice, choosing commits that are not directly connected will result in some
commit not being able to be picked due to conflicts.

If there are unstaged changes at the start of the `pick` operation, these are
first stashed away and restored at the end, but if any of the picked commits
touches any file that was unstaged, it is considered an error and the operation
is cancelled.

## Compatibility
This package replaces similar functionality that was previously found in
`python-perfact` and `perfact-dbutils-zope2`. For backwards compatibility,
those packages were changed so the corresponding calls try to import
`perfact.zodbsync` and use the functionality there, falling back to the
previous implementation if that fails. Corresponding deprecation warnings are
included.

Previous versions contained the scripts `perfact-zoperecord` and
`perfact-zopeplayback` instead of `zodbsync`. For compatibility with systems
automatically calling `perfact-zoperecord`, it is still included but only
providing the bare functionality:

  * `perfact-zoperecord` (corresponds to `zodbsync record --commit /`)
  * `perfact-zoperecord --lasttxn` (corresponds to `zodbsync record --lasttxn`,
    but including a call to `perfact-dbrecord` if a `databases` key is defined
    in the configuration)

## Caveats

Zope allows `External Method`s to be present in the ZODB even if the
corresponding modules are no longer present as extensions. It does not,
however, allow saving such an object. This gives errors if object trees
containing broken `External Method`s are recorded and played back. The same
holds for `Z SQL Method`s which have `class_name` and `class_file` set to a no
longer existing extension.

## To Do / Roadmap

  * Subcommands wrapping `git reset` and `git rebase` will allow development in
    branches, resetting a testing or a production system to the state of an
    approved development branch and rebasing other developments onto the new
    master.
  * Specifying a path to a python script that is executed after `playback`
    inside the same transaction will allow to store database changes in a
    connected relational (SQL) database inside ZODB using Z SQL Methods and
    have them executed as part of the same deployment step.
