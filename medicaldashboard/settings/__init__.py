from .base import *

if ENVIRONMENT == "development":
    from .development import *
    print("ENVIRONMENT: Development")
else:
    from .production import *
    print("ENVIRONMENT: Production")
