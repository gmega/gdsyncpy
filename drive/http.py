"""
Low-level utilities for making HTTP requests which add batching and error policies.
"""

import copy
import json
import time
from abc import ABC, abstractmethod
from collections import namedtuple
from itertools import islice
from typing import Collection, List, Dict, Tuple, Union, Set, Type, Generator

from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest

from drive.misc import eprint

_Request = namedtuple('_Request', ['request', 'id'])

RequestResult = namedtuple('_RequestResult', ['id', 'response', 'error'])


class RequestRunner(ABC):

    def __init__(self, service):
        self.service = service
        self.requests = []

    def add(self, request: HttpRequest, request_id: str = None) -> None:
        self.requests.append(_Request(request=request, id=request_id))

    @abstractmethod
    def execute(self) -> Generator[RequestResult, None, None]:
        pass


class SequentialRequestRunner(RequestRunner):

    def execute(self) -> Generator[RequestResult, None, None]:
        for request, request_id in self.requests:
            response = error = None
            try:
                response = request.execute()
            except HttpError as ex:
                error = ex
            yield RequestResult(
                id=request_id,
                response=response,
                error=error
            )


class GAPIBatchRunner(RequestRunner):
    GAPI_BATCH_LIMIT = 100

    def execute(self) -> Generator[RequestResult, None, None]:
        cursor = (x for x in self.requests)
        for batch in iter(lambda: list(islice(cursor, self.GAPI_BATCH_LIMIT)), []):
            yield from self._run_batch(batch)

    def _run_batch(self, requests) -> Generator[RequestResult, None, None]:
        results = []

        def callback(*elements):
            results.append(elements)

        batch_runner = self.service.new_batch_http_request()
        for request, request_id in requests:
            batch_runner.add(request, request_id=request_id, callback=callback)

        batch_runner.execute()

        for request_id, response, error in results:
            yield RequestResult(
                id=request_id,
                response=response,
                error=error
            )


class HttpErrorPolicy(object):
    """
    :class:`HttpErrorPolicy` specifies what should be done in response to a Drive API error condition. Error
    conditions are specified as a combination of an HTTP code (e.g. 404) and a `"reason" string
    <https://developers.google.com/drive/api/v3/handle-errors>`_.

    The three possible actions in response to an error condition are:

    * :class:`HttpErrorPolicy.RETRY` will cause the request to be _retried_ upon encountering this error condition.
      Retry specifics are documented in :func:`run_requests`, but they typically involve a period of backoff and a
      maximum number of attempts.

    * :class:`HttpErrorPolicy.SKIP` will skip the current request upon encountering this error condition.

    * :class:`HttpErrorPolicy.FAIL` will cause :func:`run_requests` to abort with an error upon encountering
      this error condition.

    """

    #: Retries requests that match this error condition.
    RETRY = 0

    #: Skips requests that match this error condition.
    SKIP = 1

    #: Fails if a request matches this error condition.
    FAIL = 2

    def __init__(self,
                 codes: Union[str, List[str]],
                 warning: str,
                 print_always: bool,
                 action: int,
                 reasons: Set[str] = None):
        self.codes = codes if isinstance(codes, list) else [codes]
        self.reasons = reasons
        self.print_always = print_always
        self.action = action
        self.warning = warning

    def print_warning(self, rid):
        eprint(self.warning.format(rid=rid))

    def matches(self, error: HttpError) -> bool:
        return self.matches_status(error) and self.matches_reason(error)

    def matches_status(self, error: HttpError) -> bool:
        return error.resp['status'] in self.codes

    def matches_reason(self, error: HttpError) -> bool:
        return True if self.reasons is None else (HttpErrorPolicy.reason(error) in self.reasons)

    @staticmethod
    def reason(error: HttpError):
        errors = json.loads(error.content.decode('utf-8'))
        if not isinstance(errors, dict):
            raise Exception('Cannot handle Drive API exception: %s' % str(error))

        error_list = errors.get('error', {}).get('errors', [])
        if len(error_list) == 0:
            return None

        return error_list[0].get('reason', None)


class HttpErrorTable(object):
    def __init__(self, *policies: HttpErrorPolicy):
        self.policies = policies

    def matching(self, error: HttpError) -> Tuple[HttpErrorPolicy, str]:
        policies = [policy for policy in self.policies if policy.matches(error)]
        return None if len(policies) == 0 else policies[0], error.resp['status']

    def policy_for(self, code):
        for policy in self.policies:
            if code in policy.codes:
                return policy


