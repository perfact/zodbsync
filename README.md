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

## Building and publishing
In general, the PyPi access token is personalized, so publishing can only be
done by one person (currently Viktor). Scratch tokens are stored in the PerFact
vault so this can be changed in the case of an emergency.

See https://packaging.python.org/en/latest/tutorials/packaging-projects/.

The username `__token__` and the password from pypi needs to be stored to
`~/.pypirc`. Then:

    virtualenv venv
    venv/bin/pip install build twine
    venv/bin/python -m build
    venv/bin/twine upload dist/*

## Installation

The package should be installed using `pip` in the same virt-env as `zope`, p.e.

    zope/bin/pip install perfact-zodbsync

On PerFact systems, it should automatically be pulled by the `requirements.txt`
of the package `perfact-dbutils-zope4` and included there.

On newer PerFact Zope4 installations, install `test` branch via, e.g. for
development/testing purposes

    sudo -H /usr/share/perfact/zope4/bin/pip install git+https://github.com/perfact/zodbsync@test --upgrade

If the `setuptools` version used by the Zope virtualenv is too old (for
example, on Ubuntu 20.04 or 22.04), you need to build the package in a separate
virtualenv using a new `setuptools` version and then install it:

    virtualenv build-venv
    build-venv/bin/pip install 'setuptools>=61.2' build
    build-venv/bin/python -m build
    sudo -H /usr/share/perfact/zope4/bin/pip install dist/$(ls -t dist/*.tar.gz | head -n 1)

Note that executing the tests requires ODBC C headers to be installed. On
Debian-like systems, install the package `unixodbc-dev`.

## Configuration

Use the `config.py` as a starting point for your configuration. At the moment,
it is not automatically installed. The canonical path for the configuration is
`/etc/perfact/modsync/zodb.py`, so if you do not want to supply the path to
the configuration when calling the scripts, copy the configuration file there.

The most important settings are:
### `conf_path` or `wsgi_conf_path`
Set `conf_path` or `wsgi_conf_path` to the `zope.conf` of your Zope instance.
If it is a standalone instance exclusively accessing its ZODB, it must be
powered down if used by zodbsync. So usually it is advisable that it is
configured to connect to a ZEO server.

The two options are present due to a no longer relevant difference between
`ZServer` and `WSGI` instance handling and can now be used interchangeably.

### `base_dir`
Inside this folder (actually, in a subfolder named `__root__`), the serialized
objects are placed.

### `manager_user`
The name of a user that must be defined in the top-level `UserFolder`
(`acl_users`) and which has Manager permissions. Transactions that are used to
upload objects to the ZODB are done by this user.

### `default_owner`
This user is considered to be the default owner of objects if no other
information is stored in the object.

### `force_default_owner`
Can be combined with `default_owner` to enforce a specific owner for objects
in the ZODB.

### `datafs_path`
The path to the location of the Data.fs file. This is needed for `zodbsync
watch`.

### `run_after_playback`
Path to a script that is executed after a successful (non-recursive) playback,
including indirect calls from `reset` or `pick`. If the script exists, it is
called and fed the list of changed objects in a JSON format.

### `playback_hook`
Path to script which is called to define the phases of playback to be
executed, Recieves a json dictionary in the form of `{"paths": [...]}`
and should output a json dictionary in the form of

```json
[
  {
    "paths": [...],
    "cmd": [...]
  },
  {
    "paths": [...],
  }
]
```

### `layers`
Path to folder on the filesystem that contains layer definitions. See below.

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

An additional option
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
`zodbsync record --lasttxn`. The process stays alive and builds an object tree
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

### `zodbsync exec`

This command requires the base directory to be a git repository and provides a
wrapper for several git commands. It executes the given command, reads the
objects changed between the old and new `HEAD` and plays them back. Any
unstaged changes are stashed away and restored afterwards. The operation is
aborted and rolled back if it results in a broken state (an interrupted
`merge`, `rebase`, `cherry-pick` etc.) or if there is an overlap between the
unstaged and the changed files.

This allows commands like the following:

    zodbsync exec "git cherry-pick COMMIT"
    zodbsync exec "git checkout otherbranch"
    zodbsync exec "git reset --hard COMMIT"
    zodbsync exec "git revert COMMIT"

### `zodbsync reset`

Resets one or more layer repos to target commits and plays back the union of
changed paths in a single pass.

```
zodbsync reset [--dry-run] [--skip-errors] <commit>
zodbsync reset [--dry-run] [--skip-errors] <ident>:<commit> [<ident>:<commit> ...]
```

The bare `<commit>` form is a shorthand for `zodbsync exec "git reset --hard
<commit>"` and operates on the fallback layer only.

The `<ident>:<commit>` form resets the named layer identified by `<ident>` to
the given commit. Multiple targets may be listed; they are reset in order and
the union of all changed paths is played back in a single pass. An empty
`<ident>` addresses the fallback layer.

Multi-layer reset is all-or-nothing: if any step fails, every already-reset
layer is rolled back to its original commit. Unstaged changes in each target
layer are stashed before resetting and restored on success or failure.

### `zodbsync checkout`

Wrapper for `git checkout` with some of its functionality.

### `zodbsync pick`

As a special case of `exec`, this wraps `git cherry-pick` and takes git commits
to be applied as arguments.
This is useful if some development has been done in a branch or on a remote
system that has to be deployed to the current system. It then becomes possible
to do something like

    git fetch origin
    zodbsync pick origin/master

to pull the latest commit, apply it to the current repository and upload the
affected paths to the Data.FS. It can also be used to pick multiple commits.
Its argument `--grep` allows, for example, to pull all commits where the commit
message starts with T12345:

    zodbsync pick --grep="^T12345" source/master

Similarly, `--since` and `--until` can be used to limit commits - see the `git
log` documentation.
Commit ranges in the form of `COMMIT1..COMMIT2` can also be picked, but be
aware that there is no check that the commit range is actually a straight
forward succession - internally, `git log` is used and therefore any commits
that are reachable from `COMMIT2` but not from `COMMIT1` are picked. In
practice, choosing commits that are not directly connected will result in some
commit not being able to be picked due to conflicts and a rollback of the
operation.

The optional `--layer <ident>` flag cherry-picks into the named layer's
`workdir` instead of the fallback layer. The named layer's workdir must be
clean before picking; dirty workdirs cause an immediate error with no
cherry-pick attempted. Changed paths are collected from the named layer's git
diff and played back as usual. The fallback layer's repo and workdir are
untouched.


### `zodbsync upload` (DEPRECATED)

`upload` expects the base directory to be a git repository and provides a tool
to upload JS and CSS libraries into the `Data.fs`. This is achieved by converting
these files into files and directories understood by `playback` and placing them
in the specified directory inside of `base_dir`.

Example to upload bootstrap:

    zodbsync upload /tmp/bootstrap lib/bootstrap

This subcommand is deprecated because external libraries should not be put into
the Data.FS. Instead, it is more efficient if they are served directly from the
file system.

### `zodbsync with-lock`

If some combination of `git` and `zodbsync` operations is not yet covered by a
wrapper subcommand, it is possible to use `zodbsync with-lock` to execute a
series of commands while still making sure that no other similar operation
interferes. Any `zodbsync` command used as part of this must then use the
option `--no-lock`. For example:

    zodbsync with-lock "git rebase origin/main && zodbsync --no-lock playback /"

Although this particular example can now be better achieved with `zodbsync
exec`.

### `zodbsync reformat`

With version 4.3.2, the formatting of meta files was changed to become more
diff-friendly, placing, for example, lists of roles for a specific permission
onto one line each. When transferring commits from a system that used the old
recording to one that uses the new one, `zodbsync reformat` can be used to
rewrite commits of the old to the new version.

Use a separate repository clone, check out the starting point and pick the
commits that used the old formatting on top of it. Executing `reformat` will
add a commit that reformats the complete repository after the starting point,
followed by rewritten commits that correspond to the original ones, but using
the new formatting. Finally, pick these commits onto the target system.
Detailed steps:

  * find the commit ID of the first commit you want to reformat,
    this ID will be referred to as `START`

  * from the source branch or system, check out the commit before `START`
    and create a work branch
    * `git checkout START~`
    * `git checkout -b <work-branch>`

  * pick the commits to be reformatted into the work branch
    * `git cherry-pick -Xno-renames <commit-ids ...>`

  * run `reformat`: this will create a commit between `START~` and `START`
    containing the reformatting of the entire repo from old to new format and
    applies the following commits as if they had been committed using the new
    format
    * `zodbsync reformat START~`

  * if the project also contains commits made after the format change,
    `cherry-pick` them into the work branch now

  * push the work branch to the target system and `zodbsync pick` the commits
    (except the `zodbsync reformat` commit)

Hint: This requires `git` in version 2.22 or above.

## Layer handling

Since version 23.1.0, `zodbsync` has gained the possibility to handle multiple
file system source trees that each contribute a separate layer to the objects
in the Data.FS. Layer handling was reworked for 23.4.0 and the following
describes the new handling.

The configuration option `layers` points to a folder on the file system that
contains separate configuration files or symlinks that may be contributed by
different layer packages. These are read alphabetically from the bottom-most to
the top-most layer and provide the following options:

### `workdir`
A path where the layer is placed. This needs to be owned by the user that
executes `zodbsync`. It will be initialized as a git repository if it is not
already one.

### `source`
This path provides the objects of a layer, possibly read-only to the user
executing `zodbsync` and provided by a Debian package or similar. Must be a
directory containing a `__root__` subdirectory or `__root__.tar*` file.
Additionally, it can contain further folders or compressed files that will also
be copied over or unpacked to the workdir.

An implicit fallback layer is added at the top where `workdir` is set to the
`base_dir` provided in the main config for compatibility with a non-layered
setup and without a `source`.

The representation rules for objects in a multi-layer setup are as follows:

- Each object is defined by the topmost layer that provides a `__meta__` file
  for it.
- If a folder in some layer contains a `__frozen__` file, both the folder and
  any subobjects from any layer below this are ignored - the layer is expected
  to fully replicate the intended state of the folder and anything below it.
- If a folder contains a file named `__deleted__`, the object is supposed to be
  deleted even if it is defined in some layer below. It must not also contain a
  meta or source file or any subdirectories. It may additionally contain a
  `__frozen__` file, which makes a difference if the object reappears and
  controls if the lower layers are considered or not.
- Layers can define subobjects without defining their parent objects, with the
  assumption that some lower layer dependency will provide the parent object.
  However, if some subobject is defined while no active layer provides the
  parent, re-recording will remove it.

### Layer resolution in `record` and `watch`

When `record` or `watch` writes an object, it determines the target layer using
the following priority order:

1. `obj.zodbsync_layer` attribute on the ZODB object (if set — authoritative)
2. The highest-priority layer that already has a `__meta__` file for this object
3. The layer where the parent object is recorded
4. The fallback layer (ultimate fallback — only reached for root-level objects
   with no prior recording)

When an object is deleted:

- If it exists in only one layer: its files are removed, no marker is needed.
- If it exists in multiple layers: a `__deleted__` marker is placed in the
  topmost layer that held the object, shadowing any lower-layer definitions.

When `record` or `watch` detects that the above rules would write to a different
layer than where the object currently lives (layer divergence), the object is
moved to the correct layer and the old location is cleaned up.

The following subcommands for `zodbsync` provide layer handling:

### `layer-init`
Initialize all given layers by initializing the `workdir` and
copying/extracting all objects there. This does *not* play back anything into
the Data.FS. It is intended for the use case where code that was previously
part of one layer (maybe the fallback layer) is now to be provided by a
separate layer. Afterwards, a `record` call can be used to clear up the
duplicates present in both layers.

### `layer-update`
For each given layer, any unstaged changes in the `workdir` are committed and
the layer content is reset to that found in the `source`. This is used to
update a layer to a newer version. Note that any changes done directly in the
layer since the last update are overwritten by this - they can still be found
in the `git` history in the `workdir`, but the working directory and the
resulting Data.FS content are reset. It is therefore possible to pre-apply
changes that will be part of the next release, but if there is a change that is
not yet merged upstream, the layer should not be updated until it is.

### `zodbsync move`

```
zodbsync move <zope-path> <layer-ident> [--no-recurse]
```

Moves an object's filesystem representation from its current layer to a target
layer identified by `<layer-ident>`. The fallback layer is addressed with an
empty string (`""`). Recursive by default; `--no-recurse` limits the operation
to the named object. Updates `obj.zodbsync_layer` in the ZODB. Descendants
whose `zodbsync_layer` already differs from the source layer are skipped,
preserving intentional cross-layer assignments. Does not create git commits.

Typical use: promote a fallback-layer object into a named layer, or demote a
named-layer object back to the fallback layer.

### `zodbsync copy`

```
zodbsync copy <zope-path> <layer-ident> [--no-recurse]
```

Copies an object's current filesystem state to a target layer, then resets the
source layer's workdir to its last git-committed state (`git checkout HEAD`).
Updates `obj.zodbsync_layer` to the target layer. Does **not** place a
`__frozen__` marker automatically. Recursive by default; `--no-recurse` limits
the operation to the named object. Does not create git commits.

Intended use: the source layer has uncommitted changes. After copy, the source
resets to HEAD, creating an immediate content difference between target and
source — this prevents the compression pass from collapsing the copy on the
next `record`/`watch` cycle.

**Known limitation:** if the source is already at HEAD (no uncommitted changes),
the copy is content-identical to the source and compression will collapse it on
the next `record`/`watch` cycle, reverting `obj.zodbsync_layer` back to the
source layer. To prevent this, either edit the object in Zope before the next
`record`/`watch` run, or place a `__frozen__` marker manually in the target
layer's workdir.

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

Since `22.2.5` a more recent version ( `>= 3.26.0` ) of `tox` is required
in order to build the test environment from `pyproject.toml` instead of
`setup.py`. Do NOT get fooled by errors like `ERROR: No setup.py file found.`,
just upgrade `tox` to latest version and retry.

## To Do / Roadmap

### Migration path for non-layered systems

Systems that have not yet adopted layers, but whose objects partially overlap
with objects provided by an upcoming named layer, need a migration tool. The
intended workflow is roughly:

1. Do a merge-based upgrade to obtain the list of deviating object paths.
2. Add `__frozen__` markers where the local version should take precedence.
3. Remove superfluous duplicates from the fallback layer and initialize the new
   named layer.

No dedicated command exists for this yet; the steps can be done manually using
`record`, `move`, `copy`, and direct filesystem operations.
