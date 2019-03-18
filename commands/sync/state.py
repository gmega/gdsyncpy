import json
import os
import random
import re
import time
from argparse import Namespace
from pathlib import Path
from typing import List

from commands.sync.serializers import SyncStateSchema
from drive.misc import eprint


class LocalFile(object):
    def __init__(self, path, mime_type, md5_checksum):
        self.path = path
        self.mime_type = mime_type
        self.md5_checksum = md5_checksum


class SyncState(object):
    """SyncState is a hacky way of storing the state of a sync without having to fully materialize
    local and Drive file systems as objects. We essentially store snapshots of remote folders and
     the metadata of local files, together with the command line arguments to allow easily and quickly
     resuming a failed sync.
    """

    NAME_REGEX = re.compile(r'[0-9]+-[0-9]+\.sync')

    def __init__(self, snapshot, args: Namespace, local_files: List[LocalFile], path: str, name: str = None,
                 timestamp: int = None, stored=False):
        self.name = SyncState.new_name() if name is None else name
        self.path = path
        self.args = args
        self.snapshot = snapshot
        self.local_files = local_files
        self.timestamp = time.time() if timestamp is None else timestamp
        self.stored = stored

    def store(self):
        eprint('Saving sync state at %s. Use _resume_ to resume sync in case of failures.' % self.full_path)
        try:
            self.stored = True
            Path(self.full_path).write_text(
                json.dumps(SyncStateSchema().dump(self).data),
                encoding='utf-8'
            )
        except Exception:
            self.stored = False
            raise

    def clear(self):
        eprint('Removing sync state %s' % self.full_path)
        os.remove(self.full_path)

    @property
    def full_path(self):
        return os.path.join(self.path, self.name)

    @staticmethod
    def sync_states(path: str, service) -> List['SyncState']:
        for content in os.listdir(path):
            match = SyncState.NAME_REGEX.fullmatch(os.path.basename(content))
            if not match:
                continue
            yield SyncState.read(os.path.join(path, content), service)

    @staticmethod
    def new_name() -> str:
        return '%d-%d.sync' % (time.monotonic(), random.randint(0, 100000))

    @staticmethod
    def read(path, service) -> 'SyncState':
        eprint('Reading sync state from %s.' % path)
        sss = SyncStateSchema()
        sss.context = {'service': service}
        return sss.load(
            json.loads(Path(path).read_text(encoding='utf-8'))
        ).data
