import hashlib
import os
import sys
from collections import defaultdict
from os import getcwd
from pathlib import Path
from typing import List, Dict

from commands.snapshot import snapshot
from commands.sync.state import SyncState, LocalFile
from drive.api import DriveFile, unique, ResourcePath, Snapshot, detector
from drive.http import SequentialRequestRunner, ErrorHandlingRunner
from drive.misc import eprint
from drive.serializers import load_snapshot

MEDIA_MIMES = {'image', 'video', 'audio'}


def sync(service, args):
    _sync(service, _new_sync_state(service, args))


def resume_sync(service, args):
    if args.clear:
        _clear_sync_states(service)
    else:
        state = _latest_sync_state(service)
        eprint('Resuming sync from state %s' % state.full_path)
        _sync(service, state)


def _clear_sync_states(service):
    for state in SyncState.sync_states(getcwd(), service):
        state.clear()


def _latest_sync_state(service) -> SyncState:
    path = getcwd()
    states = sorted(list(SyncState.sync_states(path, service)), key=lambda x: x.timestamp, reverse=True)
    if len(states) > 1:
        eprint('More than one sync state found. Will use the most recent.')
    elif len(states) == 0:
        eprint('No sync state found in %s. Nothing to resume.' % path)
        sys.exit(-1)
    return states[0]


def _new_sync_state(service, args) -> SyncState:
    # Compute MD5s for local files and fetches remotes from snapshot.
    eprint('Computing MD5 hashes for files under %s' % args.local)

    local = []
    for local_file in _local_files(args.local):
        if not _mime_allow(local_file, args.include_pictures_only):
            eprint('Exclude file %s with MIME type \'%s\'' % (local_file.path, local_file.mime_type))
        else:
            local.append(local_file)

    if not args.allow_duplicates:
        _check_duplicates(_by_md5(local))

    return SyncState(_cacheable_exclusions(service, args), args, local, getcwd())


def _mime_allow(local_file: LocalFile, pics_only: bool) -> bool:
    # Slicing like this is ugly but spares me from having to check
    # for the presence of subtypes or not.
    return True if not pics_only else local_file.mime_type[0:5] in MEDIA_MIMES


def _sync(service, state: SyncState):
    args = state.args
    local = _by_md5(state.local_files)

    # Read dynamic exclusions.
    state.snapshot = state.snapshot.merge(_uncacheable_exclusions(service, state.args))

    remote_folder = unique(ResourcePath.from_name(service, args.remote), args.remote)
    remote = set(
        entry.md5Checksum
        for entry in state.snapshot.entries
        if isinstance(entry, DriveFile)
    )

    # We sync whatever is missing in the remote MD5 set according to the exclusion lists.
    to_sync = local.keys() - remote
    n_sync = len(to_sync)
    eprint('There are %d local files, %d remote files' % (len(local), len(remote)))
    eprint('%d files will be synced' % n_sync)

    # Uploads are better handled sequentially: they're unsupported by the batch API
    # (https://developers.google.com/drive/api/v3/batch) and are apparently handled sequentially on Google's side
    # (https://stackoverflow.com/questions/10311969/what-is-the-limit-on-google-drive-api-usage).
    # Indeed, since the main bottleneck is probably the client's upload speed anyways, handling concurrent upload
    # requests is probably pointless.
    runner = ErrorHandlingRunner(service, delegate=SequentialRequestRunner)

    for i, key in enumerate(to_sync, start=1):
        local_files = local[key]
        runner.add(request_id=local_files[0].path, request=remote_folder.create_file(local_files[0].path))

    if not state.stored:
        state.store()

    eprint('Uploading %d files.' % n_sync)
    if state.args.dry_run:
        eprint('Dry run: no changes made.')
        return

    for i, result in enumerate(runner.execute(), start=1):
        eprint('%s successfully uploaded to %s (%d of %d)' % (result.id, state.args.remote, i, n_sync))

    state.clear()