DEFAULT_POLICIES = HttpErrorTable(
    # http://www.itmaybeahack.com/homepage/iblog/architecture/C551260341/E20081031204203/index.html
    HttpErrorPolicy('104',
                    warning='Connection reset by peer while processing {rid}.',
                    print_always=True,
                    action=HttpErrorPolicy.RETRY),
    HttpErrorPolicy('404',
                    warning='Request {rid} resulted in a 404 (not found) and has been skipped. Is your snapshot stale?',
                    print_always=True,
                    action=HttpErrorPolicy.SKIP),
    HttpErrorPolicy(['403', '429'],
                    reasons={'rateLimitExceeded', 'userRateLimitExceeded'},
                    warning='Request {rid} resulted in a 4XX. Slowing down.',
                    print_always=False,
                    action=HttpErrorPolicy.RETRY),
    HttpErrorPolicy('500',
                    warning='The API returned a server error (500) for {rid}. Retrying.',
                    print_always=False,
                    action=HttpErrorPolicy.RETRY)
)


class ErrorHandlingRunner(RequestRunner):
    requests: List[_Request]

    def __init__(self,
                 service,
                 timeout: float = float('Inf'),
                 min_backoff: int = 5,
                 max_retries: int = 10,
                 recovery_policy: HttpErrorTable = DEFAULT_POLICIES,
                 delegate: Type[RequestRunner] = SequentialRequestRunner):
        super().__init__(service)

        self.min_backoff = min_backoff
        self.max_retries = max_retries
        self.timeout = timeout
        self.policies = recovery_policy
        self.delegate = delegate

    def execute(self) -> Generator[RequestResult, None, None]:
        pending = {str(request.id): request for request in self.requests}
        warnings = set()
        attempts = 1
        backoff = self.min_backoff

        start = time.monotonic()
        while ((time.monotonic() - start) < self.timeout) and (attempts <= self.max_retries):
            for result in self._run_requests(pending.values()):
                if result.error is None:
                    # No errors with the current request, just return the element.
                    del pending[result.id]
                    yield result
                    # Resets the retry and backoff counters as at least one request got through.
                    attempts = 1
                    backoff = self.min_backoff
                    continue

                # Got an error. Let's see what policies we have for that.
                policy, error_code = self.policies.matching(result.error)

                # Case 1: Don't know how to handle this. Just bubble it up.
                if policy is None:
                    raise result.error

                # Prints error-specific warning.
                if policy.print_always or (error_code not in warnings):
                    policy.print_warning(rid=result.id)
                    warnings.add(error_code)

                # Case 2: Should skip the current entry as if it were already satisfied.
                if policy.action == HttpErrorPolicy.SKIP:
                    del pending[result.id]
                    continue

                # Case 3: Should retry after we're done.
                elif policy.action == HttpErrorPolicy.RETRY:
                    pass

                # Case 4: After having printed the warning message, we should fail after.
                elif policy.action == HttpErrorPolicy.FAIL:
                    raise result.error

                # Case 5: Should never happen, so we throw an error if it does.
                else:
                    raise Exception('Don\'t know how to handle action %d.' % policy.action)

            # All requests satisfied: we're done.
            if not pending:
                return

            # If there are still pending requests left, this means we have to retry them. Attempts to back off.
            eprint('Some of the requests could not be fulfilled. Backing off and retrying.')

            # Backs off.
            time.sleep(backoff)
            backoff = backoff * 2
            attempts += 1

        raise Exception(
            'Could not process request(s). %s' % (
                'Too many retry attempts.' if attempts > self.max_retries else 'Operation timed out.'
            )
        )

    def _run_requests(self, requests: Collection[_Request]):
        runner: RequestRunner = self.delegate(self.service)
        for request in requests:
            runner.add(request=request.request, request_id=request.id)
        yield from runner.execute()


def multiget_gd(service, ids: Collection[str], fields: Collection[str]) -> List[Dict[str, str]]:
    """
    Fetches information for a set of Google Drive files.

    :param service:
        a Google Drive API object as returned by :py:func:`connect`.

    :param ids:
        the list of file IDs to fetch information for.

    :param fields:
        fields to gather.

    :return: a :py:class:`List` containing objects with format:

    .. code-block:: json

        {
            'id': 'fileId',
            'field1': value1,
            'field2': value2,
            ...
        }

    """
    files = service.files()
    ids = (x for x in ids)

    fail_on_404 = copy.deepcopy(DEFAULT_POLICIES)
    fail_on_404.policy_for('404').action = HttpErrorPolicy.FAIL

    runner = ErrorHandlingRunner(service, recovery_policy=fail_on_404, delegate=GAPIBatchRunner)
    for fid in ids:
        runner.add(files.get(fileId=fid, fields=','.join(fields)), fid)

    for response in runner.execute():
        yield response.response
