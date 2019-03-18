"""
Provides a simplified layer over the Google Drive API which is structured around :class:`DriveFolder`s and
:class:`DriveFile`s. Folders can be retrieved by name and their contents can be listed, and Files can have their
names, types and MD5 hashes inspected (when available). The API also allows creating new folders and uploading new
files.
"""

import inspect
import os
from collections import namedtuple
from typing import List, Dict, Set, Any, Generator, Collection, Tuple

import magic
from googleapiclient.http import MediaFileUpload

from drive.http import multiget_gd
from drive.misc import eprint

#: Type alias for JSON objects coming from the underlying Google Drive API
GAPIJson = Dict[str, Any]

_RESOURCES = lambda: [
    DriveFolder,
    DriveFile,
    Resource
]

_PAGE_SIZE = 1000

detector = magic.Magic(mime=True)

Fields = namedtuple('Args', ['mandatory', 'optional', 'all'])


def _all_fields() -> Set[str]:
    return {field for cls in _RESOURCES() for field in cls.fields().all}


class Resource(object):
    MetaFields = {'self', 'service'}

    def __init__(self, service, id, mimeType, parents=None):
        self.service = service
        self.id = id
        self.mimeType = mimeType
        self.parents = parents if parents is not None else []

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if isinstance(other, Resource):
            return self.id == other.id
        return NotImplemented

    def __str__(self):
        return '%s : %s' % (self.id, self.mimeType)

    def delete(self):
        return self.service.files().delete(fileId=self.id)

    @classmethod
    def from_name(cls, service, name: str) -> List['Resource']:
        files = service.files()
        reply = files.list(q="name='%s'" % name, fields=_field_str(cls.fields().all)).execute()['files']
        if len(reply) == 0:
            raise FileNotFoundError('Resource %s could not be found.' % name)

        return [
            cls.from_item(service, item) for item in reply
        ]

    @classmethod
    def fields(cls) -> Fields:
        arg_spec = inspect.getfullargspec(cls)
        all = set(arg_spec.args) - Resource.MetaFields
        optionals = set(
            arg_spec.args[-len(arg_spec.defaults):]
            if arg_spec.defaults is not None else set()
        )
        return Fields(
            mandatory=all - optionals,
            optional=optionals,
            all=all
        )

    @staticmethod
    def from_item(service, item: GAPIJson) -> 'Resource':
        for cls in _RESOURCES():
            if cls.is_a(item):
                mandatory, optionals, _ = cls.fields()
                # Mandatory arguments must be present.
                actual = {
                    k: item[k] for k in mandatory
                }

                # Optional arguments have a default set for them.
                actual.update({
                    k: item[k] for k in optionals if k in item
                })

                return cls(service, **actual)

        raise Exception('Don\'t know how make resource from %s' % str(item))

    @classmethod
    def is_a(cls, item: GAPIJson) -> bool:
        return all(field in item for field in Resource.fields().mandatory)


class DriveFolder(Resource):
    MimeType = 'application/vnd.google-apps.folder'

    def __init__(self, service, name, id, mimeType, parents=None):
        assert mimeType == DriveFolder.MimeType
        super().__init__(service, id, mimeType, parents)

        self.name = name

    def children_named(self, name: str) -> List[Resource]:
        response = self.service.files().list(q="'%s' in parents and name = '%s'" % (self.id, name)).execute()
        return [
            Resource.from_item(self.service, item)
            for item in response.get('files', [])
        ]

    def list(self, recurse: bool = False) -> Generator[Resource, None, None]:
        return self._list(recurse, set())

    def _list(self, recurse: bool, exclude: Set[Resource]) -> Generator[Resource, None, None]:
        files = self.service.files()
        args = {
            'pageSize': 1000,
            # Need to fetch all fields as we have no idea what we're getting.

            'fields': 'nextPageToken,'  # If you forget this, you'll never get past page 1.
                      + _field_str(_all_fields()),
            'q': "'%s' in parents" % self.id
        }

        eprint('Scanning folder <%s>.' % self.name)

        folders = set()
        request = files.list(**args)
        total = 0
        while True:
            response = request.execute()
            items = response.get('files', [])
            for item in items:
                item = Resource.from_item(self.service, item)
                # We can't recurse immediately as we must first finish consuming
                # the current request.
                if recurse and isinstance(item, DriveFolder):  # use isinstance to please the type checker
                    folders.add(item)

                yield item

            total += len(items)
            eprint('Examined %d entries in folder <%s>.' % (total, self.name))
            page_token = response.get('nextPageToken')
            if page_token is None:
                break

            request = files.list(pageToken=page_token, **args)

        eprint('Done scanning <%s>.' % self.name)

        # Recurses.
        folders = folders - exclude
        exclude.update(folders)
        for folder in folders:
            eprint('Recursing into subfolder <%s/%s>' % (self.name, folder.name))
            yield from folder._list(recurse, exclude)

    def __str__(self):
        return 'Folder "%s" (%s)' % (self.name, self.id)

    def create_file(self, local_file_path):
        # Defers creating upload requests to execution time as the Python toolkit will load the upload contents
        # in memory. If we're uploading thousands of pics, this will lead to massive memory usage.
        return _DeferredRequest(lambda: self._create_file(local_file_path=local_file_path).execute())

    def _create_file(self, local_file_path):
        if not os.path.isfile(local_file_path):
            raise Exception('<%s> is not a file.' % local_file_path)

        media = MediaFileUpload(
            local_file_path,
            mimetype=detector.from_file(local_file_path)
        )

        meta = {
            'name': os.path.basename(local_file_path),
            'parents': [self.id]
        }

        return self.service.files().create(
            body=meta,
            media_body=media,
            fields='id'
        )

    @classmethod
    def is_a(cls, item):
        return item['mimeType'] == DriveFolder.MimeType

    @staticmethod
    def root(service) -> 'DriveFolder':
        return DriveFolder.from_item(service, service.files().get(fileId='root').execute())


