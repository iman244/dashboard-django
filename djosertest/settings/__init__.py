import os

from .base import *

print(DEBUG)

if DEBUG == True:
    from .development import *
else:
    from .production import *
