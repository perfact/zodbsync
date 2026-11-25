#!/usr/bin/env python

import argparse
import contextlib
import cProfile
import json
import logging
import os
import pstats
import shutil
import subprocess
import tempfile
import time

from .helpers import literal_eval
from .main import Runner


@contextlib.contextmanager
def muted_stdio(enabled=True):
    if not enabled:
        yield
        return

    with open(os.devnull, "w") as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            yield


@contextlib.contextmanager
def muted_logger(logger, enabled=True, level=logging.ERROR):
    if not enabled or logger is None:
        yield
        return

    old_level = logger.level
    old_disabled = logger.disabled
    old_propagate = logger.propagate
    old_handler_levels = [handler.level for handler in logger.handlers]
    logger.disabled = False
    logger.setLevel(level)
    logger.propagate = False
    for handler in logger.handlers:
        handler.setLevel(level)
    try:
        yield
    finally:
        logger.setLevel(old_level)
        logger.disabled = old_disabled
        logger.propagate = old_propagate
        for handler, handler_level in zip(logger.handlers, old_handler_levels):
            handler.setLevel(handler_level)


class ZeoInstance:
    def __init__(self, quiet=True):
        self.path = tempfile.mkdtemp(prefix="zodbsync-bench-zeo-")
        subprocess.check_call(
            ["mkzeoinstance", self.path],
            **subprocess_stdio(quiet),
        )

        fname = os.path.join(self.path, "etc", "zeo.conf")
        with open(fname) as f:
            lines = f.readlines()
        subst = "  address " + self.sockpath() + "\n"
        lines = [subst if "  address" in line else line for line in lines]
        with open(fname, "w") as f:
            f.writelines(lines)

        self._devnull = open(os.devnull, "w") if quiet else None
        self.zeo = subprocess.Popen(
            [os.path.join(self.path, "bin", "runzeo")],
            stdout=self._devnull if quiet else None,
            stderr=self._devnull if quiet else None,
        )
        self._wait_ready()

    def sockpath(self):
        return os.path.join(self.path, "var", "zeo.sock")

    def _wait_ready(self, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(self.sockpath()):
                return
            if self.zeo.poll() is not None:
                raise RuntimeError("runzeo terminated during startup")
            time.sleep(0.1)
        raise RuntimeError("Timed out waiting for runzeo socket")

    def cleanup(self):
        self.zeo.terminate()
        self.zeo.wait()
        if self._devnull is not None:
            self._devnull.close()
        shutil.rmtree(self.path)


class Repository:
    def __init__(self, quiet=True):
        self.path = tempfile.mkdtemp(prefix="zodbsync-bench-repo-")
        commands = [
            ["init"],
            ["branch", "-m", "benchmark"],
            ["config", "user.email", "benchmark@zodbsync.local"],
            ["config", "user.name", "zodbsync-benchmark"],
        ]
        for cmd in commands:
            git_run(self.path, *cmd, quiet=quiet)

    def cleanup(self):
        shutil.rmtree(self.path)


class ZopeConfig:
    def __init__(self, zeosock):
        self.path = tempfile.mkdtemp(prefix="zodbsync-bench-zope-")
        self.config = os.path.join(self.path, "zope.conf")
        content = """
%define INSTANCE {path}
%define ZEO_SERVER {zeosock}

instancehome $INSTANCE

<zodb_db main>
    <zeoclient>
      server $ZEO_SERVER
      storage 1
      name zeostorage
      var $INSTANCE/var
      cache-size 20MB
    </zeoclient>
   mount-point /
</zodb_db>
        """.format(zeosock=zeosock, path=self.path)

        with open(self.config, "w") as f:
            f.write(content)

    def cleanup(self):
        shutil.rmtree(self.path)


class ZODBSyncConfig:
    def __init__(self, repo, zopeconfig, zeopath):
        self.folder = tempfile.mkdtemp(prefix="zodbsync-bench-config-")
        os.mkdir(os.path.join(self.folder, "layers"))
        self.path = os.path.join(self.folder, "zodb.py")
        with open(self.path, "w") as f:
            f.write(
                """
conf_path = '{zopeconf}'
datafs_path = '{zeopath}/var/Data.fs'
manager_user = 'perfact'
create_manager_user = True
default_owner = 'perfact'
base_dir = '{repodir}'
commit_name = "Zope Developer"
commit_email = "zope-devel@example.de"
commit_message = "Benchmark snapshot."
layers = "{root}/layers"
                """.format(
                    zopeconf=zopeconfig,
                    zeopath=zeopath,
                    repodir=repo,
                    root=self.folder,
                )
            )

    def cleanup(self):
        shutil.rmtree(self.folder)


def git_output(repo, *args):
    return subprocess.check_output(
        ["git", "-C", repo] + list(args),
        universal_newlines=True,
    )


def subprocess_stdio(quiet):
    if not quiet:
        return {}
    return {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }


def git_run(repo, *args, quiet=True):
    subprocess.check_call(
        ["git", "-C", repo] + list(args),
        **subprocess_stdio(quiet),
    )


def count_repo_objects(root):
    total = 0
    for _, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        total += len(files)
    return total


def copy_seed_repo(source, target):
    for entry in os.listdir(source):
        if entry == ".git":
            continue
        src_path = os.path.join(source, entry)
        dst_path = os.path.join(target, entry)
        if os.path.isdir(src_path):
            shutil.copytree(src_path, dst_path, dirs_exist_ok=True)
        else:
            shutil.copy2(src_path, dst_path)


def analyze_recorded_repo(repo_root):
    object_root = os.path.join(repo_root, "__root__")
    stats = {
        "folders": 0,
        "page_templates": 0,
        "python_scripts": 0,
        "sql_methods": 0,
        "other_objects": 0,
        "payload_bytes": None,
    }
    type_map = {
        "Folder": "folders",
        "Folder (Ordered)": "folders",
        "Page Template": "page_templates",
        "Script (Python)": "python_scripts",
        "Z SQL Method": "sql_methods",
    }
    for root, _, files in os.walk(object_root):
        if "__meta__" not in files:
            continue
        with open(os.path.join(root, "__meta__"), "rb") as f:
            meta = dict(literal_eval(f.read()))
        stats[type_map.get(meta.get("type"), "other_objects")] += 1
    return stats


def dataset_object_types(object_type):
    if object_type == "mixed":
        return ("page_template", "python_script")
    if object_type == "folders":
        return ()
    return (object_type,)


def build_payload(object_type, blob_size):
    if object_type == "page_template":
        return "x" * blob_size
    if object_type == "python_script":
        return (
            "## Script (Python)\n"
            "##bind container=container\n"
            "##bind context=context\n"
            "##bind namespace=\n"
            "##bind script=script\n"
            "##bind subpath=traverse_subpath\n"
            "##parameters=\n"
            "##title=\n"
            "##\n"
            'return "' + ("x" * blob_size) + '"\n'
        )
    if object_type == "sql_method":
        return (
            "<dtml-comment>\n"
            "benchmark sql method\n"
            "</dtml-comment>\n"
            "SELECT '" + ("x" * blob_size) + "' AS payload\n"
        )
    raise ValueError(f"Unsupported benchmark object type: {object_type}")


def populate_object(parent, object_type, obj_id, payload):
    if object_type == "page_template":
        parent.manage_addProduct["PageTemplates"].manage_addPageTemplate(
            id=obj_id,
            title=obj_id,
            text=payload,
        )
        return "page_templates"
    if object_type == "python_script":
        parent.manage_addProduct["PythonScripts"].manage_addPythonScript(
            id=obj_id,
            title=obj_id,
            file=payload,
        )
        return "python_scripts"
    if object_type == "sql_method":
        parent.manage_addProduct["ZSQLMethods"].manage_addZSQLMethod(
            id=obj_id,
            title=obj_id,
            connection_id="benchmark_db",
            arguments="",
            template=payload,
        )
        return "sql_methods"
    raise ValueError(f"Unsupported benchmark object type: {object_type}")


def populate_dataset(
    app,
    depth,
    breadth,
    blobs_per_folder,
    blob_size,
    object_type,
):
    object_types = dataset_object_types(object_type)
    payloads = {
        current_type: build_payload(current_type, blob_size)
        for current_type in object_types
    }

    total_folders = 0
    total_objects = {
        "page_templates": 0,
        "python_scripts": 0,
        "sql_methods": 0,
    }
    stack = [(app, 0, "")]
    while stack:
        parent, level, prefix = stack.pop()
        if level >= depth:
            continue
        for folder_idx in range(breadth):
            folder_id = f"{prefix}f{level}_{folder_idx}"
            parent.manage_addProduct["OFSP"].manage_addFolder(id=folder_id)
            child = getattr(parent, folder_id)
            total_folders += 1
            for blob_idx in range(blobs_per_folder):
                current_type = object_types[blob_idx % len(object_types)]
                obj_prefix = {
                    "page_template": "pt",
                    "python_script": "py",
                    "sql_method": "sql",
                }[current_type]
                obj_id = f"{obj_prefix}_{level}_{folder_idx}_{blob_idx}"
                stat_key = populate_object(
                    child,
                    current_type,
                    obj_id,
                    payloads[current_type],
                )
                total_objects[stat_key] += 1
            stack.append((child, level + 1, prefix + f"{folder_idx}_"))

    return {
        "folders": total_folders,
        **total_objects,
        "payload_bytes": blob_size,
    }


def build_runner():
    return Runner()


def runner_cmd(runner, config_path, *cmd):
    return runner.parse("--config", config_path, *cmd)


def playback_command(runner, config_path, override=False):
    cmd = ["playback"]
    if override:
        cmd.append("--override")
    cmd.append("/")
    return runner_cmd(runner, config_path, *cmd)


def benchmark_once(
    config_path,
    depth,
    breadth,
    blobs_per_folder,
    blob_size,
    object_type,
    seed_repo="",
    quiet=True,
):
    runner = build_runner()

    if seed_repo:
        runner_cmd(runner, config_path, "playback", "/")
        start = time.perf_counter()
        copy_seed_repo(seed_repo, runner.sync.base_dir)
        record_seconds = time.perf_counter() - start
        stats = analyze_recorded_repo(runner.sync.base_dir)
    else:
        with muted_stdio(quiet), muted_logger(runner.logger, quiet):
            command = runner_cmd(runner, config_path, "record", "/")
            command.run()

        with muted_stdio(quiet), muted_logger(runner.logger, quiet):
            tm = runner.sync.start_transaction(note="/benchmark-seed")
            stats = populate_dataset(
                app=runner.sync.app,
                depth=depth,
                breadth=breadth,
                blobs_per_folder=blobs_per_folder,
                blob_size=blob_size,
                object_type=object_type,
            )
            tm.commit()

        start = time.perf_counter()
        with muted_stdio(quiet), muted_logger(runner.logger, quiet):
            command = runner_cmd(runner, config_path, "record", "/")
            command.run()
        record_seconds = time.perf_counter() - start

    git_run(runner.sync.base_dir, "add", ".", quiet=quiet)
    try:
        git_run(
            runner.sync.base_dir,
            "commit",
            "-m",
            "benchmark seed",
            quiet=quiet,
        )
    except subprocess.CalledProcessError:
        pass

    object_root = os.path.join(runner.sync.base_dir, runner.sync.site)
    stats["repo_files"] = count_repo_objects(runner.sync.base_dir)
    stats["object_dirs"] = sum(1 for _ in os.walk(object_root))

    with muted_stdio(quiet), muted_logger(runner.logger, quiet):
        runner.sync.tm.abort()
    return stats, record_seconds


def playback_once(config_path, override=False, quiet=True):
    runner = build_runner()
    start = time.perf_counter()
    with muted_stdio(quiet), muted_logger(runner.logger, quiet):
        command = playback_command(runner, config_path, override=override)
        command.run()
    elapsed = time.perf_counter() - start
    return elapsed, getattr(runner.sync, "_v_last_playback_fs_cache_stats", None)


def profiled_playback_once(
    config_path,
    profile_prefix,
    profile_sort="cumulative",
    profile_lines=30,
    override=False,
    quiet=True,
):
    profiler = cProfile.Profile()
    runner = build_runner()
    start = time.perf_counter()
    with muted_stdio(quiet), muted_logger(runner.logger, quiet):
        command = playback_command(runner, config_path, override=override)
        profiler.runcall(command.run)
    elapsed = time.perf_counter() - start

    profiler.dump_stats(profile_prefix + ".prof")
    with open(profile_prefix + ".txt", "w") as f:
        stats = pstats.Stats(profiler, stream=f)
        stats.sort_stats(profile_sort)
        stats.print_stats(profile_lines)

    return elapsed, getattr(runner.sync, "_v_last_playback_fs_cache_stats", None)


def create_environment(quiet=True):
    repo = Repository(quiet=quiet)
    zeo = ZeoInstance(quiet=quiet)
    zopeconfig = ZopeConfig(zeosock=zeo.sockpath())
    zodbconfig = ZODBSyncConfig(
        repo=repo.path,
        zopeconfig=zopeconfig.config,
        zeopath=zeo.path,
    )
    return {
        "repo": repo,
        "zeo": zeo,
        "zopeconfig": zopeconfig,
        "config": zodbconfig,
    }


def cleanup_environment(env):
    for key in ["config", "zopeconfig", "zeo", "repo"]:
        env[key].cleanup()


def run_benchmark(args):
    quiet = not args.verbose
    seed_env = create_environment(quiet=quiet)
    try:
        dataset_stats, record_seconds = benchmark_once(
            config_path=seed_env["config"].path,
            depth=args.depth,
            breadth=args.breadth,
            blobs_per_folder=args.blobs_per_folder,
            blob_size=args.blob_size,
            object_type=args.object_type,
            seed_repo=args.seed_repo,
            quiet=quiet,
        )

        if args.profile_dir:
            os.makedirs(args.profile_dir, exist_ok=True)

        playback_runs = []
        playback_fs_cache_stats = []
        for run_idx in range(args.runs):
            env = create_environment(quiet=quiet)
            try:
                shutil.copytree(
                    seed_env["repo"].path,
                    env["repo"].path,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git"),
                )
                git_run(env["repo"].path, "add", ".", quiet=quiet)
                try:
                    git_run(
                        env["repo"].path,
                        "commit",
                        "-m",
                        "benchmark copy",
                        quiet=quiet,
                    )
                except subprocess.CalledProcessError:
                    pass
                if args.profile_dir:
                    profile_prefix = os.path.join(
                        args.profile_dir, f"playback-run-{run_idx + 1}"
                    )
                    elapsed, fs_cache_stats = profiled_playback_once(
                        env["config"].path,
                        profile_prefix=profile_prefix,
                        profile_sort=args.profile_sort,
                        profile_lines=args.profile_lines,
                        override=args.playback_override,
                        quiet=quiet,
                    )
                else:
                    elapsed, fs_cache_stats = playback_once(
                        env["config"].path,
                        override=args.playback_override,
                        quiet=quiet,
                    )
                playback_runs.append(elapsed)
                playback_fs_cache_stats.append(fs_cache_stats)
            finally:
                cleanup_environment(env)
    finally:
        cleanup_environment(seed_env)

    result = {
        "depth": args.depth,
        "breadth": args.breadth,
        "blobs_per_folder": args.blobs_per_folder,
        "blob_size": args.blob_size,
        "object_type": args.object_type,
        "seed_repo": os.path.abspath(args.seed_repo) if args.seed_repo else "",
        "runs": args.runs,
        "record_seconds": record_seconds,
        "playback_seconds": playback_runs,
        "playback_min_seconds": min(playback_runs),
        "playback_max_seconds": max(playback_runs),
        "playback_avg_seconds": sum(playback_runs) / len(playback_runs),
        "playback_fs_cache_stats": playback_fs_cache_stats,
        "dataset": dataset_stats,
        "git_head": git_output(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "rev-parse",
            "HEAD",
        ).strip(),
    }

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2, sort_keys=True)
            f.write("\n")

    print(json.dumps(result, indent=2, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark initial zodbsync playback into a fresh Data.fs.",
    )
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--breadth", type=int, default=5)
    parser.add_argument("--blobs-per-folder", type=int, default=5)
    parser.add_argument("--blob-size", type=int, default=4096)
    parser.add_argument(
        "--seed-repo",
        type=str,
        default="",
        help=(
            "Copy an existing recorded repository tree into the benchmark repo "
            "instead of generating a synthetic dataset."
        ),
    )
    parser.add_argument(
        "--object-type",
        choices=[
            "page_template",
            "python_script",
            "sql_method",
            "mixed",
            "folders",
        ],
        default="page_template",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=str, default="")
    parser.add_argument("--profile-dir", type=str, default="")
    parser.add_argument("--profile-sort", type=str, default="cumulative")
    parser.add_argument("--profile-lines", type=int, default=30)
    parser.add_argument(
        "--playback-override",
        action="store_true",
        help="Pass --override to playback so recorded type mismatches are replaced.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show setup, git, and playback progress output during benchmark runs.",
    )
    args = parser.parse_args()
    run_benchmark(args)


if __name__ == "__main__":
    main()
