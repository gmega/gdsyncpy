import json
from collections import defaultdict
from typing import List, Tuple, Dict

from commands.snapshot import snapshot
from drive.api import DriveFile, Snapshot, resolve_paths, ResourcePath, Resource
from drive.http import ErrorHandlingRunner, GAPIBatchRunner
from drive.misc import eprint
from drive.serializers import load_snapshot


def dedup_list(service, args):
    eprint('Computing duplicates and resolving resource paths.')
    duplicates, paths = compute_duplicates(service, get_snapshot(service, args))
    summary = {
        md5: [
            {
                'id': duplicate.id,
                'path': str(paths[duplicate])
            }
            for duplicate in entries
        ] for md5, entries in duplicates.items()
    }

    if len(duplicates) == 0:
        eprint('Hooray! There are no duplicates in the snapshot.')
    else:
        eprint('Duplicates were found.')

    if args.json:
        print(json.dumps(summary, indent=3))
    else:
        for md5, entries in summary.items():
            eprint('------ %s -------' % md5)
            for entry in entries:
                eprint('%s (%s)' % (entry['path'], entry['id']))
            eprint('')


def dedup_apply(service, args):
    eprint('Now computing and removing duplicates. Prefix order is %s.' % args.prefixes)
    duplicates, paths = compute_duplicates(
        service, get_snapshot(service, args)
    )

    # Strips white spaces.
    prefixes = [prefix.strip() for prefix in args.prefixes.split(',')]
    # Adds trailing backlash to make prefixes unique.
    prefixes = [prefix + ('/' if not prefix.endswith('/') else '') for prefix in prefixes]

    runner = ErrorHandlingRunner(service, delegate=GAPIBatchRunner)
    for md5, entries in duplicates.items():
        preferences = list(zip(entries, list(rank(prefixes, [paths[entry] for entry in entries]))))
        for duplicate, _ in sorted(preferences, key=lambda x: x[1])[1:]:
            rid = '%s (%s)' % (paths[duplicate], duplicate.id)
            eprint('Queue request for deleting duplicate %s' % rid)
            runner.add(request_id=rid, request=duplicate.delete())

    if not args.dry_run:
        eprint('\n --- Now running %d deletion requests in batch.' % len(runner.requests))
        for rid, result, _ in runner.execute():
            eprint('Successfully deleted %s' % rid)
    else:
        eprint('Dry run. No changes applied.')


def rank(prefix_preferences, paths: List[ResourcePath]):
    for path in paths:
        for i, prefix in enumerate(prefix_preferences):
            if path.startswith(prefix):
                yield i
                break
        else:
            # If the path does not match any of the prefixes, we throw an error. This can be painful to the
            # user but it's better than taking an arbitrary decision for either deleting or leaving the duplicate
            # behind
            raise Exception('Path %s is not covered by any of the specified prefixes. Aborting.' % str(path))


def compute_duplicates(service, snapshot: Snapshot) -> Tuple[Dict[str, List[Resource]], Dict[Resource, ResourcePath]]:
    by_md5 = defaultdict(list)
    for entry in snapshot.entries:
        if not isinstance(entry, DriveFile):
            continue
        by_md5[entry.md5Checksum].append(entry)

    duplicates = {k: v for k, v in by_md5.items() if len(v) > 1}
    paths = dict(resolve_paths(service, [
        element for elements in duplicates.values() for element in elements
    ]))

    return duplicates, paths


def get_snapshot(service, args):
    return load_snapshot(service, args.snapshot) if args.snapshot else snapshot(service, [args.folder])


def register_parser(subparsers):
    parser = subparsers.add_parser(
        'dedup', help='Hunt down and remove duplicate files from Google Drive.')

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--snapshot', help='causes _dedup_ to look for duplicates inside of a pre-existing snapshot,'
                                          'created with the _snapshot_ command')
    group.add_argument('--folder', help='causes _dedup_ to look for duplicates in an existing folder '
                                        'in Google Drive (e.g. "/Pictures/Old/")')

    subsubparsers = parser.add_subparsers()
    subsubparsers.required = True
    subsubparsers.dest = 'command'

    compute = subsubparsers.add_parser('list', help='lists duplicates, grouped by MD5')
    compute.add_argument('--json', help='Outputs listing in JSON format.', action='store_true')
    compute.set_defaults(func=dedup_list)

    apply = subsubparsers.add_parser('apply', help='removes duplicates')
    apply.add_argument('--prefixes', help='comma-separated list of preferred prefixes, most '
                                          'preferred come first.'
                                          'Duplicates will be dropped from least-preferred '
                                          'prefixes, or prefixes not present in this list. Use '
                                          'the single prefix "/" to delete duplicates at random. If no '
                                          'prefix can be matched, deduplication will abort with an error.',
                       required=True)
    apply.add_argument('--dry-run', help='Prints actions but do not actually change the contents of Google Drive.',
                       action='store_true')
    apply.set_defaults(func=dedup_apply)
