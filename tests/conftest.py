import os
import tempfile

# Tests always run offline: mock PayPal sandbox, rules engine for the app-level flow.
os.environ["PAYPAL_MODE"] = "mock"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()
