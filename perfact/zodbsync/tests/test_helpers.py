import perfact.zodbsync.helpers as helpers


def test_remove_redundant_paths():
    """
    Check that redundant subpaths are actually removed
    """
    paths = [
        '/test',
        '/test/sub',
        '/another',
    ]
    target = [
        '/another',
        '/test',
    ]
    helpers.remove_redundant_paths(paths)
    assert paths == target


def test_remove_redundant_paths_only_real_subpaths():
    """
    Check that paths are only recognized as redundant if they are actually
    subpaths, not if the last path component starts with the other.
    """
    paths = ['/test', '/test2']
    new_paths = paths[:]
    helpers.remove_redundant_paths(new_paths)
    assert paths == new_paths
