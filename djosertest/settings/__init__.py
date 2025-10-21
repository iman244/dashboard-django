from .base import *

if ENVIRONMENT == "development":
    from .development import *
    print("Development environment")
else:
    from .production import *
    print("Production environment")
