import sys
from functools import partial

eprint = partial(print, file=sys.stderr)