class _DeferredRequest(object):
    def __init__(self, execute):
        self.execute = execute


class DriveFile(Resource):
    def __init__(self, service, name, id, mimeType, parents=None, md5Checksum=None):
        super().__init__(service, id, mimeType, parents)
        self.name = name
        # Root folders have no parent
        self.parents = [] if parents is None else parents
        self.md5Checksum = md5Checksum

    def __str__(self):
        return 'File "%s" (%s, %s, %s)' % (self.name, self.mimeType, self.md5Checksum, self.id)

    @classmethod
    def is_a(cls, item):
        return 'md5Checksum' in item


class Snapshot(object):
    def __init__(self, entries=None):
        self.entries = [] if entries is None else entries

    def service(self, reference) -> None:
        for entry in self.entries:
            entry.service = reference

    def merge(self, other: 'Snapshot') -> 'Snapshot':
        return Snapshot(self.entries + other.entries)


class ResourcePath(object):
    def __init__(self, resource: Resource, path: List[DriveFolder]):
        self.resource = resource
        self.path = path

    def __str__(self):
        return '/' + '/'.join(reversed(
            [self.resource.name] +
            [element.name for element in self.path]
        ))

    def startswith(self, path: str):
        return self.__str__().startswith(path)

    @classmethod
    def from_name(cls, service, path: str) -> List[Resource]:
        elements = [element for element in path.split('/') if element]
        resource = [DriveFolder.root(service)]
        for i, element in enumerate(elements, start=1):
            # Intermediate results must be unique as we won't
            # branch into arbitrary paths.
            resource = unique(resource, path)
            if not isinstance(resource, DriveFolder):
                raise Exception('%s is not a Google Drive folder.' % element)
            resource = resource.children_named(element)

        return resource


def unique(resources: List[Any], alias: str, allow_none=False, allow_multiple=False) -> Any:
    if len(resources) == 0 and (not allow_none):
        raise Exception('Resource %s not found.' % alias)
    elif len(resources) > 1 and (not allow_multiple):
        raise Exception('Ambiguous resource alias %s.' % alias)
    else:
        return resources[0]


def resolve_paths(service, leaves: List[Resource]) -> List[Tuple[Resource, ResourcePath]]:
    resolved = {
        leaf.id: leaf for leaf in leaves
    }

    while True:
        parents = {
            parent for leaf in resolved.values() for parent in leaf.parents
        }

        unresolved = parents - resolved.keys()

        if not unresolved:
            break

        resolved.update({
            resource.id: resource for resource in get_resources(service, unresolved)
        })

    return [
        (leaf, ResourcePath(leaf, _resource_path(leaf, resolved))) for leaf in leaves
    ]


def _resource_path(resource, table):
    if not resource.parents:
        return []

    # I may support this eventually if needed, but it's painful: the notion of a filesystem
    # path does not map very well into a DAG.
    if len(resource.parents) > 1:
        raise Exception('Resources with multiple parents are not supported.')

    parent = table[resource.parents[0]]
    path = [parent]
    path.extend(_resource_path(parent, table))
    return path


def _field_str(fields):
    return 'files(%s)' % (','.join(fields))


def get_resources(service, ids: Collection[str]) -> List[Resource]:
    for gapi_resource in multiget_gd(service, ids, _all_fields()):
        yield Resource.from_item(service, gapi_resource)
