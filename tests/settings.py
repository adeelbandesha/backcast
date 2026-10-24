import os
import tempfile

# Keep the tests' SQLite files out of the real data folder, and run tasks inside the request so a test sees them finish.
os.environ["BACKCAST_DATA_DIR"] = tempfile.mkdtemp(prefix="backcast-tests-")

from backcast.web.settings import *  # noqa: E402, F403

HUEY = {**HUEY, "immediate": True}  # noqa: F405
