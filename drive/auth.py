from os import path

import apiclient
import httplib2
from oauth2client import client, file as ofile

from drive.misc import eprint

GAPI_ID = 'gapid.json'


def connect():
    flow = _connect_flow(path.join(path.dirname(__file__), GAPI_ID))
    payload, need_auth = next(flow)
    if need_auth:
        eprint('No valid authentication token found. Authorization required. Open the '
               'following URL in your browser: \n' + payload)
        payload, _ = flow.send(input('And enter the Google Drive auth key here:'))

    return payload


def _connect_flow(apisecret):
    flow = _credentials(apisecret)
    payload, need_auth = flow.send(None)

    if need_auth:
        code = yield (payload, True)
        auth, _ = flow.send(code)
    else:
        auth = payload

    http = auth.authorize(httplib2.Http())

    yield (apiclient.discovery.build('drive', 'v3', http=http), False)


def _credentials(apisecret):
    flow = client.flow_from_clientsecrets(
        apisecret,
        scope='https://www.googleapis.com/auth/drive',
        redirect_uri='urn:ietf:wg:oauth:2.0:oob'
    )

    storage = ofile.Storage('.credentials.json')
    exists = path.exists('.credentials.json')
    if exists:
        credentials = storage.get()

    if not exists or credentials.invalid:
        auth_uri = flow.step1_get_authorize_url()
        code = yield (auth_uri, True)
        credentials = flow.step2_exchange(code)
        storage.put(credentials)

    if credentials is None:
        raise ValueError('Failed to obtain access credentials.')

    eprint('Google Drive authentication successful.')

    yield (credentials, False)
