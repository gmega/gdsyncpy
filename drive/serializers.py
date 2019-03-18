import json
from typing import Dict, Any

from marshmallow import Schema, fields, post_load
from marshmallow_oneofschema import OneOfSchema

from drive.api import DriveFolder, DriveFile, Resource, Snapshot


class ResourceSchema(Schema):
    class Meta:
        strict = True

    id = fields.Str(required=True)
    mimeType = fields.Str(required=True)
    parents = fields.List(fields.Str())

    @post_load
    def make_resource(self, data):
        return Resource(service=None, **data)


class DriveFolderSchema(Schema):
    class Meta:
        strict = True

    id = fields.Str(required=True)
    mimeType = fields.Str(required=True)
    parents = fields.List(fields.Str())
    name = fields.Str(required=True)

    @post_load
    def make_folder(self, data):
        return DriveFolder(service=None, **data)


class DriveFileSchema(Schema):
    class Meta:
        strict = True

    id = fields.Str(required=True)
    mimeType = fields.Str(required=True)
    parents = fields.List(fields.Str())
    name = fields.Str(required=True)
    md5Checksum = fields.Str()

    @post_load
    def make_file(self, data):
        return DriveFile(service=None, **data)


class PolyResourceSchema(OneOfSchema):
    class Meta:
        strict = True

    type_schemas = {
        'folder': DriveFolderSchema,
        'file': DriveFileSchema,
        'resource': ResourceSchema
    }

    def get_obj_type(self, obj):
        if isinstance(obj, DriveFolder):
            return 'folder'
        elif isinstance(obj, DriveFile):
            return 'file'
        elif isinstance(obj, Resource):
            return 'resource'
        raise Exception('Unknown object type %s' % obj.__class__.name)

    @post_load
    def make_snapshot(self, data):
        return Snapshot(**data)


class SnapshotSchema(Schema):
    class Meta:
        strict = True

    entries = fields.List(fields.Nested(PolyResourceSchema))

    @post_load
    def make_snapshot(self, data):
        snapshot = Snapshot(**data)
        for entry in snapshot.entries:
            entry.service = self.context['service']
        return snapshot


def store_snapshot(snapshot: Snapshot, snapshot_file: str) -> None:
    with open(snapshot_file, 'w') as outfile:
        json.dump(serialize_snapshot(snapshot), outfile, indent=3)


def serialize_snapshot(snapshot: Snapshot) -> Dict[str, Any]:
    return SnapshotSchema().dump(snapshot).data


def load_snapshot(service, snapshot_file: str) -> Snapshot:
    with open(snapshot_file, 'r') as infile:
        return deserialize_snapshot(service, json.load(infile))


def deserialize_snapshot(service, snapshot: Dict[str, Any]) -> Snapshot:
    ss = SnapshotSchema()
    ss.context = {'service': service}
    return ss.load(snapshot).data

