#!/usr/bin/env python

import argparse
import cProfile
import json
import os
import pstats
import shutil
import subprocess
import tempfile
import time

from .main import Runner


class ZeoInstance:
    def __init__(self):
        self.path = tempfile.mkdtemp(prefix="zodbsync-bench-zeo-")
        subprocess.check_call(["mkzeoinstance", self.path])

        fname = os.path.join(self.path, "etc", "zeo.conf")
        with open(fname) as f:
            lines = f.readlines()
        subst = "  address " + self.sockpath() + "\n"
        lines = [subst if "  address" in line else line for line in lines]
        with open(fname, "w") as f:
            f.writelines(lines)

        self.zeo = subprocess.Popen([os.path.join(self.path, "bin", "runzeo")])
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
        shutil.rmtree(self.path)


class Repository:
    def __init__(self):
        self.path = tempfile.mkdtemp(prefix="zodbsync-bench-repo-")
        commands = [
            ["init"],
            ["branch", "-m", "benchmark"],
            ["config", "user.email", "benchmark@zodbsync.local"],
            ["config", "user.name", "zodbsync-benchmark"],
        ]
        for cmd in commands:
            subprocess.check_call(["git", "-C", self.path] + cmd)

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


def git_run(repo, *args):
    subprocess.check_call(["git", "-C", repo] + list(args))


def count_repo_objects(root):
    total = 0
    for _, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        total += len(files)
    return total


def populate_dataset(app, depth, breadth, blobs_per_folder, blob_size):
    payload = "x" * blob_size

    total_folders = 0
    total_templates = 0
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
                page_id = f"pt_{level}_{folder_idx}_{blob_idx}"
                child.manage_addProduct["PageTemplates"].manage_addPageTemplate(
                    id=page_id,
                    title=page_id,
                    text=payload,
                )
                total_templates += 1
            stack.append((child, level + 1, prefix + f"{folder_idx}_"))

    return {
        "folders": total_folders,
        "page_templates": total_templates,
        "payload_bytes": blob_size,
    }


def build_runner():
    return Runner()


def runner_cmd(runner, config_path, *cmd):
    return runner.parse("--config", config_path, *cmd)


def benchmark_once(config_path, depth, breadth, blobs_per_folder, blob_size):
    runner = build_runner()

    runner_cmd(runner, config_path, "record", "/").run()

    tm = runner.sync.start_transaction(note="/benchmark-seed")
    stats = populate_dataset(
        app=runner.sync.app,
        depth=depth,
        breadth=breadth,
        blobs_per_folder=blobs_per_folder,
        blob_size=blob_size,
    )
    tm.commit()

    start = time.perf_counter()
    runner_cmd(runner, config_path, "record", "/").run()
    record_seconds = time.perf_counter() - start

    git_run(runner.sync.base_dir, "add", ".")
    try:
        git_run(runner.sync.base_dir, "commit", "-m", "benchmark seed")
    except subprocess.CalledProcessError:
        pass

    object_root = os.path.join(runner.sync.base_dir, runner.sync.site)
    stats["repo_files"] = count_repo_objects(runner.sync.base_dir)
    stats["object_dirs"] = sum(1 for _ in os.walk(object_root))

    runner.sync.tm.abort()
    return stats, record_seconds


def playback_once(config_path):
    runner = build_runner()
    start = time.perf_counter()
    runner_cmd(runner, config_path, "playback", "/").run()
    elapsed = time.perf_counter() - start
    return elapsed, getattr(runner.sync, "_v_last_playback_fs_cache_stats", None)


def profiled_playback_once(
    config_path, profile_prefix, profile_sort="cumulative", profile_lines=30
):
    profiler = cProfile.Profile()
    runner = build_runner()
    start = time.perf_counter()
    profiler.runcall(runner_cmd(runner, config_path, "playback", "/").run)
    elapsed = time.perf_counter() - start

    profiler.dump_stats(profile_prefix + ".prof")
    with open(profile_prefix + ".txt", "w") as f:
        stats = pstats.Stats(profiler, stream=f)
        stats.sort_stats(profile_sort)
        stats.print_stats(profile_lines)

    return elapsed, getattr(runner.sync, "_v_last_playback_fs_cache_stats", None)


def create_environment():
    repo = Repository()
    zeo = ZeoInstance()
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
    seed_env = create_environment()
    try:
        dataset_stats, record_seconds = benchmark_once(
            config_path=seed_env["config"].path,
            depth=args.depth,
            breadth=args.breadth,
            blobs_per_folder=args.blobs_per_folder,
            blob_size=args.blob_size,
        )

        if args.profile_dir:
            os.makedirs(args.profile_dir, exist_ok=True)

        playback_runs = []
        playback_fs_cache_stats = []
        for run_idx in range(args.runs):
            env = create_environment()
            try:
                shutil.copytree(
                    seed_env["repo"].path,
                    env["repo"].path,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git"),
                )
                git_run(env["repo"].path, "add", ".")
                try:
                    git_run(env["repo"].path, "commit", "-m", "benchmark copy")
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
                    )
                else:
                    elapsed, fs_cache_stats = playback_once(env["config"].path)
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
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=str, default="")
    parser.add_argument("--profile-dir", type=str, default="")
    parser.add_argument("--profile-sort", type=str, default="cumulative")
    parser.add_argument("--profile-lines", type=int, default=30)
    args = parser.parse_args()
    run_benchmark(args)


if __name__ == "__main__":
    main()
