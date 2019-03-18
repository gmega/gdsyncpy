from argparse import ArgumentParser

from commands import dedup, snapshot
from commands.sync import sync
from drive.auth import connect

COMMANDS = [snapshot, dedup, sync]


def main():
    # Monkeypatches httplib2 to avoid issues with connection polling and oauth2client
    # (https://github.com/googleapis/google-api-python-client/issues/218)
    import httplib2shim
    httplib2shim.patch()

    parser = ArgumentParser()
    subparsers = parser.add_subparsers()
    subparsers.required = True
    subparsers.dest = 'command'
    for command in COMMANDS:
        command.register_parser(subparsers)

    args = parser.parse_args()
    actual = {
        'service': connect(),
        'args': args
    }
    if 'api_key' in args:
        actual['service'] = connect(args.api_key)

    args.func(**actual)


if __name__ == '__main__':
    main()
