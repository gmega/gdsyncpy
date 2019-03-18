from argparse import Namespace
from copy import copy

from marshmallow import Schema, fields, pre_dump, post_load

from drive.serializers import SnapshotSchema


class LocalFileSchema(Schema):
    class Meta:
        strict = True

    path = fields.Str(required=True)
    mime_type = fields.Str(required=True)
    md5_checksum = fields.Str(required=True)

    @post_load
    def make_local_file(self, data):
        # Marshmallow makes it impossible to refer to the schema
        # from the module that contains the domain objects without
        # creating a circular import.
        from commands.sync.state import LocalFile

        return LocalFile(**data)


class SyncStateSchema(Schema):
    class Meta:
        strict = True

    path = fields.Str(required=True)
    name = fields.Str(required=True)
    args = fields.Dict(required=True)
    snapshot = fields.Nested(SnapshotSchema, required=True)
    local_files = fields.List(fields.Nested(LocalFileSchema), required=True)
    timestamp = fields.Float(required=True)
    stored = fields.Bool(required=True)

    @post_load
    def make_sync_state(self, data):
        # Ugh.
        from commands.sync.state import SyncState

        data['args'] = Namespace(**data['args'])
        return SyncState(**data)

    @pre_dump
    def serialize_args(self, state):
        state_copy = copy(state)
        # We can't use a marshmallow serializer for Namespace as it has
        # no schema, and its contents are stored in __dict__. So we do it here.
        args_as_dict = state_copy.args.__dict__
        args_as_dict.pop('func')
        state_copy.args = args_as_dict
        return state_copy
