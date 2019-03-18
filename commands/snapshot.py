"""
Records a snapshot of a Google Drive file DAG which contains:

   1. all folders;
   2. all files for which an MD5 checksum is defined. Those include Photos/Videos/Music, as well
      as any binary files.
"""
import sys

from drive.api import DriveFolder, Snapshot, ResourcePath, unique as u
from drive.misc import eprint
from drive.serializers import store_snapshot

_ROOT_FOLDER = '**'


def create(service, args):
    store_snapshot(snapshot(service, args.folder), args.output)


def snapshot(service, folder_paths):
    folders = [
        u(ResourcePath.from_name(service, path), path)
        for path in folder_paths
    ]

    entries = []
    for folder in folders:
        if not isinstance(folder, DriveFolder):
            eprint('<%s> is not a folder. Aborting.' % str(folder))
            sys.exit(-1)

        entries.extend(folder.list(recurse=True))

    # Some entries may be scooped in more than once if root folders
    # contain some of the same subfolders (which would imply someone
    # has more than one parent). We discard these spurious duplicates
    # by keeping entries with unique ids.
    unique = set(entries)

    eprint('There were %d entries, %d unique.' % (len(entries), len(unique)))

    return Snapshot(list(unique))


def register_parser(subparsers):
    parser = subparsers.add_parser('snapshot', help='Creates a snapshot of Google Drive metadata.')
    parser.add_argument('folder', help='List of Google Drive paths to fetch info from (e.g. '
                                       '/My Folder/My Nested Folder/). Paths are located with respect to '
                                       '"My Drive". Use "/" to refer to the entire contents of the "My Drive"'
                                       'folder.',
                        metavar='folder',
                        nargs='+')
    parser.add_argument('output', help='Filename where to store the snapshot.')
    parser.set_defaults(func=create)