def _by_md5(local_files: List[LocalFile]) -> Dict[str, List[LocalFile]]:
    by_md5 = defaultdict(list)
    for local_file in local_files:
        by_md5[local_file.md5_checksum].append(local_file)
    return by_md5


def _local_files(local_folder, recurse=True) -> List[LocalFile]:
    file_paths = os.listdir(local_folder)
    files = []
    folders = []
    for file_path in file_paths:
        full_path = os.path.join(local_folder, file_path)
        if os.path.isdir(full_path):
            folders.append(full_path)
            continue

        eprint('Analyzing %s' % full_path)
        files.append(
            LocalFile(
                path=full_path,
                mime_type=detector.from_file(full_path),
                md5_checksum=hashlib.md5(
                    Path(full_path).read_bytes()
                ).hexdigest()
            )
        )

    if recurse:
        for folder in folders:
            eprint('Recursing into %s' % folder)
            files.extend(_local_files(folder, recurse))

    return files


def _check_duplicates(local):
    duplicates = {k: v for k, v in local.items() if len(v) > 1}
    if len(duplicates) == 0:
        return

    eprint('Error: duplicates found in the local folder. Re-run with --allow-duplicates to '
           'run the synchronization anyway. Duplicates are listed as follows.\n' % local)

    for md5, entries in duplicates.items():
        eprint('------ %s -------' % md5)
        for entry in entries:
            eprint(entry)
        eprint('')

    sys.exit(-1)


def _cacheable_exclusions(service, args) -> Snapshot:
    ss = Snapshot()
    # Stored snapshots and remote folders are cached.
    for _snapshot in args.exclude_snapshot:
        eprint('Reading stored snapshot at <%s>' % _snapshot)
        ss = ss.merge(load_snapshot(service, _snapshot))

    for remote_folder in args.exclude_folder:
        eprint('Examining contents of Google Drive folder <%s>' % remote_folder)
        ss = ss.merge(snapshot(service, [remote_folder]))
    return ss


def _uncacheable_exclusions(service, args) -> Snapshot:
    ss = Snapshot()
    eprint('Examining contents of Google Drive folder <%s>' % args.remote)
    ss = ss.merge(snapshot(service, [args.remote]))
    return ss


def register_parser(subparsers):
    parser = subparsers.add_parser('sync', help='Syncs a local folder with Google Drive by uploading '
                                                'files with no corresponding MD5 in Drive.')

    parser.add_argument('local', help='A local folder.')
    parser.add_argument('remote', help='an *existing* remote path in Google Drive (i.e. you must create the target '
                                       'folder yourself). Only paths in "My Drive" are currently supported.')

    parser.add_argument('--exclude-snapshot',
                        help='any files present in the specified snapshot (created with the _snapshot_ command)'
                             'will be excluded from sync',
                        nargs='*', default=[])

    parser.add_argument('--exclude-folder', help='any files present under the specified Google Drive folder '
                                                 'or one of its subfolders will be excluded from sync. '
                                                 'To exclude all files already in Google Drive, use --exclude-folder /',
                        nargs='*', default=[])

    parser.add_argument('--include-recursive',
                        help='Recursively includes files present within subfolders of the *local* '
                             'folder into the sync. Note that the sync will *not* preserve nestings '
                             'when copying to Google Drive: it will simply copy all files into the '
                             'remote folder, directly, without creating subfolders.',
                        action='store_true')

    parser.add_argument('--include-pictures-only',
                        help='Includes only files with video/audio/image MIME types'
                             'into the sync.',
                        action='store_true')

    parser.add_argument('--allow-duplicates', help='Allows duplicate files in the local folder. By default, sync will'
                                                   'abort with an error if a duplicate is found.',
                        action='store_true'),

    parser.add_argument('--dry-run', help='Dry run (does not apply changes to Google Drive).',
                        action='store_true')

    parser.set_defaults(func=sync)

    resume = subparsers.add_parser('resume', help='Resumes an ongoing sync.')

    resume.add_argument('--clear', help='Clears cached data for all pending syncs.', action='store_true')

    resume.set_defaults(func=resume_sync)
